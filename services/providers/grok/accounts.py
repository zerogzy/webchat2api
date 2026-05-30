from __future__ import annotations

import re
from typing import Any

from services.providers.base import ModelSpec

TIER_ALIASES = {
    "free": "basic",
    "basic": "basic",
    "fast": "basic",
    "premium": "super",
    "super": "super",
    "supergrok": "super",
    "super-grok": "super",
    "premium-plus": "super",
    "premium+": "super",
    "heavy": "heavy",
    "max": "heavy",
}
CAPABILITY_ALIASES = {
    "": "",
    "app-chat": "chat",
    "app_chat": "chat",
    "text": "chat",
    "text-chat": "chat",
    "image-generation": "image",
    "image-gen": "image",
    "imagine": "image",
    "image-editing": "image_edit",
    "image-edit": "image_edit",
    "edit-image": "image_edit",
}
STATUS_ALIASES = {
    "disabled": "禁用",
    "disable": "禁用",
    "inactive": "禁用",
    "abnormal": "异常",
    "auth_failed": "异常",
    "auth-failed": "异常",
    "authentication_failed": "异常",
    "authentication-failed": "异常",
    "unauthenticated": "异常",
    "unauthorized": "异常",
    "forbidden": "异常",
    "expired": "异常",
    "token_expired": "异常",
    "token-expired": "异常",
    "invalid_token": "异常",
    "invalid-token": "异常",
    "limited": "限流",
    "rate_limited": "限流",
    "rate-limited": "限流",
    "rate_limit_exceeded": "限流",
    "rate-limit-exceeded": "限流",
    "quota_exhausted": "限流",
    "quota-exhausted": "限流",
}
UNAVAILABLE_STATUSES = {"禁用", "异常", "限流", "disabled", "abnormal", "limited", "rate_limited", "rate-limited"}
CONSOLE_QUOTA_TOTAL = 30
CONSOLE_QUOTA_WINDOW_SECONDS = 900
EXPORT_FILENAME = "webchat2api_grok.txt"


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_tier(value: Any) -> str:
    return TIER_ALIASES.get(str(value or "").strip().lower().replace("_", "-"), "")


def normalize_capability(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace(" ", "-")
    if not normalized:
        return ""
    return CAPABILITY_ALIASES.get(normalized, normalized.replace("-", "_"))


def normalize_status(value: Any) -> Any:
    text = clean_string(value)
    if not text:
        return value
    return STATUS_ALIASES.get(text.lower().replace(" ", "-"), text)


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    return [item for item in (normalize_capability(raw) for raw in raw_items) if item]


def _looks_like_existing_normalized_account(item: dict[str, Any], token: str) -> bool:
    if not token or "=" in token:
        return False
    return any(key in item for key in ("quota_console", "success", "fail", "last_used_at", "app_chat"))


def _token_candidates(item: dict[str, Any]) -> list[str]:
    candidates = [item.get("access_token"), item.get("accessToken"), item.get("cookie")]
    cookies = item.get("cookies")
    if isinstance(cookies, str):
        candidates.append(cookies)
    return [candidate for candidate in (clean_string(value) for value in candidates) if candidate]


def _normalize_sso_candidate(candidate: str, *, allow_bare: bool) -> str:
    if not candidate:
        return ""
    simple_sso = re.fullmatch(r"sso\s*=\s*(.+)", candidate, flags=re.IGNORECASE)
    if simple_sso and ";" not in candidate:
        return simple_sso.group(1).strip()
    if any(name.lower() == "sso" for name, _ in _cookie_items(candidate)):
        return candidate
    if allow_bare and "=" not in candidate:
        return candidate
    return ""


def normalize_access_token(item: dict[str, Any]) -> str:
    token = clean_string(item.get("access_token") or item.get("accessToken") or "")
    if item.get("_grok_sso_import") and token:
        return token
    if _looks_like_existing_normalized_account(item, token):
        return token
    explicit_sso = _normalize_sso_candidate(clean_string(item.get("sso")), allow_bare=True)
    if explicit_sso:
        item["_grok_sso_import"] = True
        return explicit_sso
    for candidate in _token_candidates(item):
        normalized = _normalize_sso_candidate(candidate, allow_bare=False)
        if normalized:
            item["_grok_sso_import"] = True
            return normalized
    return ""


def _cookie_items(cookie_header: Any) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for fragment in str(cookie_header or "").split(";"):
        name, separator, value = fragment.strip().partition("=")
        clean_name = " ".join(name.strip().split())
        clean_value = " ".join(value.strip().split())
        if separator and clean_name:
            items.append((clean_name, clean_value))
    return items


def _normalize_cookie_header(value: Any) -> str:
    cookies: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    for name, cookie_value in _cookie_items(value):
        normalized_name = name.lower()
        if normalized_name in seen:
            cookies[seen[normalized_name]] = (normalized_name, cookie_value)
            continue
        seen[normalized_name] = len(cookies)
        cookies.append((normalized_name, cookie_value))
    return "; ".join(f"{name}={cookie_value}" for name, cookie_value in cookies)


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
    account.pop("_grok_sso_import", None)
    raw_tier = account.get("tier") or account.get("model_tier")
    normalized_tier = normalize_tier(raw_tier) or clean_string(raw_tier) or None
    account["tier"] = normalized_tier
    if "model_tier" in account:
        account["model_tier"] = normalized_tier
    account["status"] = normalize_status(account.get("status")) or account.get("status")
    account["app_chat"] = bool(account.get("app_chat"))
    account["quota_console"] = normalize_console_quota(account.get("quota_console"))
    account["capabilities"] = normalize_string_list(account.get("capabilities"))
    cf_cookies = account.get("cf_cookies") or account.get("cfCookies") or account.get("cloudflare_cookies")
    account["cf_cookies"] = cf_cookies if isinstance(cf_cookies, dict) else _normalize_cookie_header(cf_cookies)
    account["cf_clearance"] = clean_string(account.get("cf_clearance") or account.get("cfClearance")) or None
    account["user_agent"] = clean_string(account.get("user_agent") or account.get("userAgent") or account.get("user-agent")) or None
    account["statsig_id"] = clean_string(account.get("statsig_id") or account.get("statsigId") or account.get("x-statsig-id")) or None
    account["sec_ch_ua"] = clean_string(account.get("sec_ch_ua") or account.get("sec-ch-ua")) or None
    account["sec_ch_ua_mobile"] = clean_string(account.get("sec_ch_ua_mobile") or account.get("sec-ch-ua-mobile")) or None
    account["sec_ch_ua_platform"] = clean_string(account.get("sec_ch_ua_platform") or account.get("sec-ch-ua-platform")) or None
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
    requested = {normalize_capability(spec.capability or "chat")}
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
