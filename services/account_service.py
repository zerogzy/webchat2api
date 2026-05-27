from __future__ import annotations

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from threading import Condition, Lock
from typing import Any, Callable

from services.config import config
from services.log_service import (
    LOG_TYPE_ACCOUNT,
    log_service,
)
from services.models import GPT_PROVIDER, GROK_PROVIDER, ModelSpec, normalize_provider
from services.storage.base import StorageBackend
from utils.helper import anonymize_token


EXPORT_TIMEZONE = timezone(timedelta(hours=8))


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        data = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _format_timestamp(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(EXPORT_TIMEZONE).isoformat(timespec="seconds")


def _nested_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


GROK_TIER_ALIASES = {
    "free": "basic",
    "basic": "basic",
    "premium": "super",
    "super": "super",
    "heavy": "heavy",
}
GROK_UNAVAILABLE_STATUSES = {"禁用", "异常", "限流", "disabled", "abnormal", "limited"}
GROK_CONSOLE_QUOTA_TOTAL = 30
GROK_CONSOLE_QUOTA_WINDOW_SECONDS = 900


def _normalize_grok_tier(value: Any) -> str:
    return GROK_TIER_ALIASES.get(str(value or "").strip().lower().replace("_", "-"), "")


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    return [item for item in (_clean_string(raw).lower() for raw in raw_items) if item]


def _normalize_grok_access_token(value: Any) -> str:
    token = _clean_string(value)
    simple_sso = re.fullmatch(r"sso\s*=\s*(.+)", token, flags=re.IGNORECASE)
    if simple_sso and ";" not in token:
        return simple_sso.group(1).strip()
    return token


def _grok_tier_matches(account_tier: str, requested_tier: str) -> bool:
    if not account_tier or not requested_tier:
        return False
    if requested_tier == "heavy":
        return account_tier == "heavy"
    if requested_tier == "super":
        return account_tier in {"super", "heavy"}
    if requested_tier == "basic":
        return account_tier in {"basic", "super", "heavy"}
    return False


def _grok_requested_tiers(spec: ModelSpec) -> list[str]:
    if spec.prefer_best:
        return ["heavy", "super", "basic"]
    requested = _normalize_grok_tier(spec.model_tier)
    return [requested] if requested else []


def _grok_account_has_capability(account: dict, spec: ModelSpec) -> bool:
    capabilities = set(account.get("capabilities") or [])
    if not capabilities:
        return True
    requested = {str(spec.capability or "chat").lower()}
    if spec.mode_id:
        requested.add(str(spec.mode_id).lower())
    if spec.model_tier:
        normalized_tier = _normalize_grok_tier(spec.model_tier)
        requested.add(normalized_tier or str(spec.model_tier).lower())
    return bool(capabilities & requested)


class AccountService:
    """账号池服务，使用 token -> account 的 dict 保存账号。"""

    def __init__(self, storage_backend: StorageBackend, now: Callable[[], float] | None = None):
        self.storage = storage_backend
        self._lock = Lock()
        self._image_slot_condition = Condition(self._lock)
        self._index = 0
        self._now = now or (lambda: datetime.now(timezone.utc).timestamp())
        self._accounts = self._load_accounts()
        self._image_inflight: dict[str, int] = {}

    def _load_accounts(self) -> dict[str, dict]:
        accounts = self.storage.load_accounts()
        return {
            normalized["access_token"]: normalized
            for item in accounts
            if (normalized := self._normalize_account(item)) is not None
        }

    def _save_accounts(self) -> None:
        self.storage.save_accounts(list(self._accounts.values()))

    @staticmethod
    def _is_gpt_account(account: dict) -> bool:
        return normalize_provider(account.get("provider")) == GPT_PROVIDER

    @staticmethod
    def _is_image_account_available(account: dict) -> bool:
        if not isinstance(account, dict):
            return False
        if not AccountService._is_gpt_account(account):
            return False
        if account.get("status") in {"禁用", "限流", "异常"}:
            return False
        if bool(account.get("image_quota_unknown")):
            return True
        return int(account.get("quota") or 0) > 0

    @staticmethod
    def _normalize_console_quota(value: Any) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        total = raw.get("total")
        try:
            total_value = int(total if total is not None else GROK_CONSOLE_QUOTA_TOTAL)
        except (TypeError, ValueError):
            total_value = GROK_CONSOLE_QUOTA_TOTAL
        total_value = max(0, total_value)

        window_seconds = raw.get("window_seconds")
        try:
            window_value = int(window_seconds if window_seconds is not None else GROK_CONSOLE_QUOTA_WINDOW_SECONDS)
        except (TypeError, ValueError):
            window_value = GROK_CONSOLE_QUOTA_WINDOW_SECONDS
        window_value = max(0, window_value)

        remaining = raw.get("remaining")
        try:
            remaining_value = int(remaining if remaining is not None else total_value)
        except (TypeError, ValueError):
            remaining_value = total_value
        remaining_value = min(total_value, max(0, remaining_value))

        reset_at = raw.get("reset_at")
        try:
            reset_at_value = int(reset_at) if reset_at is not None else None
        except (TypeError, ValueError):
            reset_at_value = None

        return {
            "remaining": remaining_value,
            "total": total_value,
            "window_seconds": window_value,
            "reset_at": reset_at_value,
        }

    def _reset_console_quota_if_ready(self, account: dict, now: float | None = None) -> dict:
        next_account = dict(account)
        quota = self._normalize_console_quota(next_account.get("quota_console"))
        reset_at = quota.get("reset_at")
        current_time = self._now() if now is None else now
        if reset_at is not None and int(reset_at) <= int(current_time):
            quota["remaining"] = quota["total"]
            quota["reset_at"] = None
        next_account["quota_console"] = quota
        return next_account

    def _is_console_account_available(self, account: dict, now: float | None = None) -> bool:
        if not isinstance(account, dict):
            return False
        if normalize_provider(account.get("provider")) != GROK_PROVIDER:
            return False
        if account.get("status") in GROK_UNAVAILABLE_STATUSES:
            return False
        quota = self._reset_console_quota_if_ready(account, now).get("quota_console") or {}
        return int(quota.get("remaining") or 0) > 0

    @staticmethod
    def _normalize_account_type(value: Any) -> str | None:
        text = _clean_string(value)
        if not text:
            return None
        normalized = re.sub(r"[^a-z0-9]+", "", text.lower())
        if normalized in {"prolite", "pluslite"}:
            return "ProLite"
        return text

    @classmethod
    def _search_account_type(cls, payload: Any) -> str | None:
        if isinstance(payload, dict):
            for key in ("type", "account_type", "plan_type", "plan", "tier"):
                if key in payload:
                    account_type = cls._normalize_account_type(payload.get(key))
                    if account_type:
                        return account_type
            for value in payload.values():
                account_type = cls._search_account_type(value)
                if account_type:
                    return account_type
        elif isinstance(payload, (list, tuple, set)):
            for value in payload:
                account_type = cls._search_account_type(value)
                if account_type:
                    return account_type
        return None

    def _normalize_account(self, item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        provider = normalize_provider(item.get("provider"))
        access_token = _normalize_grok_access_token(item.get("access_token") or "") if provider == GROK_PROVIDER else item.get("access_token") or ""
        if not access_token:
            return None
        normalized = dict(item)
        normalized["access_token"] = access_token
        normalized["provider"] = provider
        account_type = self._normalize_account_type(normalized.get("type")) or self._search_account_type(normalized) or "free"
        normalized["type"] = account_type
        normalized["status"] = normalized.get("status") or "正常"
        quota_value = normalized.get("quota")
        normalized["quota"] = max(0, int(quota_value if quota_value is not None else 0))
        normalized["image_quota_unknown"] = bool(normalized.get("image_quota_unknown"))
        normalized["email"] = normalized.get("email") or None
        normalized["user_id"] = normalized.get("user_id") or None
        limits_progress = normalized.get("limits_progress")
        normalized["limits_progress"] = limits_progress if isinstance(limits_progress, list) else []
        normalized["default_model_slug"] = normalized.get("default_model_slug") or None
        normalized["restore_at"] = normalized.get("restore_at") or None
        if normalized["provider"] == GROK_PROVIDER:
            raw_tier = normalized.get("tier") or normalized.get("model_tier")
            normalized_tier = _normalize_grok_tier(raw_tier) or _clean_string(raw_tier) or None
            normalized["tier"] = normalized_tier
            if "model_tier" in normalized:
                normalized["model_tier"] = normalized_tier
            normalized["app_chat"] = bool(normalized.get("app_chat"))
            normalized["quota_console"] = self._normalize_console_quota(normalized.get("quota_console"))
            normalized["capabilities"] = _normalize_string_list(normalized.get("capabilities"))
            cf_cookies = normalized.get("cf_cookies")
            normalized["cf_cookies"] = cf_cookies if isinstance(cf_cookies, dict) else _clean_string(cf_cookies)
            normalized["user_agent"] = _clean_string(normalized.get("user_agent")) or None
        normalized["success"] = int(normalized.get("success") or 0)
        normalized["fail"] = int(normalized.get("fail") or 0)
        normalized["last_used_at"] = normalized.get("last_used_at")
        return normalized

    def list_tokens(self) -> list[str]:
        with self._lock:
            return list(self._accounts)

    def _list_ready_candidate_tokens(self, excluded_tokens: set[str] | None = None) -> list[str]:
        excluded = set(excluded_tokens or set())
        return [
            token
            for item in self._accounts.values()
            if self._is_image_account_available(item)
               and (token := item.get("access_token") or "")
               and token not in excluded
        ]

    def _list_available_candidate_tokens(self, excluded_tokens: set[str] | None = None) -> list[str]:
        max_concurrency = max(1, int(config.image_account_concurrency or 1))
        return [
            token
            for token in self._list_ready_candidate_tokens(excluded_tokens)
            if int(self._image_inflight.get(token, 0)) < max_concurrency
        ]

    def _acquire_next_candidate_token(self, excluded_tokens: set[str] | None = None) -> str:
        with self._image_slot_condition:
            while True:
                if not self._list_ready_candidate_tokens(excluded_tokens):
                    raise RuntimeError("no available image quota")
                tokens = self._list_available_candidate_tokens(excluded_tokens)
                if tokens:
                    access_token = tokens[self._index % len(tokens)]
                    self._index += 1
                    self._image_inflight[access_token] = int(self._image_inflight.get(access_token, 0)) + 1
                    return access_token
                self._image_slot_condition.wait(timeout=1.0)

    def release_image_slot(self, access_token: str) -> None:
        if not access_token:
            return
        with self._image_slot_condition:
            current_inflight = int(self._image_inflight.get(access_token, 0))
            if current_inflight <= 1:
                self._image_inflight.pop(access_token, None)
            else:
                self._image_inflight[access_token] = current_inflight - 1
            self._image_slot_condition.notify_all()

    def get_available_access_token(self, excluded_tokens: set[str] | None = None) -> str:
        attempted_tokens: set[str] = set(excluded_tokens or set())
        while True:
            access_token = self._acquire_next_candidate_token(excluded_tokens=attempted_tokens)
            attempted_tokens.add(access_token)
            try:
                account = self.fetch_remote_info(access_token, "get_available_access_token")
            except Exception:
                self.release_image_slot(access_token)
                continue
            if self._is_image_account_available(account or {}):
                return access_token
            self.release_image_slot(access_token)

    def get_text_access_token(self, excluded_tokens: set[str] | None = None, provider: str = GPT_PROVIDER) -> str:
        excluded = set(excluded_tokens or set())
        target_provider = normalize_provider(provider)
        with self._lock:
            candidates = [
                token
                for account in self._accounts.values()
                if account.get("status") not in {"禁用", "异常", "限流"}
                   and normalize_provider(account.get("provider")) == target_provider
                   and (token := account.get("access_token") or "")
                   and token not in excluded
            ]
            return self._next_text_token(candidates)

    def _next_text_token(self, candidates: list[str]) -> str:
        if not candidates:
            return ""
        access_token = candidates[self._index % len(candidates)]
        self._index += 1
        return access_token

    def _reserve_grok_console_quota(self, account: dict, now: float) -> dict:
        next_item = self._reset_console_quota_if_ready(account, now)
        quota = self._normalize_console_quota(next_item.get("quota_console"))
        quota["remaining"] = max(0, int(quota.get("remaining") or 0) - 1)
        if quota["remaining"] == 0 and quota.get("reset_at") is None:
            quota["reset_at"] = int(now) + int(quota.get("window_seconds") or GROK_CONSOLE_QUOTA_WINDOW_SECONDS)
        next_item["quota_console"] = quota
        next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return next_item

    def get_grok_console_access_token(self, excluded_tokens: set[str] | None = None) -> str:
        excluded = set(excluded_tokens or set())
        with self._lock:
            now = self._now()
            candidates: list[str] = []
            changed = False
            for token, account in list(self._accounts.items()):
                if normalize_provider(account.get("provider")) != GROK_PROVIDER:
                    continue
                if (account.get("access_token") or "") in excluded:
                    continue
                refreshed = self._reset_console_quota_if_ready(account, now)
                if refreshed != account:
                    self._accounts[token] = refreshed
                    changed = True
                if self._is_console_account_available(refreshed, now):
                    candidates.append(refreshed.get("access_token") or "")
            access_token = self._next_text_token([candidate for candidate in candidates if candidate])
            if access_token:
                reserved = self._reserve_grok_console_quota(self._accounts[access_token], now)
                account = self._normalize_account(reserved)
                if account is not None:
                    self._accounts[access_token] = account
                    changed = True
            if changed:
                self._save_accounts()
            return access_token

    def get_grok_app_chat_access_token(self, spec: ModelSpec, excluded_tokens: set[str] | None = None) -> str:
        requested_tiers = _grok_requested_tiers(spec)
        if not requested_tiers:
            return self.get_text_access_token(excluded_tokens=excluded_tokens, provider=GROK_PROVIDER)
        excluded = set(excluded_tokens or set())
        with self._lock:
            accounts = [
                account
                for account in self._accounts.values()
                if normalize_provider(account.get("provider")) == GROK_PROVIDER
                   and account.get("status") not in GROK_UNAVAILABLE_STATUSES
                   and account.get("access_token")
                   and account.get("access_token") not in excluded
                   and _grok_account_has_capability(account, spec)
            ]
            for requested_tier in requested_tiers:
                tiered_candidates = [
                    account.get("access_token") or ""
                    for account in accounts
                    if _grok_tier_matches(_normalize_grok_tier(account.get("tier") or account.get("model_tier")), requested_tier)
                ]
                token = self._next_text_token([candidate for candidate in tiered_candidates if candidate])
                if token:
                    return token
        return self.get_text_access_token(excluded_tokens=excluded_tokens, provider=GROK_PROVIDER)

    def mark_text_used(self, access_token: str) -> None:
        if not access_token:
            return
        with self._lock:
            current = self._accounts.get(access_token)
            if current is None:
                return
            next_item = dict(current)
            next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            account = self._normalize_account(next_item)
            if account is None:
                return
            self._accounts[access_token] = account
            self._save_accounts()

    def mark_grok_console_used(self, access_token: str, success: bool = True) -> None:
        if not access_token:
            return
        with self._lock:
            current = self._accounts.get(access_token)
            if current is None:
                return
            next_item = dict(current)
            next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if success:
                next_item["success"] = int(next_item.get("success") or 0) + 1
            else:
                next_item["fail"] = int(next_item.get("fail") or 0) + 1
            account = self._normalize_account(next_item)
            if account is None:
                return
            self._accounts[access_token] = account
            self._save_accounts()

    def remove_invalid_token(self, access_token: str, event: str) -> bool:
        if not config.auto_remove_invalid_accounts:
            self.update_account(access_token, {"status": "异常", "quota": 0})
            return False
        removed = bool(self.delete_accounts([access_token])["removed"])
        if removed:
            log_service.add(LOG_TYPE_ACCOUNT, "自动移除异常账号",
                            {"source": event, "token": anonymize_token(access_token)})
        elif access_token:
            self.update_account(access_token, {"status": "异常", "quota": 0})
        return removed

    def get_account(self, access_token: str) -> dict | None:
        if not access_token:
            return None
        with self._lock:
            account = self._accounts.get(access_token)
            return dict(account) if account else None

    def list_accounts(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self._accounts.values()]

    def list_limited_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for item in self._accounts.values()
                if item.get("status") == "限流"
                   and normalize_provider(item.get("provider")) == GPT_PROVIDER
                   and (token := item.get("access_token") or "")
            ]

    def add_accounts(self, tokens: list[str]) -> dict:
        tokens = list(dict.fromkeys(token for token in tokens if token))
        if not tokens:
            return {"added": 0, "skipped": 0, "items": self.list_accounts()}

        with self._lock:
            added = 0
            skipped = 0
            for access_token in tokens:
                current = self._accounts.get(access_token)
                if current is None:
                    added += 1
                    current = {}
                else:
                    skipped += 1
                account = self._normalize_account(
                    {
                        **current,
                        "access_token": access_token,
                        "type": str(current.get("type") or "free"),
                        "provider": current.get("provider") or GPT_PROVIDER,
                    }
                )
                if account is not None:
                    self._accounts[access_token] = account
            self._save_accounts()
            items = [dict(item) for item in self._accounts.values()]
            log_service.add(LOG_TYPE_ACCOUNT, f"新增 {added} 个账号，跳过 {skipped} 个",
                            {"added": added, "skipped": skipped})
        return {"added": added, "skipped": skipped, "items": items}

    def add_account_items(self, items: list[dict[str, Any]]) -> dict:
        payloads: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            provider = normalize_provider(item.get("provider"))
            access_token = _clean_string(item.get("access_token") or item.get("accessToken"))
            if provider == GROK_PROVIDER:
                access_token = _normalize_grok_access_token(access_token)
            if not access_token or access_token in seen_tokens:
                continue

            payload = dict(item)
            payload["access_token"] = access_token
            payload.pop("accessToken", None)
            if _clean_string(payload.get("type")).lower() == "codex":
                payload.setdefault("export_type", "codex")
                payload.pop("type", None)
            payloads.append(payload)
            seen_tokens.add(access_token)

        if not payloads:
            return {"added": 0, "skipped": 0, "items": self.list_accounts()}

        with self._lock:
            added = 0
            skipped = 0
            for payload in payloads:
                access_token = payload["access_token"]
                current = self._accounts.get(access_token)
                if current is None:
                    added += 1
                    current = {}
                else:
                    skipped += 1
                account = self._normalize_account({**current, **payload})
                if account is not None:
                    self._accounts[access_token] = account
            self._save_accounts()
            items = [dict(item) for item in self._accounts.values()]
            log_service.add(LOG_TYPE_ACCOUNT, f"新增 {added} 个账号，跳过 {skipped} 个",
                            {"added": added, "skipped": skipped})
        return {"added": added, "skipped": skipped, "items": items}

    def replace_account_items_for_remote_source(self, source_id: str, items: list[dict[str, Any]]) -> dict:
        source_id = _clean_string(source_id)
        if not source_id:
            raise ValueError("source_id is required")

        payloads: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            provider = normalize_provider(item.get("provider"))
            access_token = _clean_string(item.get("access_token") or item.get("accessToken"))
            if provider == GROK_PROVIDER:
                access_token = _normalize_grok_access_token(access_token)
            if not access_token or access_token in seen_tokens:
                continue

            payload = dict(item)
            payload["access_token"] = access_token
            payload.pop("accessToken", None)
            if _clean_string(payload.get("type")).lower() == "codex":
                payload.setdefault("export_type", "codex")
                payload.pop("type", None)
            payloads.append(payload)
            seen_tokens.add(access_token)

        if not payloads:
            raise ValueError("replace requires a non-empty account payload")

        with self._lock:
            next_accounts = {
                token: dict(account)
                for token, account in self._accounts.items()
                if account.get("remote_source_id") != source_id
            }
            removed = len(self._accounts) - len(next_accounts)
            added = 0
            skipped = 0
            for payload in payloads:
                access_token = payload["access_token"]
                current = next_accounts.get(access_token)
                if current is None:
                    added += 1
                    current = {}
                else:
                    skipped += 1
                account = self._normalize_account({**current, **payload})
                if account is not None:
                    next_accounts[access_token] = account

            self.storage.save_accounts(list(next_accounts.values()))
            self._accounts = next_accounts
            for token, count in list(self._image_inflight.items()):
                if token not in self._accounts:
                    self._image_inflight.pop(token, None)
            if self._accounts:
                self._index %= len(self._accounts)
            else:
                self._index = 0
            log_service.add(LOG_TYPE_ACCOUNT, f"替换远程来源账号，新增 {added} 个，跳过 {skipped} 个，删除 {removed} 个", {"added": added, "skipped": skipped, "removed": removed})
            items = [dict(item) for item in self._accounts.values()]
        return {"added": added, "skipped": skipped, "removed": removed, "items": items}

    def delete_accounts(self, tokens: list[str]) -> dict:
        target_set = set(token for token in tokens if token)
        if not target_set:
            return {"removed": 0, "items": self.list_accounts()}
        with self._lock:
            removed = sum(self._accounts.pop(token, None) is not None for token in target_set)
            for token in target_set:
                self._image_inflight.pop(token, None)
            if removed:
                if self._accounts:
                    self._index %= len(self._accounts)
                else:
                    self._index = 0
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, f"删除 {removed} 个账号", {"removed": removed})
            items = [dict(item) for item in self._accounts.values()]
        return {"removed": removed, "items": items}

    def delete_limited_accounts(self) -> dict:
        with self._lock:
            target_tokens = [
                token
                for token, account in self._accounts.items()
                if account.get("status") == "限流"
            ]
            removed = sum(self._accounts.pop(token, None) is not None for token in target_tokens)
            for token in target_tokens:
                self._image_inflight.pop(token, None)
            if removed:
                if self._accounts:
                    self._index %= len(self._accounts)
                else:
                    self._index = 0
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, f"删除 {removed} 个限流账号", {"removed": removed})
            items = [dict(item) for item in self._accounts.values()]
        return {"removed": removed, "items": items}

    def update_account(self, access_token: str, updates: dict) -> dict | None:
        if not access_token:
            return None
        with self._lock:
            current = self._accounts.get(access_token)
            if current is None:
                return None
            account = self._normalize_account({**current, **updates, "access_token": access_token})
            if account is None:
                return None
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                self._accounts.pop(access_token, None)
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {"token": anonymize_token(access_token)})
                return None
            self._accounts[access_token] = account
            self._save_accounts()
            log_service.add(LOG_TYPE_ACCOUNT, "更新账号",
                            {"token": anonymize_token(access_token), "status": account.get("status")})
            return dict(account)
        return None

    def mark_image_result(self, access_token: str, success: bool) -> dict | None:
        if not access_token:
            return None
        self.release_image_slot(access_token)
        with self._lock:
            current = self._accounts.get(access_token)
            if current is None:
                return None
            next_item = dict(current)
            next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            image_quota_unknown = bool(next_item.get("image_quota_unknown"))
            if success:
                next_item["success"] = int(next_item.get("success") or 0) + 1
                if not image_quota_unknown:
                    next_item["quota"] = max(0, int(next_item.get("quota") or 0) - 1)
                if not image_quota_unknown and next_item["quota"] == 0:
                    next_item["status"] = "限流"
                    next_item["restore_at"] = next_item.get("restore_at") or None
                elif next_item.get("status") == "限流":
                    next_item["status"] = "正常"
            else:
                next_item["fail"] = int(next_item.get("fail") or 0) + 1
            account = self._normalize_account(next_item)
            if account is None:
                return None
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                self._accounts.pop(access_token, None)
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {"token": anonymize_token(access_token)})
                return None
            self._accounts[access_token] = account
            self._save_accounts()
            return dict(account)
        return None

    def fetch_remote_info(self, access_token: str, event: str = "fetch_remote_info") -> dict[str, Any] | None:
        if not access_token:
            raise ValueError("access_token is required")
        account = self.get_account(access_token) or {}
        provider = normalize_provider(account.get("provider"))
        if provider == GROK_PROVIDER:
            return self.fetch_grok_remote_info(access_token, event)
        if provider != GPT_PROVIDER:
            return dict(account) if account else None
        from services.openai_backend_api import InvalidAccessTokenError, OpenAIBackendAPI

        try:
            with OpenAIBackendAPI(access_token) as backend:
                result = backend.get_user_info()
        except InvalidAccessTokenError:
            self.remove_invalid_token(access_token, event)
            raise
        return self.update_account(access_token, result)

    @staticmethod
    def _is_grok_auth_failure_payload(payload: Any) -> bool:
        if isinstance(payload, dict):
            for key in ("error", "message", "detail", "code", "reason"):
                text = _clean_string(payload.get(key)).lower()
                if any(marker in text for marker in ("auth", "login", "session", "token", "unauthorized", "forbidden")):
                    return True
            return any(AccountService._is_grok_auth_failure_payload(value) for value in payload.values())
        if isinstance(payload, (list, tuple, set)):
            return any(AccountService._is_grok_auth_failure_payload(value) for value in payload)
        return False

    def fetch_grok_remote_info(self, access_token: str, event: str = "fetch_grok_remote_info") -> dict[str, Any] | None:
        account = self.get_account(access_token) or {}
        from services.providers.grok import GrokConsoleError, validate_grok_access_token

        try:
            payload = validate_grok_access_token(access_token, account)
        except GrokConsoleError as exc:
            status = exc.upstream_status or exc.status_code
            if status in {401, 403} or any(marker in str(exc).lower() for marker in ("auth", "login", "session", "token")):
                self.remove_invalid_token(access_token, event)
            elif status in {402, 429}:
                self.update_account(access_token, {"status": "限流"})
            raise
        if self._is_grok_auth_failure_payload(payload):
            self.remove_invalid_token(access_token, event)
            raise RuntimeError("Grok app-chat authentication failed")
        return self.update_account(access_token, {"status": "正常", "app_chat": True})

    def _refresh_error_message(self, access_token: str, exc: Exception) -> str:
        account = self.get_account(access_token) or {}
        if normalize_provider(account.get("provider")) == GROK_PROVIDER:
            return "Grok app-chat rate-limit validation failed"
        return str(exc)

    def refresh_accounts(self, access_tokens: list[str]) -> dict[str, Any]:
        access_tokens = list(dict.fromkeys(token for token in access_tokens if token))
        with self._lock:
            access_tokens = [
                token
                for token in access_tokens
                if normalize_provider((self._accounts.get(token) or {}).get("provider")) in {GPT_PROVIDER, GROK_PROVIDER}
            ]
        if not access_tokens:
            return {"refreshed": 0, "errors": [], "items": self.list_accounts()}

        refreshed = 0
        errors = []
        max_workers = min(10, len(access_tokens))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.fetch_remote_info, token, "refresh_accounts"): token
                for token in access_tokens
            }
            for future in as_completed(futures):
                try:
                    account = future.result()
                except Exception as exc:
                    token = futures[future]
                    errors.append({"token": anonymize_token(token), "error": self._refresh_error_message(token, exc)})
                    continue
                if account is not None:
                    refreshed += 1

        return {
            "refreshed": refreshed,
            "errors": errors,
            "items": self.list_accounts(),
        }

    def build_export_items(self, access_tokens: list[str] | None = None, provider: str | None = None) -> list[dict[str, str]]:
        requested_tokens = [token for token in dict.fromkeys(access_tokens or []) if token]
        provider_filter = normalize_provider(provider) if provider else None
        if provider_filter not in {None, GPT_PROVIDER, GROK_PROVIDER}:
            return []
        with self._lock:
            if requested_tokens:
                accounts = [dict(self._accounts[token]) for token in requested_tokens if token in self._accounts]
            else:
                accounts = [dict(item) for item in self._accounts.values()]

        export_items: list[dict[str, str]] = []
        for account in accounts:
            access_token = _clean_string(account.get("access_token"))
            if not access_token:
                continue
            if provider_filter is not None and normalize_provider(account.get("provider")) != provider_filter:
                continue

            id_token = _clean_string(account.get("id_token"))
            refresh_token = _clean_string(account.get("refresh_token"))
            access_claims = _decode_jwt_payload(access_token)
            id_claims = _decode_jwt_payload(id_token)
            access_auth = _nested_dict(access_claims.get("https://api.openai.com/auth"))
            id_auth = _nested_dict(id_claims.get("https://api.openai.com/auth"))
            profile = _nested_dict(access_claims.get("https://api.openai.com/profile"))

            email = (
                _clean_string(account.get("email"))
                or _clean_string(profile.get("email"))
                or _clean_string(id_claims.get("email"))
            )
            account_id = (
                _clean_string(account.get("account_id"))
                or _clean_string(access_auth.get("chatgpt_account_id"))
                or _clean_string(id_auth.get("chatgpt_account_id"))
            )
            expired = _clean_string(account.get("expired")) or _format_timestamp(access_claims.get("exp"))
            last_refresh = (
                _clean_string(account.get("last_refresh"))
                or _format_timestamp(access_claims.get("iat"))
                or _format_timestamp(access_claims.get("nbf"))
            )

            export_items.append(
                {
                    "type": _clean_string(account.get("export_type")) or "codex",
                    "email": email,
                    "expired": expired,
                    "id_token": id_token,
                    "account_id": account_id,
                    "access_token": access_token,
                    "sso": _clean_string(account.get("sso")),
                    "last_refresh": last_refresh,
                    "refresh_token": refresh_token,
                }
            )

        return export_items

    @staticmethod
    def build_export_text(items: list[dict[str, str]]) -> str:
        lines = [credential for item in items if (credential := _clean_string(item.get("access_token") or item.get("sso")))]
        return "\n".join(lines) + ("\n" if lines else "")


account_service = AccountService(config.get_storage_backend())
