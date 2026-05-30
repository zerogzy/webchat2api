from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from services.models import GPT_PROVIDER, normalize_provider
from services.providers.base import ModelSpec

EXPORT_TIMEZONE = timezone(timedelta(hours=8))
UNAVAILABLE_STATUSES = {"禁用", "限流", "异常", "disabled", "limited", "abnormal"}
UNAVAILABLE_IMAGE_STATUSES = UNAVAILABLE_STATUSES
EXPORT_FILENAME = "webchat2api-gpt.txt"
DEFAULT_CONSOLE_QUOTA_TOTAL = 0
DEFAULT_CONSOLE_QUOTA_WINDOW_SECONDS = 0

TIER_ALIASES = {
    "": "",
    "free": "free",
    "basic": "free",
    "plus": "plus",
    "pluslite": "plus",
    "plus-lite": "plus",
    "team": "team",
    "business": "team",
    "enterprise": "enterprise",
    "pro": "pro",
    "prolite": "pro",
    "pro-lite": "pro",
}
CAPABILITY_ALIASES = {
    "": "",
    "app-chat": "chat",
    "text": "chat",
    "text-chat": "chat",
    "image-generation": "image",
    "image-gen": "image",
    "images": "image",
    "image-editing": "image_edit",
    "image-edit": "image_edit",
    "edit-image": "image_edit",
}
STATUS_ALIASES = {
    "ok": "正常",
    "normal": "正常",
    "active": "正常",
    "enabled": "正常",
    "disabled": "禁用",
    "disable": "禁用",
    "abnormal": "异常",
    "auth_failed": "异常",
    "auth-failed": "异常",
    "unauthorized": "异常",
    "forbidden": "异常",
    "limited": "限流",
    "rate_limited": "限流",
    "rate-limited": "限流",
    "quota_exhausted": "限流",
    "quota-exhausted": "限流",
}
SECRET_EXPORT_KEYS = {"id_token", "refresh_token", "sso"}


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _alias_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", clean_string(value).lower()).strip("-")


def normalize_tier(value: Any) -> str:
    return TIER_ALIASES.get(_alias_key(value), "")


def normalize_capability(value: Any) -> str:
    key = _alias_key(value)
    if not key:
        return ""
    return CAPABILITY_ALIASES.get(key, key.replace("-", "_"))


def normalize_status(value: Any) -> Any:
    text = clean_string(value)
    if not text:
        return value
    return STATUS_ALIASES.get(_alias_key(text), text)


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    return [item for item in (normalize_capability(raw) for raw in raw_items) if item]


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def normalize_access_token(item: dict[str, Any]) -> str:
    return clean_string(item.get("access_token") or item.get("accessToken") or "")


