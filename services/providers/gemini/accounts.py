from __future__ import annotations

import hashlib
from typing import Any

EXPORT_FILENAME = "webchat2api_gemini.txt"
SECRET_KEYS = ("access_token", "accessToken", "cookies", "__Secure-1PSID", "__Secure-1PSIDTS", "SNlM0e", "session_token", "at")
SESSION_TOKEN_FIELDS = ("session_token", "SNlM0e", "at")
UNAVAILABLE_STATUSES = {"禁用", "异常", "限流"}
AUTH_FAILURE_MARKERS = (
    "auth",
    "login",
    "unauthorized",
    "forbidden",
    "snlm0e",
    "secure-1psid",
    "session expired",
    "invalid session",
    "expired session",
    "invalid token",
    "expired token",
    "missing token",
    "credential",
)


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().strip('"').strip("'").rstrip(";").strip()


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(cookie_header or "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = clean_string(value)
        if name:
            cookies[name] = value
    return cookies


def _cookie_field(item: dict[str, Any], name: str) -> str:
    cookies = item.get("cookies")
    if isinstance(cookies, dict):
        value = clean_string(cookies.get(name))
        if value:
            return value
    return clean_string(item.get(name))


def cookie_header(psid: str, psidts: str) -> str:
    parts = []
    if psid:
        parts.append(f"__Secure-1PSID={psid}")
    if psidts:
        parts.append(f"__Secure-1PSIDTS={psidts}")
    return "; ".join(parts)


def _merged_cookie_fields(item: dict[str, Any]) -> dict[str, str]:
    cookies = parse_cookie_header(clean_string(item.get("access_token") or item.get("accessToken")))
    stored_cookies = item.get("cookies")
    if isinstance(stored_cookies, dict):
        for name, value in stored_cookies.items():
            value_text = clean_string(value)
            if value_text:
                cookies[str(name)] = value_text
    return cookies


def _session_field(item: dict[str, Any], cookies: dict[str, str], name: str) -> str:
    return clean_string(item.get(name)) or clean_string(cookies.get(name))


def gemini_session_token(item: dict[str, Any]) -> str:
    cookies = _merged_cookie_fields(item)
    for name in SESSION_TOKEN_FIELDS:
        value = _session_field(item, cookies, name)
        if value:
            return value
    return ""


def account_category(item: dict[str, Any]) -> str:
    access_token, psid, psidts = normalize_account_credentials(dict(item))
    session_token = gemini_session_token(item)
    has_cookie_header = bool(parse_cookie_header(access_token))
    if psid and psidts and session_token:
        return "full_session"
    if psid and psidts:
        return "psid_psidts"
    if psid:
        return "psid_only"
    if session_token or has_cookie_header:
        return "session_token_only"
    return "missing_session"


def account_status(item: dict[str, Any]) -> str:
    category = account_category(item)
    if category == "missing_session":
        return "missing_gemini_session"
    if category == "session_token_only":
        return "missing_psid"
    return "usable_gemini_session"


def has_gemini_session(item: dict[str, Any]) -> bool:
    return account_category(item) != "missing_session"


def normalize_account_credentials(item: dict[str, Any]) -> tuple[str, str, str]:
    raw_cookies = _merged_cookie_fields(item)
    psid = clean_string(raw_cookies.get("__Secure-1PSID")) or _cookie_field(item, "__Secure-1PSID")
    psidts = clean_string(raw_cookies.get("__Secure-1PSIDTS")) or _cookie_field(item, "__Secure-1PSIDTS")
    access_token = clean_string(item.get("access_token") or item.get("accessToken"))
    if not access_token and psid:
        access_token = cookie_header(psid, psidts)
    return access_token, psid, psidts


def normalize_access_token(item: dict[str, Any]) -> str:
    return normalize_account_credentials(item)[0]


def normalize_account(account: dict[str, Any]) -> dict[str, Any]:
    access_token, psid, psidts = normalize_account_credentials(account)
    account["access_token"] = access_token
    account["__Secure-1PSID"] = psid
    account["__Secure-1PSIDTS"] = psidts
    cookies = _merged_cookie_fields(account)
    if psid:
        cookies["__Secure-1PSID"] = psid
    if psidts:
        cookies["__Secure-1PSIDTS"] = psidts
    for name in SESSION_TOKEN_FIELDS:
        value = clean_string(account.get(name)) or clean_string(cookies.get(name))
        if value:
            account[name] = value
    account["cookies"] = {name: value for name, value in cookies.items() if value}
    account["user_agent"] = clean_string(account.get("user_agent")) or None
    category = account_category(account)
    account["account_category"] = category
    account["account_status"] = account_status(account)
    account["has_gemini_session"] = category != "missing_session"
    return account


def delete_token_matches_account(token: str, account: dict[str, Any]) -> bool:
    token_access, token_psid, token_psidts = normalize_account_credentials({"access_token": token})
    account_access, account_psid, account_psidts = normalize_account_credentials(dict(account))
    return bool(
        (token_access and token_access == account_access)
        or (token_psid and token_psid == account_psid)
        or (token_psidts and token_psidts == account_psidts)
    )


def supports_refresh(account: dict[str, Any]) -> bool:
    return False


# Gemini session rotation is implemented in the provider client. The generic
# account refresh pipeline cannot currently persist those updates without
# touching shared account-service code, so Gemini must not advertise refresh
# support there.
def refresh_error_message(exc: Exception) -> str:
    message = clean_string(exc)
    return message or "Gemini session refresh failed"


def is_auth_failure_payload(payload: Any) -> bool:
    if isinstance(payload, dict):
        status = payload.get("status") or payload.get("status_code") or payload.get("code")
        status_text = clean_string(status)
        try:
            if status_text and int(status_text) in {401, 403}:
                return True
        except (TypeError, ValueError):
            pass
        for key in ("error", "message", "detail", "code", "reason", "error_description"):
            text = clean_string(payload.get(key)).lower()
            if any(marker in text for marker in AUTH_FAILURE_MARKERS):
                return True
        return any(is_auth_failure_payload(value) for value in payload.values())
    if isinstance(payload, (list, tuple, set)):
        return any(is_auth_failure_payload(value) for value in payload)
    return False


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
    return account_category(account) != "missing_session"


def tier_matches(account_tier: str, requested_tier: str) -> bool:
    return False


def normalize_tier(value: Any) -> str:
    return clean_string(value).lower()


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


def account_row_id(account: dict[str, Any]) -> str:
    access_token, psid, psidts = normalize_account_credentials(dict(account))
    source = "\0".join([access_token, psid, psidts, clean_string(account.get("account_id"))])
    if not source.strip("\0"):
        return ""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def sanitize_account(item: dict[str, Any]) -> dict[str, Any]:
    account = dict(item)
    row_id = account_row_id(item)
    for key in SECRET_KEYS:
        account.pop(key, None)
    if row_id:
        account["row_id"] = row_id
    category = account_category(item)
    account["has_gemini_session"] = category != "missing_session"
    account["account_category"] = category
    account["account_status"] = account_status(item)
    return account
