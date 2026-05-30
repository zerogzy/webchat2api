from __future__ import annotations

from typing import Any

EXPORT_FILENAME = "webchat2api_gemini.txt"
SECRET_KEYS = ("access_token", "cookies", "__Secure-1PSID", "__Secure-1PSIDTS")


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(cookie_header or "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
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
    return f"__Secure-1PSID={psid}; __Secure-1PSIDTS={psidts}"


def normalize_account_credentials(item: dict[str, Any]) -> tuple[str, str, str]:
    raw_cookies = parse_cookie_header(clean_string(item.get("access_token") or item.get("accessToken")))
    stored_cookies = item.get("cookies")
    if isinstance(stored_cookies, dict):
        for name, value in stored_cookies.items():
            value_text = clean_string(value)
            if value_text:
                raw_cookies[str(name)] = value_text
    psid = clean_string(raw_cookies.get("__Secure-1PSID")) or _cookie_field(item, "__Secure-1PSID")
    psidts = clean_string(raw_cookies.get("__Secure-1PSIDTS")) or _cookie_field(item, "__Secure-1PSIDTS")
    access_token = clean_string(item.get("access_token") or item.get("accessToken"))
    if not access_token and psid and psidts:
        access_token = cookie_header(psid, psidts)
    return access_token, psid, psidts


def normalize_access_token(item: dict[str, Any]) -> str:
    return normalize_account_credentials(item)[0]


def normalize_account(account: dict[str, Any]) -> dict[str, Any]:
    access_token, psid, psidts = normalize_account_credentials(account)
    account["access_token"] = access_token
    account["__Secure-1PSID"] = psid
    account["__Secure-1PSIDTS"] = psidts
    cookies = parse_cookie_header(access_token)
    cookies["__Secure-1PSID"] = psid
    cookies["__Secure-1PSIDTS"] = psidts
    account["cookies"] = {name: value for name, value in cookies.items() if value}
    account["user_agent"] = clean_string(account.get("user_agent")) or None
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
    account = dict(item)
    for key in SECRET_KEYS:
        account.pop(key, None)
    account["has_gemini_session"] = bool(
        item.get("access_token")
        or item.get("__Secure-1PSID")
        or item.get("__Secure-1PSIDTS")
        or item.get("cookies")
    )
    return account
