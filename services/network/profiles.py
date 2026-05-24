from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping
from uuid import uuid4


def _new_uuid() -> str:
    return str(uuid4())

CHATGPT_WEB_FINGERPRINT_KEYS = (
    "user-agent",
    "impersonate",
    "oai-device-id",
    "oai-session-id",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
)

DEFAULT_CHATGPT_WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)
DEFAULT_CHATGPT_WEB_IMPERSONATE = "edge101"
DEFAULT_CHATGPT_WEB_SEC_CH_UA = '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"'
DEFAULT_CHATGPT_WEB_SEC_CH_UA_MOBILE = "?0"
DEFAULT_CHATGPT_WEB_SEC_CH_UA_PLATFORM = '"Windows"'

DEFAULT_GROK_CONSOLE_IMPERSONATE = "edge101"
DEFAULT_GROK_CONSOLE_USER_AGENT = "Mozilla/5.0 (webchat2api grok console)"
DEFAULT_GROK_CONSOLE_VERIFY = True
DEFAULT_GROK_CONSOLE_TIMEOUT = 60
DEFAULT_GROK_APP_CHAT_IMPERSONATE = "chrome136"
DEFAULT_GROK_APP_CHAT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
DEFAULT_GROK_APP_CHAT_VERIFY = True
DEFAULT_GROK_APP_CHAT_TIMEOUT = 60
DEFAULT_GROK_APP_CHAT_STATSIG_ID = "0196a8f6-0501-79f8-8d74-a2f2c0f5f5f5"
DEFAULT_GROK_APP_CHAT_SEC_CH_UA = '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"'
DEFAULT_GROK_APP_CHAT_SEC_CH_UA_MOBILE = "?0"
DEFAULT_GROK_APP_CHAT_SEC_CH_UA_PLATFORM = '"Windows"'


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _clean_bool(value: object, default: bool) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _clean_timeout(value: object, default: float | int) -> float | int:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return default
    if timeout <= 0:
        return default
    return int(timeout) if timeout.is_integer() else timeout


def _major_version(value: str) -> str:
    match = re.search(r"(?:Chrome|Chromium|CriOS|Edg|OPR|Version|Firefox)/(\d+)", value)
    return match.group(1) if match else ""


def infer_chromium_impersonate(user_agent: str, browser: str = "") -> str:
    source = f"{browser} {user_agent}".lower()
    if not any(token in source for token in ("chrome", "chromium", "edg", "crios", "opr")):
        return ""
    version = _major_version(user_agent) or _major_version(browser)
    if not version:
        return ""
    if "edg" in source and "chrome" not in str(browser).lower():
        return f"edge{version}"
    return f"chrome{version}"


def _ua_platform(user_agent: str) -> str:
    lowered = user_agent.lower()
    if "windows" in lowered:
        return '"Windows"'
    if "android" in lowered:
        return '"Android"'
    if any(token in lowered for token in ("iphone", "ipad", "ipod")):
        return '"iOS"'
    if any(token in lowered for token in ("macintosh", "mac os x")):
        return '"macOS"'
    if "linux" in lowered:
        return '"Linux"'
    return DEFAULT_GROK_APP_CHAT_SEC_CH_UA_PLATFORM


def derive_client_hints(user_agent: str, browser: str = "") -> tuple[str, str, str]:
    ua = _clean_text(user_agent) or DEFAULT_GROK_APP_CHAT_USER_AGENT
    browser_text = _clean_text(browser)
    source = f"{browser_text} {ua}".lower()
    version = _major_version(ua) or _major_version(browser_text)
    mobile = "?1" if re.search(r"Mobile|Android|iPhone|iPad|iPod", ua) else "?0"
    platform = _ua_platform(ua)
    if not version:
        return DEFAULT_GROK_APP_CHAT_SEC_CH_UA, mobile, platform
    if "edg" in source:
        sec_ch_ua = f'"Microsoft Edge";v="{version}", "Chromium";v="{version}", "Not A(Brand";v="24"'
    elif any(token in source for token in ("chrome", "chromium", "crios", "opr")):
        sec_ch_ua = f'"Chromium";v="{version}", "Google Chrome";v="{version}", "Not.A/Brand";v="99"'
    else:
        sec_ch_ua = DEFAULT_GROK_APP_CHAT_SEC_CH_UA
    return sec_ch_ua, mobile, platform


