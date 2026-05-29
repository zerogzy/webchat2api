from __future__ import annotations

import re
from typing import Any

from services.providers.base import ModelSpec

TIER_ALIASES = {
    "free": "basic",
    "basic": "basic",
    "premium": "super",
    "super": "super",
    "heavy": "heavy",
}
UNAVAILABLE_STATUSES = {"禁用", "异常", "限流", "disabled", "abnormal", "limited"}
CONSOLE_QUOTA_TOTAL = 30
CONSOLE_QUOTA_WINDOW_SECONDS = 900
EXPORT_FILENAME = "webchat2api_grok.txt"


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_tier(value: Any) -> str:
    return TIER_ALIASES.get(str(value or "").strip().lower().replace("_", "-"), "")


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    return [item for item in (clean_string(raw).lower() for raw in raw_items) if item]


def normalize_access_token(item: dict[str, Any]) -> str:
    token = clean_string(item.get("access_token") or item.get("accessToken") or "")
    simple_sso = re.fullmatch(r"sso\s*=\s*(.+)", token, flags=re.IGNORECASE)
    if simple_sso and ";" not in token:
        return simple_sso.group(1).strip()
    return token


def normalize_console_quota(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    total = raw.get("total")
    try:
        total_value = int(total if total is not None else CONSOLE_QUOTA_TOTAL)
    except (TypeError, ValueError):
        total_value = CONSOLE_QUOTA_TOTAL
    total_value = max(0, total_value)

    window_seconds = raw.get("window_seconds")
    try:
        window_value = int(window_seconds if window_seconds is not None else CONSOLE_QUOTA_WINDOW_SECONDS)
    except (TypeError, ValueError):
        window_value = CONSOLE_QUOTA_WINDOW_SECONDS
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


def normalize_account(account: dict[str, Any]) -> dict[str, Any]:
    raw_tier = account.get("tier") or account.get("model_tier")
    normalized_tier = normalize_tier(raw_tier) or clean_string(raw_tier) or None
    account["tier"] = normalized_tier
    if "model_tier" in account:
        account["model_tier"] = normalized_tier
    account["app_chat"] = bool(account.get("app_chat"))
    account["quota_console"] = normalize_console_quota(account.get("quota_console"))
    account["capabilities"] = normalize_string_list(account.get("capabilities"))
    cf_cookies = account.get("cf_cookies")
    account["cf_cookies"] = cf_cookies if isinstance(cf_cookies, dict) else clean_string(cf_cookies)
    account["user_agent"] = clean_string(account.get("user_agent")) or None
    return account


def reset_console_quota_if_ready(account: dict[str, Any], current_time: float) -> dict[str, Any]:
    next_account = dict(account)
    quota = normalize_console_quota(next_account.get("quota_console"))
    reset_at = quota.get("reset_at")
    if reset_at is not None and int(reset_at) <= int(current_time):
        quota["remaining"] = quota["total"]
        quota["reset_at"] = None
    next_account["quota_console"] = quota
    return next_account


def is_console_account_available(account: dict[str, Any], current_time: float) -> bool:
    if not isinstance(account, dict):
        return False
    if account.get("status") in UNAVAILABLE_STATUSES:
        return False
    quota = reset_console_quota_if_ready(account, current_time).get("quota_console") or {}
    return int(quota.get("remaining") or 0) > 0


def tier_matches(account_tier: str, requested_tier: str) -> bool:
    if not account_tier or not requested_tier:
        return False
    if requested_tier == "heavy":
        return account_tier == "heavy"
    if requested_tier == "super":
        return account_tier in {"super", "heavy"}
    if requested_tier == "basic":
        return account_tier in {"basic", "super", "heavy"}
    return False


def requested_tiers(spec: ModelSpec) -> list[str]:
    if spec.prefer_best:
        return ["heavy", "super", "basic"]
    requested = normalize_tier(spec.model_tier)
    return [requested] if requested else []


def account_has_capability(account: dict[str, Any], spec: ModelSpec) -> bool:
    capabilities = set(account.get("capabilities") or [])
    if not capabilities:
        return True
    requested = {str(spec.capability or "chat").lower()}
    if spec.mode_id:
        requested.add(str(spec.mode_id).lower())
    if spec.model_tier:
        normalized_tier = normalize_tier(spec.model_tier)
        requested.add(normalized_tier or str(spec.model_tier).lower())
    return bool(capabilities & requested)


def is_auth_failure_payload(payload: Any) -> bool:
    if isinstance(payload, dict):
        for key in ("error", "message", "detail", "code", "reason"):
            text = clean_string(payload.get(key)).lower()
            if any(marker in text for marker in ("auth", "login", "session", "token", "unauthorized", "forbidden")):
                return True
        return any(is_auth_failure_payload(value) for value in payload.values())
    if isinstance(payload, (list, tuple, set)):
        return any(is_auth_failure_payload(value) for value in payload)
    return False


def supports_refresh(account: dict[str, Any]) -> bool:
    return True


def refresh_error_message(exc: Exception) -> str:
    return "Grok app-chat rate-limit validation failed"


def export_filename() -> str:
    return EXPORT_FILENAME


def build_export_item(account: dict[str, Any]) -> dict[str, str] | None:
    access_token = clean_string(account.get("access_token"))
    if not access_token:
        return None
    return {
        "type": clean_string(account.get("export_type")) or "codex",
        "email": clean_string(account.get("email")),
        "expired": clean_string(account.get("expired")),
        "id_token": clean_string(account.get("id_token")),
        "account_id": clean_string(account.get("account_id")),
        "access_token": access_token,
        "sso": clean_string(account.get("sso")),
        "last_refresh": clean_string(account.get("last_refresh")),
        "refresh_token": clean_string(account.get("refresh_token")),
    }


def sanitize_account(item: dict[str, Any]) -> dict[str, Any]:
    return dict(item)
