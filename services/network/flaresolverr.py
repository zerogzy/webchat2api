from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from curl_cffi import requests

from services.config import config


@dataclass(frozen=True)
class FlareSolverrClearance:
    user_agent: str
    cf_clearance: str
    cf_cookies: str


class FlareSolverrClearanceProvider:
    def __init__(self, flaresolverr_url: str | None = None, timeout_sec: int | None = None) -> None:
        self.flaresolverr_url = str(flaresolverr_url or config.flaresolverr_url).strip().rstrip("/")
        self.timeout_sec = int(timeout_sec if timeout_sec is not None else config.flaresolverr_timeout_sec)

    def solve(self) -> FlareSolverrClearance | None:
        if not self.flaresolverr_url:
            return None
        payload: dict[str, Any] = {
            "cmd": "request.get",
            "url": "https://grok.com",
            "maxTimeout": max(1, self.timeout_sec) * 1000,
        }
        if proxy := config.get_proxy_settings():
            payload["proxy"] = {"url": proxy}
        response = requests.post(f"{self.flaresolverr_url}/v1", json=payload, timeout=max(1, self.timeout_sec) + 5)
        response.raise_for_status()
        data = response.json()
        solution = data.get("solution") if isinstance(data, dict) else None
        if not isinstance(solution, dict):
            return None
        user_agent = str(solution.get("userAgent") or "").strip()
        cookies = solution.get("cookies")
        cookie_items = cookies if isinstance(cookies, list) else []
        cookie_parts: list[str] = []
        cf_clearance = ""
        for item in cookie_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if not name or not value or "=" in name or any(char in name + value for char in ";\r\n"):
                continue
            if name == "cf_clearance":
                cf_clearance = value
            cookie_parts.append(f"{name}={value}")
        if not user_agent or not cf_clearance:
            return None
        return FlareSolverrClearance(user_agent=user_agent, cf_clearance=cf_clearance, cf_cookies="; ".join(cookie_parts))