@dataclass(frozen=True)
class ChatGPTWebNetworkProfile:
    user_agent: str
    impersonate: str
    oai_device_id: str
    oai_session_id: str
    sec_ch_ua: str
    sec_ch_ua_mobile: str
    sec_ch_ua_platform: str
    verify: bool = True

    def as_fingerprint(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "impersonate": self.impersonate,
            "oai-device-id": self.oai_device_id,
            "oai-session-id": self.oai_session_id,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": self.sec_ch_ua_mobile,
            "sec-ch-ua-platform": self.sec_ch_ua_platform,
        }


@dataclass(frozen=True)
class GrokConsoleNetworkProfile:
    user_agent: str = DEFAULT_GROK_CONSOLE_USER_AGENT
    impersonate: str = DEFAULT_GROK_CONSOLE_IMPERSONATE
    verify: bool = DEFAULT_GROK_CONSOLE_VERIFY
    timeout: float | int = DEFAULT_GROK_CONSOLE_TIMEOUT
    cf_clearance: str = ""


@dataclass(frozen=True)
class GrokAppChatNetworkProfile:
    user_agent: str = DEFAULT_GROK_APP_CHAT_USER_AGENT
    impersonate: str = DEFAULT_GROK_APP_CHAT_IMPERSONATE
    verify: bool = DEFAULT_GROK_APP_CHAT_VERIFY
    timeout: float | int = DEFAULT_GROK_APP_CHAT_TIMEOUT
    cf_cookies: str = ""
    cf_clearance: str = ""
    sec_ch_ua: str = DEFAULT_GROK_APP_CHAT_SEC_CH_UA
    sec_ch_ua_mobile: str = DEFAULT_GROK_APP_CHAT_SEC_CH_UA_MOBILE
    sec_ch_ua_platform: str = DEFAULT_GROK_APP_CHAT_SEC_CH_UA_PLATFORM
    statsig_id: str = DEFAULT_GROK_APP_CHAT_STATSIG_ID


def _fingerprint_values(source: Mapping[str, object]) -> dict[str, str]:
    return {key: value for key in CHATGPT_WEB_FINGERPRINT_KEYS if (value := _clean_text(source.get(key)))}


def build_chatgpt_web_profile(
    account: Mapping[str, object] | None,
    global_fingerprint: Mapping[str, object] | None = None,
) -> ChatGPTWebNetworkProfile:
    source = account if isinstance(account, Mapping) else {}
    fp = _fingerprint_values(global_fingerprint) if isinstance(global_fingerprint, Mapping) else {}
    raw_fp = source.get("fp")
    if isinstance(raw_fp, Mapping):
        fp.update({str(key).lower(): str(value) for key, value in raw_fp.items()})
    fp.update(_fingerprint_values(source))
    return ChatGPTWebNetworkProfile(
        user_agent=_clean_text(fp.get("user-agent")) or DEFAULT_CHATGPT_WEB_USER_AGENT,
        impersonate=_clean_text(fp.get("impersonate")) or DEFAULT_CHATGPT_WEB_IMPERSONATE,
        oai_device_id=_clean_text(fp.get("oai-device-id")) or _new_uuid(),
        oai_session_id=_clean_text(fp.get("oai-session-id")) or _new_uuid(),
        sec_ch_ua=_clean_text(fp.get("sec-ch-ua")) or DEFAULT_CHATGPT_WEB_SEC_CH_UA,
        sec_ch_ua_mobile=_clean_text(fp.get("sec-ch-ua-mobile")) or DEFAULT_CHATGPT_WEB_SEC_CH_UA_MOBILE,
        sec_ch_ua_platform=_clean_text(fp.get("sec-ch-ua-platform")) or DEFAULT_CHATGPT_WEB_SEC_CH_UA_PLATFORM,
    )


def _profile_section(settings: Mapping[str, object], name: str) -> Mapping[str, object]:
    profiles = settings.get("network_profiles")
    if isinstance(profiles, Mapping):
        profile = profiles.get(name)
        if isinstance(profile, Mapping):
            return profile
    return {}