def normalize_console_quota(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    total = max(0, _coerce_int(raw.get("total"), DEFAULT_CONSOLE_QUOTA_TOTAL))
    window_seconds = max(0, _coerce_int(raw.get("window_seconds"), DEFAULT_CONSOLE_QUOTA_WINDOW_SECONDS))
    remaining = min(total, max(0, _coerce_int(raw.get("remaining"), total)))
    reset_at = raw.get("reset_at") or raw.get("resets_at") or raw.get("restore_at")
    reset_at_value = _coerce_int(reset_at, 0) if reset_at is not None else None

    return {
        "remaining": remaining,
        "total": total,
        "window_seconds": window_seconds,
        "reset_at": reset_at_value,
    }


def normalize_account(account: dict[str, Any]) -> dict[str, Any]:
    raw_tier = account.get("tier") or account.get("model_tier") or account.get("type")
    normalized_tier = normalize_tier(raw_tier) or clean_string(raw_tier) or None
    account["tier"] = normalized_tier
    if "model_tier" in account:
        account["model_tier"] = normalized_tier
    account["status"] = normalize_status(account.get("status")) or account.get("status")
    quota = account.get("quota_console")
    if quota is not None or "quota_console" in account:
        account["quota_console"] = normalize_console_quota(quota)
    account["capabilities"] = normalize_string_list(account.get("capabilities"))
    return account


def delete_token_matches_account(token: str, account: dict[str, Any]) -> bool:
    return clean_string(token) == clean_string(account.get("access_token"))


def reset_console_quota_if_ready(account: dict[str, Any], current_time: float) -> dict[str, Any]:
    next_account = dict(account)
    quota = normalize_console_quota(next_account.get("quota_console"))
    reset_at = quota.get("reset_at")
    if reset_at is not None and int(reset_at) <= int(current_time):
        quota["remaining"] = quota["total"]
        quota["reset_at"] = None
        if next_account.get("status") == "限流" and quota["remaining"] > 0:
            next_account["status"] = "正常"
    next_account["quota_console"] = quota
    return next_account


def is_console_account_available(account: dict[str, Any], current_time: float) -> bool:
    if not isinstance(account, dict):
        return False
    if account.get("status") in UNAVAILABLE_STATUSES:
        return False
    quota = reset_console_quota_if_ready(account, current_time).get("quota_console") or {}
    return int(quota.get("remaining") or 0) > 0


def requested_tiers(spec: ModelSpec) -> list[str]:
    requested = normalize_tier(spec.model_tier)
    return [requested] if requested else []


def account_has_capability(account: dict[str, Any], spec: ModelSpec) -> bool:
    if spec.provider != GPT_PROVIDER or spec.capability not in {"chat", "image", "image_edit"}:
        return False
    capabilities = set(normalize_string_list(account.get("capabilities")))
    if not capabilities:
        return True
    return normalize_capability(spec.capability or "chat") in capabilities


def tier_matches(account_tier: str, requested_tier: str) -> bool:
    account_value = normalize_tier(account_tier) or clean_string(account_tier).lower()
    requested_value = normalize_tier(requested_tier) or clean_string(requested_tier).lower()
    if not account_value or not requested_value:
        return False
    if requested_value == "pro":
        return account_value == "pro"
    if requested_value == "team":
        return account_value in {"team", "enterprise", "pro"}
    if requested_value == "plus":
        return account_value in {"plus", "team", "enterprise", "pro"}
    if requested_value == "free":
        return account_value in {"free", "plus", "team", "enterprise", "pro"}
    return account_value == requested_value


def is_auth_failure_payload(payload: Any) -> bool:
    if isinstance(payload, dict):
        status = payload.get("status") or payload.get("status_code") or payload.get("code")
        if _coerce_int(status, 0) in {401, 403}:
            return True
        for key in ("error", "message", "detail", "code", "reason", "error_description"):
            text = clean_string(payload.get(key)).lower()
            if any(marker in text for marker in ("invalid token", "expired token", "unauthorized", "forbidden", "login", "session", "auth")):
                return True
        return any(is_auth_failure_payload(value) for value in payload.values())
    if isinstance(payload, (list, tuple, set)):
        return any(is_auth_failure_payload(value) for value in payload)
    return False


def is_account(account: dict[str, Any]) -> bool:
    return normalize_provider(account.get("provider")) == GPT_PROVIDER


def is_image_account_available(account: dict[str, Any]) -> bool:
    if not isinstance(account, dict):
        return False
    if not is_account(account):
        return False
    if account.get("status") in UNAVAILABLE_STATUSES:
        return False
    if bool(account.get("image_quota_unknown")):
        return True
    return int(account.get("quota") or 0) > 0


def supports_refresh(account: dict[str, Any]) -> bool:
    return is_account(account)


def refresh_error_message(exc: Exception) -> str:
    return str(exc)


def export_filename() -> str:
    return EXPORT_FILENAME


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


def build_export_item(account: dict[str, Any]) -> dict[str, str] | None:
    access_token = clean_string(account.get("access_token"))
    if not access_token:
        return None

    id_token = clean_string(account.get("id_token"))
    refresh_token = clean_string(account.get("refresh_token"))
    access_claims = _decode_jwt_payload(access_token)
    id_claims = _decode_jwt_payload(id_token)
    access_auth = _nested_dict(access_claims.get("https://api.openai.com/auth"))
    id_auth = _nested_dict(id_claims.get("https://api.openai.com/auth"))
    profile = _nested_dict(access_claims.get("https://api.openai.com/profile"))

    email = (
        clean_string(account.get("email"))
        or clean_string(profile.get("email"))
        or clean_string(id_claims.get("email"))
    )
    account_id = (
        clean_string(account.get("account_id"))
        or clean_string(access_auth.get("chatgpt_account_id"))
        or clean_string(id_auth.get("chatgpt_account_id"))
    )
    expired = clean_string(account.get("expired")) or _format_timestamp(access_claims.get("exp"))
    last_refresh = (
        clean_string(account.get("last_refresh"))
        or _format_timestamp(access_claims.get("iat"))
        or _format_timestamp(access_claims.get("nbf"))
    )

    return {
        "type": clean_string(account.get("export_type")) or "codex",
        "email": email,
        "expired": expired,
        "id_token": id_token,
        "account_id": account_id,
        "access_token": access_token,
        "sso": clean_string(account.get("sso")),
        "last_refresh": last_refresh,
        "refresh_token": refresh_token,
    }


def sanitize_account(item: dict[str, Any]) -> dict[str, Any]:
    account = dict(item)
    for key in SECRET_EXPORT_KEYS:
        account.pop(key, None)
    account["has_refresh_token"] = bool(item.get("refresh_token"))
    account["has_id_token"] = bool(item.get("id_token"))
    account["has_sso"] = bool(item.get("sso"))
    return account
