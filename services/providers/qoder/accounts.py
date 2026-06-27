from __future__ import annotations

import hashlib
from typing import Any

UNAVAILABLE_STATUSES = {"禁用", "异常", "限流", "disabled", "unauthorized", "rate_limited", "expired"}
AUTH_FAILURE_MARKERS = ("auth", "unauthorized", "forbidden", "invalid", "expired", "credential", "token")
SECRET_KEYS = ("pat_token", "access_token", "accessToken", "token")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_access_token(item: dict[str, Any]) -> str:
    return _clean(item.get("account_id") or item.get("user_id") or item.get("pat_token") or item.get("access_token") or item.get("token"))


def normalize_account(account: dict[str, Any]) -> dict[str, Any]:
    token = _clean(account.get("pat_token") or account.get("access_token") or account.get("token"))
    identity = _clean(account.get("account_id") or account.get("user_id"))
    if not identity and token:
        identity = "qoder:" + hashlib.sha256(token.encode()).hexdigest()[:16]
    account["access_token"] = identity
    account["account_id"] = _clean(account.get("account_id")) or identity
    account["user_id"] = _clean(account.get("user_id"))
    account["pat_token"] = token
    account["type"] = "qoder"
    account.setdefault("status", "正常")
    return account


def account_row_id(account: dict[str, Any]) -> str:
    identity = normalize_access_token(account)
    return hashlib.sha256(f"qoder\0{identity}".encode()).hexdigest() if identity else ""


def sanitize_account(item: dict[str, Any]) -> dict[str, Any]:
    account = dict(item)
    for key in SECRET_KEYS:
        account.pop(key, None)
    account["has_pat_token"] = bool(_clean(item.get("pat_token")))
    if row_id := account_row_id(item):
        account["row_id"] = row_id
    return account


def delete_token_matches_account(token: str, account: dict[str, Any]) -> bool:
    text = _clean(token)
    return text in {normalize_access_token(account), _clean(account.get("pat_token")), _clean(account.get("user_id"))}


def supports_refresh(account: dict[str, Any]) -> bool:
    return False


def validate_remote_info(access_token: str, account: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "正常"}


def remote_error_status(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None)


def refresh_error_message(exc: Exception) -> str:
    return str(exc) or "Qoder credential validation failed"


def export_filename() -> str:
    return "webchat2api_qoder.txt"


def build_export_item(account: dict[str, Any]) -> dict[str, str] | None:
    token = _clean(account.get("pat_token"))
    if not token:
        return None
    return {"provider": "qoder", "pat_token": token, "user_id": _clean(account.get("user_id"))}


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
    return bool(_clean(account.get("pat_token")))


def tier_matches(account_tier: str, requested_tier: str) -> bool:
    return False


def normalize_tier(value: Any) -> str:
    return _clean(value).lower()
