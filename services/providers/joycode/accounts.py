from __future__ import annotations

import hashlib
from typing import Any

UNAVAILABLE_STATUSES = {"禁用", "异常", "限流", "disabled", "unauthorized", "rate_limited", "expired"}
AUTH_FAILURE_MARKERS = ("auth", "unauthorized", "forbidden", "invalid", "expired", "登录", "pt_key")
SECRET_KEYS = ("pt_key", "access_token", "anthropic_pt_key")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_access_token(item: dict[str, Any]) -> str:
    return _clean(item.get("user_id") or item.get("account_id") or item.get("access_token") or item.get("pt_key"))


def normalize_account(account: dict[str, Any]) -> dict[str, Any]:
    identity = normalize_access_token(account)
    pt_key = _clean(account.get("pt_key") or account.get("access_token"))
    account["access_token"] = identity
    account["account_id"] = _clean(account.get("account_id")) or identity
    account["user_id"] = _clean(account.get("user_id")) or identity
    account["pt_key"] = pt_key
    account["type"] = "joycode"
    account.setdefault("status", "正常")
    for key in ("login_type", "tenant", "color_base_url", "master_base_url", "org_full_name", "anthropic_pt_key", "default_model"):
        account[key] = _clean(account.get(key))
    return account


def account_row_id(account: dict[str, Any]) -> str:
    identity = normalize_access_token(account)
    return hashlib.sha256(f"joycode\0{identity}".encode()).hexdigest() if identity else ""


def sanitize_account(item: dict[str, Any]) -> dict[str, Any]:
    account = dict(item)
    for key in SECRET_KEYS:
        account.pop(key, None)
    account["has_pt_key"] = bool(_clean(item.get("pt_key")))
    account["has_anthropic_pt_key"] = bool(_clean(item.get("anthropic_pt_key")))
    if row_id := account_row_id(item):
        account["row_id"] = row_id
    return account


def delete_token_matches_account(token: str, account: dict[str, Any]) -> bool:
    text = _clean(token)
    return text in {normalize_access_token(account), _clean(account.get("pt_key")), _clean(account.get("user_id"))}


def supports_refresh(account: dict[str, Any]) -> bool:
    return bool(_clean(account.get("pt_key")))


def validate_remote_info(access_token: str, account: dict[str, Any] | None = None) -> dict[str, Any]:
    from services.providers.joycode.client import JoyCodeClient

    source = dict(account or {})
    if not source.get("pt_key"):
        source["pt_key"] = source.get("access_token") or access_token
    with JoyCodeClient(source, timeout=60) as client:
        payload, refreshed = client.user_info_with_refresh()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    updates: dict[str, Any] = {
        "status": "正常",
        "user_id": _clean(data.get("userId")) or _clean(source.get("user_id")) or access_token,
        "email": _clean(data.get("email")) or source.get("email"),
    }
    if name := _clean(data.get("realName")):
        updates["name"] = name
    if refreshed:
        updates["pt_key"] = refreshed
    return updates


def remote_error_status(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None)


def refresh_error_message(exc: Exception) -> str:
    return str(exc) or "JoyCode credential refresh failed"


def export_filename() -> str:
    return "webchat2api_joycode.txt"


def build_export_item(account: dict[str, Any]) -> dict[str, str] | None:
    pt_key = _clean(account.get("pt_key"))
    if not pt_key:
        return None
    return {
        "provider": "joycode",
        "user_id": _clean(account.get("user_id")),
        "pt_key": pt_key,
    }


def is_auth_failure_payload(payload: Any) -> bool:
    if isinstance(payload, dict):
        return any(is_auth_failure_payload(value) for value in payload.values())
    if isinstance(payload, (list, tuple, set)):
        return any(is_auth_failure_payload(value) for value in payload)
    text = _clean(payload).lower()
    return any(marker in text for marker in AUTH_FAILURE_MARKERS)


def is_image_account_available(account: dict[str, Any]) -> bool:
    return False


def normalize_console_quota(value: Any) -> dict[str, Any]:
    return {}


def reset_console_quota_if_ready(account: dict[str, Any], current_time: float) -> dict[str, Any]:
    return dict(account)


def is_console_account_available(account: dict[str, Any], current_time: float) -> bool:
    return False


def requested_tiers(spec: Any) -> list[str]:
    return []


def account_has_capability(account: dict[str, Any], spec: Any) -> bool:
    return bool(_clean(account.get("pt_key")))


def tier_matches(account_tier: str, requested_tier: str) -> bool:
    return False


def normalize_tier(value: Any) -> str:
    return _clean(value).lower()