def build_grok_console_profile(settings: Mapping[str, object] | None = None) -> GrokConsoleNetworkProfile:
    source = settings if isinstance(settings, Mapping) else {}
    legacy = source.get("grok_console_fingerprint")
    legacy_source = legacy if isinstance(legacy, Mapping) else {}
    profile_source = _profile_section(source, "grok_console")
    merged = {**legacy_source, **profile_source}
    user_agent = (
        _clean_text(profile_source.get("user-agent") or profile_source.get("user_agent"))
        or _clean_text(legacy_source.get("user-agent") or legacy_source.get("user_agent"))
        or DEFAULT_GROK_CONSOLE_USER_AGENT
    )
    impersonate = _clean_text(merged.get("impersonate")) or DEFAULT_GROK_CONSOLE_IMPERSONATE
    verify = _clean_bool(merged.get("verify"), DEFAULT_GROK_CONSOLE_VERIFY)
    timeout = _clean_timeout(merged.get("timeout"), DEFAULT_GROK_CONSOLE_TIMEOUT)
    cf_clearance = _clean_text(merged.get("cf_clearance"))
    return GrokConsoleNetworkProfile(
        user_agent=user_agent,
        impersonate=impersonate,
        verify=verify,
        timeout=timeout,
        cf_clearance=cf_clearance,
    )


def build_grok_app_chat_profile(settings: Mapping[str, object] | None = None) -> GrokAppChatNetworkProfile:
    source = settings if isinstance(settings, Mapping) else {}
    console_profile = _profile_section(source, "grok_console")
    app_profile = _profile_section(source, "grok_app_chat")
    user_agent = (
        _clean_text(app_profile.get("user-agent") or app_profile.get("user_agent"))
        or _clean_text(console_profile.get("user-agent") or console_profile.get("user_agent"))
        or DEFAULT_GROK_APP_CHAT_USER_AGENT
    )
    explicit_impersonate = _clean_text(app_profile.get("impersonate") or app_profile.get("browser"))
    fallback_impersonate = _clean_text(console_profile.get("impersonate") or console_profile.get("browser"))
    impersonate = explicit_impersonate or fallback_impersonate or infer_chromium_impersonate(user_agent) or DEFAULT_GROK_APP_CHAT_IMPERSONATE
    verify = _clean_bool(app_profile.get("verify", console_profile.get("verify")), DEFAULT_GROK_APP_CHAT_VERIFY)
    timeout = _clean_timeout(app_profile.get("timeout", console_profile.get("timeout")), DEFAULT_GROK_APP_CHAT_TIMEOUT)
    cf_cookies = _clean_text(app_profile.get("cf_cookies"))
    cf_clearance = _clean_text(app_profile.get("cf_clearance")) or _clean_text(console_profile.get("cf_clearance"))
    derived_sec_ch_ua, derived_sec_ch_ua_mobile, derived_sec_ch_ua_platform = derive_client_hints(user_agent, impersonate)
    sec_ch_ua = _clean_text(app_profile.get("sec-ch-ua") or app_profile.get("sec_ch_ua")) or derived_sec_ch_ua
    sec_ch_ua_mobile = (
        _clean_text(app_profile.get("sec-ch-ua-mobile") or app_profile.get("sec_ch_ua_mobile"))
        or derived_sec_ch_ua_mobile
    )
    sec_ch_ua_platform = (
        _clean_text(app_profile.get("sec-ch-ua-platform") or app_profile.get("sec_ch_ua_platform"))
        or derived_sec_ch_ua_platform
    )
    statsig_id = _clean_text(app_profile.get("statsig_id") or app_profile.get("x-statsig-id")) or DEFAULT_GROK_APP_CHAT_STATSIG_ID
    return GrokAppChatNetworkProfile(
        user_agent=user_agent,
        impersonate=impersonate,
        verify=verify,
        timeout=timeout,
        cf_cookies=cf_cookies,
        cf_clearance=cf_clearance,
        sec_ch_ua=sec_ch_ua,
        sec_ch_ua_mobile=sec_ch_ua_mobile,
        sec_ch_ua_platform=sec_ch_ua_platform,
        statsig_id=statsig_id,
    )
