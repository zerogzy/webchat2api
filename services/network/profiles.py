from __future__ import annotations

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
