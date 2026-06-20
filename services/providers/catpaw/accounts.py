"""CatPaw AccountAdapter — multi-account store integration.

CatPaw accounts are imported via QR login and live in the shared account store
(data/accounts.json), one record per logged-in user. The identity key is a STABLE
id (mis id / login name) because the CatPaw access token rotates on every refresh;
the rotating chat credential is kept in `catpaw_access_token` and the refresh token
in `refresh_token`, so token renewal updates those fields without re-keying the row.
"""
from __future__ import annotations

import hashlib
from typing import Any

UNAVAILABLE_STATUSES = {"禁用", "异常", "限流", "disabled", "unauthorized", "rate_limited", "expired"}

AUTH_FAILURE_MARKERS = ("auth failed", "unauthorized", "token_expired", "invalid token", "expired token", "登录")

SECRET_KEYS = ("catpaw_access_token", "refresh_token", "accessToken", "refreshToken", "token")


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def catpaw_identity(item: dict[str, Any]) -> str:
    """Stable identity for a CatPaw account (never the rotating access token)."""
    return _clean(
        item.get("catpaw_id")
        or item.get("mis_id")
        or item.get("login_name")
        or item.get("account_id")
        or item.get("access_token")
    )


def normalize_access_token(item: dict[str, Any]) -> str:
    return catpaw_identity(item)


def normalize_account(account: dict[str, Any]) -> dict[str, Any]:
    identity = catpaw_identity(account)
    account["catpaw_id"] = identity
    account["account_id"] = account.get("account_id") or identity
    account["type"] = "catpaw"
    account.setdefault("status", "正常")
    account["login_name"] = account.get("login_name") or None
    account["mis_id"] = account.get("mis_id") or None
    account["email"] = account.get("email") or None
    account["catpaw_access_token"] = _clean(account.get("catpaw_access_token") or account.get("accessToken"))
    account["refresh_token"] = _clean(account.get("refresh_token") or account.get("refreshToken"))
    account.pop("accessToken", None)
    account.pop("refreshToken", None)
    return account


def account_row_id(account: dict[str, Any]) -> str:
    identity = catpaw_identity(account)
    if not identity:
        return ""
    return hashlib.sha256(f"catpaw\0{identity}".encode("utf-8")).hexdigest()


def sanitize_account(item: dict[str, Any]) -> dict[str, Any]:
    account = dict(item)
    for key in SECRET_KEYS:
        account.pop(key, None)
    account["has_access_token"] = bool(_clean(item.get("catpaw_access_token")))
    account["has_refresh_token"] = bool(_clean(item.get("refresh_token")))
    row_id = account_row_id(item)
    if row_id:
        account["row_id"] = row_id
    account["login_name"] = item.get("login_name") or None
    account["mis_id"] = item.get("mis_id") or None
    account["email"] = item.get("email") or None
    account["expires"] = item.get("expires")
    account["refresh_expires"] = item.get("refresh_expires")
    account["catpaw_quota"] = item.get("catpaw_quota") if isinstance(item.get("catpaw_quota"), dict) else None
    return account


def delete_token_matches_account(token: str, account: dict[str, Any]) -> bool:
    return _clean(token) == catpaw_identity(account)


def supports_refresh(account: dict[str, Any]) -> bool:
    return bool(_clean(account.get("refresh_token")))


def validate_remote_info(access_token: str, account: dict[str, Any] | None = None) -> dict[str, Any]:
    from services.providers.catpaw import client as catpaw_client

    source = dict(account or {})
    refresh = _clean(source.get("refresh_token") or source.get("refreshToken"))
    if not refresh:
        raise RuntimeError("CatPaw 账号缺少 refresh token，请重新扫码登录")
    data = catpaw_client.refresh_token_value(refresh)
    if not data or not data.get("accessToken"):
        raise RuntimeError("CatPaw token 续期失败，refresh token 可能已过期，请重新扫码登录")
    return {
        "catpaw_access_token": _clean(data.get("accessToken")),
        "refresh_token": _clean(data.get("refreshToken")) or refresh,
        "expires": data.get("expires"),
        "refresh_expires": data.get("refreshExpires"),
        "status": "正常",
    }


def remote_error_status(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None)


def refresh_error_message(exc: Exception) -> str:
    return _clean(exc) or "CatPaw 账号续期失败"


def export_filename() -> str:
    return "webchat2api_catpaw.txt"


def build_export_item(account: dict[str, Any]) -> dict[str, str] | None:
    refresh = _clean(account.get("refresh_token"))
    if not refresh:
        return None
    return {
        "catpaw_id": catpaw_identity(account),
        "login_name": _clean(account.get("login_name")),
        "mis_id": _clean(account.get("mis_id")),
        "refresh_token": refresh,
    }


def is_auth_failure_payload(payload: Any) -> bool:
    if isinstance(payload, dict):
        for value in payload.values():
            if is_auth_failure_payload(value):
                return True
        return False
    if isinstance(payload, (list, tuple, set)):
        return any(is_auth_failure_payload(value) for value in payload)
    text = _clean(payload).lower()
    return any(marker in text for marker in AUTH_FAILURE_MARKERS)


# --- chat-only provider: trivial console/tier/image stubs (mirror gemini) ---
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
    return bool(_clean(account.get("catpaw_access_token")))


def tier_matches(account_tier: str, requested_tier: str) -> bool:
    return False


def normalize_tier(value: Any) -> str:
    return _clean(value).lower()
