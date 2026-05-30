from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Condition, Lock
from typing import Any, Callable

from services.config import config
from services.log_service import (
    LOG_TYPE_ACCOUNT,
    log_service,
)
from services.models import GEMINI_PROVIDER, GPT_PROVIDER, GROK_PROVIDER, ModelSpec, normalize_account_provider, normalize_provider
from services.providers.registry import account_strategy
from services.providers.gemini.accounts import account_row_id as gemini_account_row_id
from services.storage.base import StorageBackend
from utils.helper import anonymize_token


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _account_strategy(provider: Any):
    return account_strategy(provider)


def _item_provider(item: dict[str, Any]) -> str:
    return normalize_provider(item.get("provider")) if not _clean_string(item.get("provider")) else normalize_account_provider(item.get("provider"))


def _normalize_access_token_for_provider(item: dict, provider: str) -> str:
    return _account_strategy(provider).normalize_access_token(item)


def _normalized_delete_tokens_for_provider(tokens: list[str], provider: str | None) -> set[str]:
    target_set = set(token for token in tokens if token)
    if not target_set or not provider:
        return target_set
    provider_strategy = _account_strategy(provider)
    for token in tokens:
        token_text = _clean_string(token)
        if not token_text:
            continue
        normalized = provider_strategy.normalize_access_token({"access_token": token_text, "provider": provider})
        if normalized:
            target_set.add(normalized)
            normalized_account = provider_strategy.normalize_account({"access_token": normalized, "provider": provider})
            normalized_account_token = _clean_string(normalized_account.get("access_token"))
            if normalized_account_token:
                target_set.add(normalized_account_token)
    return target_set


def _matched_delete_tokens_for_provider(target_set: set[str], accounts: dict[str, dict], provider: str | None) -> set[str]:
    if not target_set or not provider:
        return set()
    provider_strategy = _account_strategy(provider)
    matched_tokens: set[str] = set()
    for account_token, account in accounts.items():
        for token in target_set:
            if provider_strategy.delete_token_matches_account(token, account):
                matched_tokens.add(account_token)
                break
    return matched_tokens


def _matched_delete_identifiers_for_provider(identifiers: list[dict[str, str]], accounts: dict[str, dict], provider: str | None) -> set[str]:
    if provider != GEMINI_PROVIDER or not identifiers:
        return set()
    account_ids = {
        _clean_string(identifier.get("account_id"))
        for identifier in identifiers
        if isinstance(identifier, dict) and _clean_string(identifier.get("account_id"))
    }
    row_ids = {
        _clean_string(identifier.get("row_id"))
        for identifier in identifiers
        if isinstance(identifier, dict) and _clean_string(identifier.get("row_id"))
    }
    if not account_ids and not row_ids:
        return set()
    return {
        account_token
        for account_token, account in accounts.items()
        if (account_ids and _clean_string(account.get("account_id")) in account_ids)
        or (row_ids and gemini_account_row_id(account) in row_ids)
    }


