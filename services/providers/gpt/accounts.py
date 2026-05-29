from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from services.models import GPT_PROVIDER, normalize_provider

EXPORT_TIMEZONE = timezone(timedelta(hours=8))
UNAVAILABLE_IMAGE_STATUSES = {"禁用", "限流", "异常"}
EXPORT_FILENAME = "webchat2api-gpt.txt"


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_access_token(item: dict[str, Any]) -> str:
    return clean_string(item.get("access_token") or item.get("accessToken") or "")


def is_account(account: dict[str, Any]) -> bool:
    return normalize_provider(account.get("provider")) == GPT_PROVIDER


def is_image_account_available(account: dict[str, Any]) -> bool:
    if not isinstance(account, dict):
        return False
    if not is_account(account):
        return False
    if account.get("status") in UNAVAILABLE_IMAGE_STATUSES:
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
    return dict(item)
