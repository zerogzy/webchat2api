from __future__ import annotations

from typing import Mapping

from services.network.profiles import ChatGPTWebNetworkProfile, GrokConsoleNetworkProfile


def build_chatgpt_web_headers(
    profile: ChatGPTWebNetworkProfile,
    *,
    base_url: str,
    client_version: str,
    client_build_number: str,
) -> dict[str, str]:
    return {
        "User-Agent": profile.user_agent,
        "Origin": base_url,
        "Referer": base_url + "/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Priority": "u=1, i",
        "Sec-Ch-Ua": profile.sec_ch_ua,
        "Sec-Ch-Ua-Arch": '"x86"',
        "Sec-Ch-Ua-Bitness": '"64"',
        "Sec-Ch-Ua-Full-Version": '"143.0.3650.96"',
        "Sec-Ch-Ua-Full-Version-List": '"Microsoft Edge";v="143.0.3650.96", "Chromium";v="143.0.7499.147", "Not A(Brand";v="24.0.0.0"',
        "Sec-Ch-Ua-Mobile": profile.sec_ch_ua_mobile,
        "Sec-Ch-Ua-Model": '""',
        "Sec-Ch-Ua-Platform": profile.sec_ch_ua_platform,
        "Sec-Ch-Ua-Platform-Version": '"19.0.0"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "OAI-Device-Id": profile.oai_device_id,
        "OAI-Session-Id": profile.oai_session_id,
        "OAI-Language": "zh-CN",
        "OAI-Client-Version": client_version,
        "OAI-Client-Build-Number": client_build_number,
    }


def build_grok_console_headers(
    profile: GrokConsoleNetworkProfile,
    *,
    access_token: str,
    base_url: str,
) -> dict[str, str]:
    token = str(access_token or "").strip()
    headers = {
        "Content-Type": "application/json",
        "Origin": base_url,
        "Referer": f"{base_url}/",
        "User-Agent": profile.user_agent,
    }
    cookie_parts: list[str] = []
    if token:
        cookie_parts.append(token if "=" in token else f"sso={token}")
        headers["Authorization"] = f"Bearer {token}"
    if profile.cf_clearance:
        cookie_parts.append(f"cf_clearance={profile.cf_clearance}")
    if cookie_parts:
        headers["Cookie"] = "; ".join(cookie_parts)
    return headers


def merge_headers(base_headers: Mapping[str, str], extra: Mapping[str, str] | None = None) -> dict[str, str]:
    headers = dict(base_headers)
    if extra:
        headers.update(extra)
    return headers
