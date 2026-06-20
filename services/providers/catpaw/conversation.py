from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


BOOTSTRAP_TTL_SECONDS = int(os.environ.get("CATPAW_CONVERSATION_BOOTSTRAP_TTL_SECONDS") or 600)
ACTIVE_TTL_SECONDS = int(os.environ.get("CATPAW_CONVERSATION_ACTIVE_TTL_SECONDS") or 7200)
MAX_ENTRIES = int(os.environ.get("CATPAW_CONVERSATION_CACHE_MAX_ENTRIES") or 512)


_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class _Entry:
    conversation_id: str
    expires_at: float
    updated_at: float


class CatpawConversationCache:
    def __init__(
        self,
        bootstrap_ttl_seconds: int = BOOTSTRAP_TTL_SECONDS,
        active_ttl_seconds: int = ACTIVE_TTL_SECONDS,
        max_entries: int = MAX_ENTRIES,
    ) -> None:
        self.bootstrap_ttl_seconds = max(1, int(bootstrap_ttl_seconds))
        self.active_ttl_seconds = max(1, int(active_ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._bootstrap_by_root: OrderedDict[str, _Entry] = OrderedDict()
        self._active_by_branch: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._bootstrap_by_root.clear()
            self._active_by_branch.clear()

    def conversation_id(self, root_key: str, branch_key: str, has_history: bool, now: float | None = None) -> str:
        now = time.time() if now is None else now
        with self._lock:
            self._prune(now)
            if not has_history:
                conversation_id = str(uuid.uuid4())
                self._bootstrap_by_root[root_key] = _Entry(
                    conversation_id=conversation_id,
                    expires_at=now + self.bootstrap_ttl_seconds,
                    updated_at=now,
                )
                self._bootstrap_by_root.move_to_end(root_key)
                self._enforce_limits()
                return conversation_id

            active = self._active_by_branch.get(branch_key)
            if active and active.expires_at > now:
                active.expires_at = now + self.active_ttl_seconds
                active.updated_at = now
                self._active_by_branch.move_to_end(branch_key)
                return active.conversation_id

            bootstrap = self._bootstrap_by_root.get(root_key)
            if bootstrap and bootstrap.expires_at > now:
                conversation_id = bootstrap.conversation_id
            else:
                conversation_id = str(uuid.uuid4())

            self._active_by_branch[branch_key] = _Entry(
                conversation_id=conversation_id,
                expires_at=now + self.active_ttl_seconds,
                updated_at=now,
            )
            self._active_by_branch.move_to_end(branch_key)
            self._enforce_limits()
            return conversation_id

    def _prune(self, now: float) -> None:
        for store in (self._bootstrap_by_root, self._active_by_branch):
            expired = [key for key, entry in store.items() if entry.expires_at <= now]
            for key in expired:
                store.pop(key, None)

    def _enforce_limits(self) -> None:
        while len(self._bootstrap_by_root) > self.max_entries:
            self._bootstrap_by_root.popitem(last=False)
        while len(self._active_by_branch) > self.max_entries:
            self._active_by_branch.popitem(last=False)


_cache = CatpawConversationCache()


def reset_cache_for_tests() -> None:
    _cache.reset()


def conversation_id_for_anthropic_request(
    messages: list[dict[str, Any]],
    model: str,
    tools: Any = None,
    session_key: str = "",
) -> str:
    root_key = _root_key(messages, model, tools, session_key=session_key)
    branch_key = _branch_key(messages, root_key)
    return _cache.conversation_id(root_key, branch_key, _has_history(messages))


def _root_key(messages: list[dict[str, Any]], model: str, tools: Any = None, session_key: str = "") -> str:
    system_text = "\n".join(_message_text(message) for message in messages if message.get("role") == "system")
    first_user = _first_user_text(messages)
    payload = {
        "version": 1,
        "model": str(model or ""),
        "session": _digest(str(session_key or "")),
        "system": _digest(system_text),
        "tools": _digest(_stable_json(_tool_fingerprint(tools))),
        "root_user": _digest(first_user),
    }
    return "root:" + _digest(_stable_json(payload))


def _branch_key(messages: list[dict[str, Any]], root_key: str) -> str:
    anchor = _first_history_anchor(messages)
    if not anchor:
        return "branch:" + root_key
    return "branch:" + _digest(_stable_json({"root": root_key, "anchor": _digest(anchor)}))


def _has_history(messages: list[dict[str, Any]]) -> bool:
    saw_user = False
    for message in messages:
        role = str(message.get("role") or "")
        if role == "system":
            continue
        if role == "user":
            if saw_user:
                return True
            saw_user = True
            continue
        if saw_user:
            return True
    return False


def _first_history_anchor(messages: list[dict[str, Any]]) -> str:
    saw_user = False
    for message in messages:
        role = str(message.get("role") or "")
        if role == "system":
            continue
        if role == "user" and not saw_user:
            saw_user = True
            continue
        if saw_user:
            text = _message_text(message)
            if text:
                return f"{role}:{text}"
    return ""


def _first_user_text(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            return _message_text(message)
    return ""


def _message_text(message: dict[str, Any]) -> str:
    return _content_text(message.get("content"))


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return _normalize_text(content)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in {"text", "input_text", "output_text"}:
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "tool_result":
                    parts.append(_content_text(item.get("content")))
                elif item.get("type") == "tool_use":
                    parts.append(_stable_json({
                        "name": item.get("name"),
                        "input": item.get("input"),
                    }))
        return _normalize_text("\n".join(part for part in parts if part))
    if content is None:
        return ""
    return _normalize_text(str(content))


def _tool_fingerprint(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    result: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        if not name:
            name = str(function.get("name") or "").strip()
        schema = tool.get("input_schema") or tool.get("parameters") or function.get("input_schema") or function.get("parameters") or {}
        result.append({"name": name, "schema": schema})
    return sorted(result, key=lambda item: item.get("name") or "")


def _normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text or "").strip()[:4096]


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
