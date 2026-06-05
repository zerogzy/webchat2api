from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from services.config import config

CACHE_KEY_EXCLUDED_BODY_KEYS = {
    "messages",
    "stream",
}


@dataclass
class CacheEntry:
    expires_at: float
    value: dict[str, Any]


@dataclass
class InflightCall:
    condition: threading.Condition = field(default_factory=lambda: threading.Condition(threading.RLock()))
    done: bool = False
    value: dict[str, Any] | None = None
    error: BaseException | None = None


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes_sha256__": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if isinstance(value, bytearray):
        data = bytes(value)
        return {"__bytes_sha256__": hashlib.sha256(data).hexdigest(), "length": len(data)}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _message_signature(message: dict[str, Any]) -> str:
    return json.dumps(_json_safe(message), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_text_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = config.get_chat_completion_message_normalization_settings()
    if not settings.get("enabled") or not settings.get("drop_adjacent_duplicates"):
        return messages

    normalized: list[dict[str, Any]] = []
    previous_signature = ""
    for message in messages:
        if "tool_calls" in message or "tool_call_id" in message:
            normalized.append(message)
            previous_signature = _message_signature(message)
            continue
        signature = _message_signature(message)
        if signature == previous_signature:
            continue
        normalized.append(message)
        previous_signature = signature
    return normalized


def cache_key(body: dict[str, Any], messages: list[dict[str, Any]], *, stream: bool) -> str:
    payload = {key: value for key, value in body.items() if key not in CACHE_KEY_EXCLUDED_BODY_KEYS}
    payload["messages"] = messages
    payload["stream"] = bool(stream)
    encoded = json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _setting_float(settings: dict[str, object], key: str, default: float = 0.0) -> float:
    value = settings.get(key, default)
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _setting_int(settings: dict[str, object], key: str, default: int = 0) -> int:
    value = settings.get(key, default)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _is_deterministic_text_request(body: dict[str, Any]) -> bool:
    temperature = body.get("temperature")
    if temperature is None:
        return False
    try:
        if float(temperature) != 0.0:
            return False
    except (TypeError, ValueError):
        return False
    top_p = body.get("top_p")
    if top_p is not None:
        try:
            if float(top_p) != 1.0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def is_cacheable_text_request(body: dict[str, Any], *, stream: bool) -> bool:
    settings = config.get_chat_completion_cache_settings()
    if not settings.get("enabled") or _setting_float(settings, "ttl_seconds") <= 0:
        return False
    if not _is_deterministic_text_request(body):
        return False
    if stream and not settings.get("cache_stream"):
        return False
    if not settings.get("cache_tool_calls") and (body.get("tools") or body.get("tool_choice") or body.get("functions") or body.get("function_call")):
        return False
    return True


class ChatCompletionCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, CacheEntry] = {}
        self._inflight: dict[str, InflightCall] = {}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._inflight.clear()

    @staticmethod
    def _copy(value: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(value)

    def _prune_locked(self, now: float, max_entries: int) -> None:
        expired_keys = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired_keys:
            self._entries.pop(key, None)
        while len(self._entries) > max_entries:
            oldest_key = min(self._entries, key=lambda key: self._entries[key].expires_at)
            self._entries.pop(oldest_key, None)

    def get_or_compute(self, key: str, compute: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        settings = config.get_chat_completion_cache_settings()
        ttl_seconds = _setting_float(settings, "ttl_seconds")
        if not settings.get("enabled") or ttl_seconds <= 0:
            return compute()

        now = time.time()
        max_entries = max(1, _setting_int(settings, "max_entries", 1))
        with self._lock:
            self._prune_locked(now, max_entries)
            entry = self._entries.get(key)
            if entry and entry.expires_at > now:
                return self._copy(entry.value)
            inflight = self._inflight.get(key) if settings.get("dedupe_inflight") else None
            if inflight is None:
                inflight = InflightCall()
                if settings.get("dedupe_inflight"):
                    self._inflight[key] = inflight
                owner = True
            else:
                owner = False

        if not owner:
            with inflight.condition:
                while not inflight.done:
                    inflight.condition.wait()
                if inflight.error:
                    raise inflight.error
                return self._copy(inflight.value or {})

        try:
            value = compute()
        except BaseException as exc:
            with self._lock:
                self._inflight.pop(key, None)
            with inflight.condition:
                inflight.error = exc
                inflight.done = True
                inflight.condition.notify_all()
            raise

        cached_value = self._copy(value)
        with self._lock:
            self._entries[key] = CacheEntry(expires_at=time.time() + ttl_seconds, value=cached_value)
            self._prune_locked(time.time(), max_entries)
            self._inflight.pop(key, None)
        with inflight.condition:
            inflight.value = self._copy(value)
            inflight.done = True
            inflight.condition.notify_all()
        return value


chat_completion_cache = ChatCompletionCache()