class AccountService:
    """账号池服务，按 provider + token 管理账号。"""

    def __init__(self, storage_backend: StorageBackend, now: Callable[[], float] | None = None):
        self.storage = storage_backend
        self._lock = Lock()
        self._image_slot_condition = Condition(self._lock)
        self._index = 0
        self._now = now or (lambda: datetime.now(timezone.utc).timestamp())
        self._accounts = self._load_accounts()
        self._image_inflight: dict[str, int] = {}

    def _load_accounts(self) -> dict[str, dict[str, dict]]:
        accounts: dict[str, dict[str, dict]] = {}
        for item in self.storage.load_accounts():
            normalized = self._normalize_account(item)
            if normalized is None:
                continue
            provider = normalized["provider"]
            accounts.setdefault(provider, {})[normalized["access_token"]] = normalized
        return accounts

    def _save_accounts(self) -> None:
        self.storage.save_accounts(self._all_accounts_locked())

    def _all_accounts_locked(self) -> list[dict]:
        return [account for provider_accounts in self._accounts.values() for account in provider_accounts.values()]

    def _account_count_locked(self) -> int:
        return sum(len(provider_accounts) for provider_accounts in self._accounts.values())

    def _provider_filter(self, provider: str | None) -> str | None:
        return normalize_account_provider(provider) if _clean_string(provider) else None

    def _provider_from_token_locked(self, access_token: str) -> str | None:
        if access_token in self._accounts.get(GPT_PROVIDER, {}):
            return GPT_PROVIDER
        for provider, provider_accounts in self._accounts.items():
            if access_token in provider_accounts:
                return provider
        return None

    def _provider_accounts_locked(self, provider: str) -> dict[str, dict]:
        return self._accounts.setdefault(provider, {})

    def _list_account_items_locked(self, provider: str | None = None) -> list[dict]:
        provider_filter = self._provider_filter(provider)
        if provider_filter is not None:
            return [dict(item) for item in self._accounts.get(provider_filter, {}).values()]
        return [dict(item) for item in self._all_accounts_locked()]

    def _list_account_tokens_locked(self, provider: str | None = None) -> list[str]:
        provider_filter = self._provider_filter(provider)
        if provider_filter is not None:
            return list(self._accounts.get(provider_filter, {}))
        return [token for provider_accounts in self._accounts.values() for token in provider_accounts]

    def _get_account_locked(self, access_token: str, provider: str | None = None) -> dict | None:
        if provider_filter := self._provider_filter(provider):
            return self._accounts.get(provider_filter, {}).get(access_token)
        for provider_accounts in self._accounts.values():
            if access_token in provider_accounts:
                return provider_accounts[access_token]
        return None

    def _set_account_locked(self, account: dict) -> None:
        provider = account["provider"]
        self._provider_accounts_locked(provider)[account["access_token"]] = account

    def _pop_account_locked(self, access_token: str, provider: str | None = None) -> dict | None:
        if provider_filter := self._provider_filter(provider):
            return self._accounts.get(provider_filter, {}).pop(access_token, None)
        for provider_accounts in self._accounts.values():
            if access_token in provider_accounts:
                return provider_accounts.pop(access_token)
        return None

    def _pop_accounts_by_token_locked(self, access_token: str, provider: str | None = None) -> int:
        if provider_filter := self._provider_filter(provider):
            return int(self._accounts.get(provider_filter, {}).pop(access_token, None) is not None)
        removed = 0
        for provider_accounts in self._accounts.values():
            removed += int(provider_accounts.pop(access_token, None) is not None)
        return removed


    @staticmethod
    def _is_image_account_available(account: dict) -> bool:
        if normalize_provider(account.get("provider")) != GPT_PROVIDER:
            return False
        return account_strategy(GPT_PROVIDER).is_image_account_available(account)

    def _reset_console_quota_if_ready(self, account: dict, now: float | None = None) -> dict:
        current_time = self._now() if now is None else now
        return account_strategy(GROK_PROVIDER).reset_console_quota_if_ready(account, current_time)

    def _is_console_account_available(self, account: dict, now: float | None = None) -> bool:
        if not isinstance(account, dict):
            return False
        if normalize_provider(account.get("provider")) != GROK_PROVIDER:
            return False
        current_time = self._now() if now is None else now
        return account_strategy(GROK_PROVIDER).is_console_account_available(account, current_time)

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
        try:
            provider = _item_provider(item)
        except ValueError:
            return None
        access_token = _normalize_access_token_for_provider(item, provider)
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
        provider_strategy = account_strategy(normalized["provider"])
        if normalized["provider"] in {GROK_PROVIDER, GEMINI_PROVIDER}:
            normalized = provider_strategy.normalize_account(normalized)
        normalized["success"] = int(normalized.get("success") or 0)
        normalized["fail"] = int(normalized.get("fail") or 0)
        normalized["last_used_at"] = normalized.get("last_used_at")
        return normalized

    def list_tokens(self, provider: str | None = None) -> list[str]:
        with self._lock:
            return self._list_account_tokens_locked(provider)

    def _list_ready_candidate_tokens(self, excluded_tokens: set[str] | None = None) -> list[str]:
        excluded = set(excluded_tokens or set())
        return [
            token
            for item in self._all_accounts_locked()
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
        target_provider = normalize_account_provider(provider)
        with self._lock:
            candidates = [
                token
                for account in self._all_accounts_locked()
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
        grok_strategy = account_strategy(GROK_PROVIDER)
        quota = grok_strategy.normalize_console_quota(next_item.get("quota_console"))
        quota["remaining"] = max(0, int(quota.get("remaining") or 0) - 1)
        if quota["remaining"] == 0 and quota.get("reset_at") is None:
            quota["reset_at"] = int(now) + int(quota.get("window_seconds") or 900)
        next_item["quota_console"] = quota
        next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return next_item

    def get_grok_console_access_token(self, excluded_tokens: set[str] | None = None) -> str:
        excluded = set(excluded_tokens or set())
        with self._lock:
            now = self._now()
            candidates: list[str] = []
            changed = False
            for account in list(self._accounts.get(GROK_PROVIDER, {}).values()):
                if (account.get("access_token") or "") in excluded:
                    continue
                refreshed = self._reset_console_quota_if_ready(account, now)
                if refreshed != account:
                    self._set_account_locked(refreshed)
                    changed = True
                if self._is_console_account_available(refreshed, now):
                    candidates.append(refreshed.get("access_token") or "")
            access_token = self._next_text_token([candidate for candidate in candidates if candidate])
            if access_token:
                current = self._get_account_locked(access_token, GROK_PROVIDER)
                if current is not None:
                    reserved = self._reserve_grok_console_quota(current, now)
                    account = self._normalize_account(reserved)
                    if account is not None:
                        self._set_account_locked(account)
                        changed = True
            if changed:
                self._save_accounts()
            return access_token

    def get_grok_app_chat_access_token(self, spec: ModelSpec, excluded_tokens: set[str] | None = None) -> str:
        requested_tiers = account_strategy(GROK_PROVIDER).requested_tiers(spec)
        if not requested_tiers:
            return self.get_text_access_token(excluded_tokens=excluded_tokens, provider=GROK_PROVIDER)
        excluded = set(excluded_tokens or set())
        with self._lock:
            accounts = [
                account
                for account in self._all_accounts_locked()
                if normalize_provider(account.get("provider")) == GROK_PROVIDER
                   and account.get("status") not in account_strategy(GROK_PROVIDER).UNAVAILABLE_STATUSES
                   and account.get("access_token")
                   and account.get("access_token") not in excluded
                   and account_strategy(GROK_PROVIDER).account_has_capability(account, spec)
            ]
            for requested_tier in requested_tiers:
                tiered_candidates = [
                    account.get("access_token") or ""
                    for account in accounts
                    if account_strategy(GROK_PROVIDER).tier_matches(account_strategy(GROK_PROVIDER).normalize_tier(account.get("tier") or account.get("model_tier")), requested_tier)
                ]
                token = self._next_text_token([candidate for candidate in tiered_candidates if candidate])
                if token:
                    return token
        return self.get_text_access_token(excluded_tokens=excluded_tokens, provider=GROK_PROVIDER)

    def mark_text_used(self, access_token: str) -> None:
        if not access_token:
            return
        with self._lock:
            provider = self._provider_from_token_locked(access_token)
            current = self._get_account_locked(access_token, provider)
            if current is None:
                return
            next_item = dict(current)
            next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            account = self._normalize_account(next_item)
            if account is None:
                return
            self._set_account_locked(account)
            self._save_accounts()

    def mark_grok_console_used(self, access_token: str, success: bool = True) -> None:
        if not access_token:
            return
        with self._lock:
            current = self._get_account_locked(access_token, GROK_PROVIDER)
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
            self._set_account_locked(account)
            self._save_accounts()

    def remove_invalid_token(self, access_token: str, event: str) -> bool:
        with self._lock:
            provider = self._provider_from_token_locked(access_token)
        if not config.auto_remove_invalid_accounts:
            self.update_account(access_token, {"status": "异常", "quota": 0}, provider=provider)
            return False
        removed = bool(self.delete_accounts([access_token], provider=provider)["removed"])
        if removed:
            log_service.add(LOG_TYPE_ACCOUNT, "自动移除异常账号",
                            {"source": event, "token": anonymize_token(access_token)})
        elif access_token:
            self.update_account(access_token, {"status": "异常", "quota": 0}, provider=provider)
        return removed

    def get_account(self, access_token: str, provider: str | None = None) -> dict | None:
        if not access_token:
            return None
        with self._lock:
            account = self._get_account_locked(access_token, provider)
            return dict(account) if account else None

    def list_accounts(self, provider: str | None = None) -> list[dict]:
        with self._lock:
            return self._list_account_items_locked(provider)

    def list_limited_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for item in self._all_accounts_locked()
                if item.get("status") == "限流"
                   and normalize_provider(item.get("provider")) == GPT_PROVIDER
                   and (token := item.get("access_token") or "")
            ]

    def add_accounts(self, tokens: list[str], provider: str | None = None) -> dict:
        target_provider = self._provider_filter(provider) or GPT_PROVIDER
        tokens = list(dict.fromkeys(token for token in tokens if token))
        if not tokens:
            return {"added": 0, "skipped": 0, "items": self.list_accounts()}

        with self._lock:
            added = 0
            skipped = 0
            provider_accounts = self._provider_accounts_locked(target_provider)
            for access_token in tokens:
                current = provider_accounts.get(access_token)
                current_payload = current or {}
                account = self._normalize_account(
                    {
                        **current_payload,
                        "access_token": access_token,
                        "type": str(current_payload.get("type") or "free"),
                        "provider": target_provider,
                    }
                )
                if account is not None:
                    if current is None:
                        added += 1
                    else:
                        skipped += 1
                    self._set_account_locked(account)
            self._save_accounts()
            items = [dict(item) for item in self._all_accounts_locked()]
            log_service.add(LOG_TYPE_ACCOUNT, f"新增 {added} 个账号，跳过 {skipped} 个",
                            {"added": added, "skipped": skipped})
        return {"added": added, "skipped": skipped, "items": items}

    def add_account_items(self, items: list[dict[str, Any]]) -> dict:
        payloads: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                provider = _item_provider(item)
            except ValueError:
                continue
            access_token = _normalize_access_token_for_provider(item, provider)
            account_key = (provider, access_token)
            if not access_token or account_key in seen_keys:
                continue

            payload = dict(item)
            payload["access_token"] = access_token
            payload.pop("accessToken", None)
            if _clean_string(payload.get("type")).lower() == "codex":
                payload.setdefault("export_type", "codex")
                payload.pop("type", None)
            payloads.append(payload)
            seen_keys.add(account_key)

        if not payloads:
            return {"added": 0, "skipped": 0, "items": self.list_accounts()}

        with self._lock:
            added = 0
            skipped = 0
            for payload in payloads:
                access_token = payload["access_token"]
                provider = _item_provider(payload)
                provider_accounts = self._provider_accounts_locked(provider)
                current = provider_accounts.get(access_token)
                if current is None:
                    added += 1
                    current = {}
                else:
                    skipped += 1
                account = self._normalize_account({**current, **payload})
                if account is not None:
                    self._set_account_locked(account)
            self._save_accounts()
            items = [dict(item) for item in self._all_accounts_locked()]
            log_service.add(LOG_TYPE_ACCOUNT, f"新增 {added} 个账号，跳过 {skipped} 个",
                            {"added": added, "skipped": skipped})
        return {"added": added, "skipped": skipped, "items": items}

    def replace_account_items_for_remote_source(self, source_id: str, items: list[dict[str, Any]]) -> dict:
        source_id = _clean_string(source_id)
        if not source_id:
            raise ValueError("source_id is required")

        payloads: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                provider = _item_provider(item)
            except ValueError:
                continue
            access_token = _normalize_access_token_for_provider(item, provider)
            account_key = (provider, access_token)
            if not access_token or account_key in seen_keys:
                continue

            payload = dict(item)
            payload["access_token"] = access_token
            payload.pop("accessToken", None)
            if _clean_string(payload.get("type")).lower() == "codex":
                payload.setdefault("export_type", "codex")
                payload.pop("type", None)
            payloads.append(payload)
            seen_keys.add(account_key)

        if not payloads:
            raise ValueError("replace requires a non-empty account payload")

        with self._lock:
            next_accounts: dict[str, dict[str, dict]] = {}
            for provider, provider_accounts in self._accounts.items():
                kept_accounts = {
                    token: dict(account)
                    for token, account in provider_accounts.items()
                    if account.get("remote_source_id") != source_id
                }
                if kept_accounts:
                    next_accounts[provider] = kept_accounts
            removed = self._account_count_locked() - sum(len(provider_accounts) for provider_accounts in next_accounts.values())
            added = 0
            skipped = 0
            for payload in payloads:
                access_token = payload["access_token"]
                provider = _item_provider(payload)
                provider_accounts = next_accounts.setdefault(provider, {})
                current = provider_accounts.get(access_token)
                if current is None:
                    added += 1
                    current = {}
                else:
                    skipped += 1
                account = self._normalize_account({**current, **payload})
                if account is not None:
                    provider_accounts[account["access_token"]] = account

            self.storage.save_accounts([account for provider_accounts in next_accounts.values() for account in provider_accounts.values()])
            self._accounts = next_accounts
            saved_tokens = {account.get("access_token") for provider_accounts in self._accounts.values() for account in provider_accounts.values()}
            for token, count in list(self._image_inflight.items()):
                if token not in saved_tokens:
                    self._image_inflight.pop(token, None)
            if self._account_count_locked():
                self._index %= self._account_count_locked()
            else:
                self._index = 0
            log_service.add(LOG_TYPE_ACCOUNT, f"替换远程来源账号，新增 {added} 个，跳过 {skipped} 个，删除 {removed} 个", {"added": added, "skipped": skipped, "removed": removed})
            items = [dict(item) for item in self._all_accounts_locked()]
        return {"added": added, "skipped": skipped, "removed": removed, "items": items}

    def delete_accounts(self, tokens: list[str], provider: str | None = None, identifiers: list[dict[str, str]] | None = None) -> dict:
        target_provider = self._provider_filter(provider)
        target_set = _normalized_delete_tokens_for_provider(tokens, target_provider)
        if not target_set and not identifiers:
            return {"removed": 0, "items": self.list_accounts(provider)}
        with self._lock:
            provider_accounts = self._accounts.get(target_provider, {}) if target_provider else {}
            matched_tokens = _matched_delete_tokens_for_provider(
                set(target_set),
                provider_accounts,
                target_provider,
            )
            matched_tokens.update(_matched_delete_identifiers_for_provider(identifiers or [], provider_accounts, target_provider))
            target_set.update(matched_tokens)
            removed = sum(self._pop_accounts_by_token_locked(token, target_provider) for token in target_set)
            for token in target_set:
                self._image_inflight.pop(token, None)
            if removed:
                if self._account_count_locked():
                    self._index %= self._account_count_locked()
                else:
                    self._index = 0
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, f"删除 {removed} 个账号", {"removed": removed})
            items = self._list_account_items_locked(provider)
        return {"removed": removed, "items": items}

    def delete_limited_accounts(self, provider: str | None = None) -> dict:
        with self._lock:
            provider_filter = self._provider_filter(provider)
            target_tokens = [
                account.get("access_token") or ""
                for account in self._all_accounts_locked()
                if account.get("status") == "限流"
                   and (provider_filter is None or account.get("provider") == provider_filter)
                   and account.get("access_token")
            ]
            removed = sum(self._pop_accounts_by_token_locked(token, provider_filter) for token in target_tokens)
            for token in target_tokens:
                self._image_inflight.pop(token, None)
            if removed:
                if self._account_count_locked():
                    self._index %= self._account_count_locked()
                else:
                    self._index = 0
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, f"删除 {removed} 个限流账号", {"removed": removed})
            items = self._list_account_items_locked(provider)
        return {"removed": removed, "items": items}

    def update_account(self, access_token: str, updates: dict, provider: str | None = None) -> dict | None:
        if not access_token:
            return None
        with self._lock:
            current = self._get_account_locked(access_token, provider)
            if current is None:
                return None
            account = self._normalize_account({**current, **updates, "access_token": access_token})
            if account is None:
                return None
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                self._pop_account_locked(access_token, provider)
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {"token": anonymize_token(access_token)})
                return None
            self._set_account_locked(account)
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
            current = self._get_account_locked(access_token, GPT_PROVIDER)
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
                self._pop_account_locked(access_token, GPT_PROVIDER)
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {"token": anonymize_token(access_token)})
                return None
            self._set_account_locked(account)
            self._save_accounts()
            return dict(account)
        return None

    def fetch_remote_info(self, access_token: str, event: str = "fetch_remote_info", provider: str | None = None) -> dict[str, Any] | None:
        if not access_token:
            raise ValueError("access_token is required")
        account = self.get_account(access_token, provider=provider) or {}
        account_provider = normalize_provider(account.get("provider"))
        if account_provider == GROK_PROVIDER:
            return self.fetch_grok_remote_info(access_token, event, provider=account_provider)
        if account_provider != GPT_PROVIDER:
            return dict(account) if account else None
        from services.openai_backend_api import InvalidAccessTokenError, OpenAIBackendAPI

        try:
            with OpenAIBackendAPI(access_token) as backend:
                result = backend.get_user_info()
        except InvalidAccessTokenError:
            self.remove_invalid_token(access_token, event)
            raise
        return self.update_account(access_token, result, provider=account_provider)

    def fetch_grok_remote_info(self, access_token: str, event: str = "fetch_grok_remote_info", provider: str | None = None) -> dict[str, Any] | None:
        provider_filter = provider or GROK_PROVIDER
        account = self.get_account(access_token, provider=provider_filter) or {}
        from services.providers.grok import GrokConsoleError, validate_grok_access_token

        try:
            payload = validate_grok_access_token(access_token, account)
        except GrokConsoleError as exc:
            status = exc.upstream_status or exc.status_code
            if status in {401, 403} or any(marker in str(exc).lower() for marker in ("auth", "login", "session", "token")):
                self.remove_invalid_token(access_token, event)
            elif status in {402, 429}:
                self.update_account(access_token, {"status": "限流"}, provider=provider_filter)
            raise
        if account_strategy(GROK_PROVIDER).is_auth_failure_payload(payload):
            self.remove_invalid_token(access_token, event)
            raise RuntimeError("Grok app-chat authentication failed")
        return self.update_account(access_token, {"status": "正常", "app_chat": True}, provider=provider_filter)

    def _refresh_error_message(self, access_token: str, exc: Exception, provider: str | None = None) -> str:
        account = self.get_account(access_token, provider=provider) or {}
        return _account_strategy(account.get("provider")).refresh_error_message(exc)

    def refresh_accounts(self, access_tokens: list[str], provider: str | None = None) -> dict[str, Any]:
        access_tokens = list(dict.fromkeys(token for token in access_tokens if token))
        provider_filter = self._provider_filter(provider)
        with self._lock:
            if not access_tokens:
                access_tokens = self._list_account_tokens_locked(provider_filter)
            refresh_targets = [
                (token, account.get("provider"))
                for token in access_tokens
                if (account := self._get_account_locked(token, provider_filter))
                   and _account_strategy(account.get("provider")).supports_refresh(account)
            ]
        if not refresh_targets:
            return {"refreshed": 0, "errors": [], "items": self.list_accounts(provider_filter)}

        refreshed = 0
        errors = []
        max_workers = min(10, len(refresh_targets))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.fetch_remote_info, token, "refresh_accounts", target_provider): (token, target_provider)
                for token, target_provider in refresh_targets
            }
            for future in as_completed(futures):
                token, target_provider = futures[future]
                try:
                    account = future.result()
                except Exception as exc:
                    errors.append({"token": anonymize_token(token), "error": self._refresh_error_message(token, exc, target_provider)})
                    continue
                if account is not None:
                    refreshed += 1

        return {
            "refreshed": refreshed,
            "errors": errors,
            "items": self.list_accounts(provider_filter),
        }

    def build_export_items(self, access_tokens: list[str] | None = None, provider: str | None = None) -> list[dict[str, str]]:
        requested_tokens = [token for token in dict.fromkeys(access_tokens or []) if token]
        provider_filter = normalize_provider(provider) if provider else None
        if provider_filter not in {None, GPT_PROVIDER, GROK_PROVIDER, GEMINI_PROVIDER}:
            return []
        with self._lock:
            if requested_tokens:
                accounts = [
                    dict(account)
                    for token in requested_tokens
                    if (account := self._get_account_locked(token, provider_filter)) is not None
                ]
            else:
                accounts = self._list_account_items_locked(provider_filter)

        export_items: list[dict[str, str]] = []
        for account in accounts:
            if provider_filter is not None and normalize_provider(account.get("provider")) != provider_filter:
                continue
            export_item = _account_strategy(account.get("provider")).build_export_item(account)
            if export_item is not None:
                export_items.append(export_item)

        return export_items

    @staticmethod
    def build_export_text(items: list[dict[str, str]]) -> str:
        lines = [credential for item in items if (credential := _clean_string(item.get("access_token") or item.get("sso")))]
        return "\n".join(lines) + ("\n" if lines else "")


account_service = AccountService(config.get_storage_backend())
