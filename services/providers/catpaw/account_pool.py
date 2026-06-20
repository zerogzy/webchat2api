from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.providers.base import CATPAW_PROVIDER
from services.providers.registry import normalize_provider
from utils.helper import anonymize_token


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def select_account_for_chat(service: Any, excluded_ids: set[str] | None = None) -> dict[str, Any] | None:
    excluded = set(excluded_ids or set())
    with service._lock:
        candidates = [
            dict(account)
            for account in service._all_accounts_locked()
            if normalize_provider(account.get("provider")) == CATPAW_PROVIDER
               and not service._state_decision(account).unavailable
               and (account.get("access_token") or "") not in excluded
               and _clean_string(account.get("catpaw_access_token"))
        ]
        if not candidates:
            return None
        account = candidates[service._index % len(candidates)]
        service._index += 1
    expires = account.get("expires")
    try:
        near_expiry = bool(expires) and int(datetime.now(timezone.utc).timestamp() * 1000) > int(expires) - 60000
    except (TypeError, ValueError):
        near_expiry = False
    if near_expiry:
        try:
            refreshed = service.fetch_catpaw_remote_info(account.get("access_token") or "")
            if refreshed:
                account = refreshed
        except Exception as exc:
            print(f"[catpaw] token refresh on select failed: {exc}")
    return account


def renew_due_accounts(service: Any, margin_seconds: int = 1800) -> int:
    now_ms_value = int(datetime.now(timezone.utc).timestamp() * 1000)
    with service._lock:
        due_tokens = [
            account.get("access_token") or ""
            for account in service._all_accounts_locked()
            if normalize_provider(account.get("provider")) == CATPAW_PROVIDER
               and _clean_string(account.get("refresh_token"))
               and isinstance(account.get("expires"), (int, float))
               and now_ms_value > int(account["expires"]) - margin_seconds * 1000
        ]
    refreshed = 0
    for token in due_tokens:
        if not token:
            continue
        try:
            if service.fetch_catpaw_remote_info(token):
                refreshed += 1
        except Exception as exc:
            print(f"[catpaw-renew] {anonymize_token(token)} failed: {exc}")
    return refreshed
