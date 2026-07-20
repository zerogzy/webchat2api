from __future__ import annotations

import base64
import threading
import time
from html.parser import HTMLParser
from ipaddress import ip_address
from urllib.parse import urlparse

from services.network.client import create_session


class _VerificationMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.content = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta" or self.content:
            return
        values = {str(key).lower(): str(value or "").strip() for key, value in attrs}
        name = values.get("name", "").lower().replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
        if name == "grok-site-verification":
            self.content = values.get("content", "")


def valid_statsig_id(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        decoded = base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
    except Exception:
        return False
    return len(decoded) == 70


def _validate_signer_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if (
        len(raw) > 2048
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Statsig signer URL must not contain credentials, query parameters, or fragments")
    host = parsed.hostname.lower().rstrip(".")
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    internal = bool(
        (address is not None and (address.is_loopback or address.is_private or address.is_link_local))
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
        or ("." not in host and host.replace("-", "").replace("_", "").isalnum())
    )
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Statsig signer URL has an invalid port") from exc
    if internal and parsed.scheme in {"http", "https"}:
        return parsed.geturl()
    if parsed.scheme != "https" or port not in {None, 443}:
        raise ValueError("Public Statsig signer URLs must use HTTPS on port 443")
    return parsed.geturl()


class GrokStatsigSigner:
    def __init__(self, ttl_seconds: float = 3600.0, failure_ttl_seconds: float = 30.0) -> None:
        self.ttl_seconds = ttl_seconds
        self.failure_ttl_seconds = failure_ttl_seconds
        self._cache: dict[tuple[str, str, str], tuple[str, float]] = {}
        self._failures: dict[tuple[str, str, str], tuple[Exception, float]] = {}
        self._lock = threading.Lock()

    def invalidate(self, method: str, target_url: str) -> None:
        path = urlparse(target_url).path or "/"
        with self._lock:
            for key in list(self._cache):
                if key[1:] == (method.upper(), path):
                    self._cache.pop(key, None)
                    self._failures.pop(key, None)

    def sign(self, upstream_session: object, headers: dict[str, str], signer_url: str, method: str, target_url: str) -> str:
        path = urlparse(target_url).path or "/"
        key = (_validate_signer_url(signer_url), method.upper(), path)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[1] > now:
                return cached[0]
            failed = self._failures.get(key)
            if failed and failed[1] > now:
                raise RuntimeError(str(failed[0])) from failed[0]

        try:
            index_headers = dict(headers)
            index_headers.pop("x-statsig-id", None)
            index_headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            index_headers["Sec-Fetch-Dest"] = "document"
            index_headers["Sec-Fetch-Mode"] = "navigate"
            index_response = upstream_session.get("https://grok.com/index", headers=index_headers, timeout=15)
            if int(getattr(index_response, "status_code", 0) or 0) >= 400:
                raise RuntimeError(f"Grok index returned HTTP {index_response.status_code}")
            parser = _VerificationMetaParser()
            parser.feed(str(getattr(index_response, "text", "") or ""))
            if not parser.content:
                raise RuntimeError("Grok index is missing grok-site-verification")

            signer = create_session()
            try:
                response = signer.post(
                    key[0],
                    headers={"Content-Type": "application/json"},
                    json={
                        "method": method.upper(),
                        "path": path,
                        "environment": {"metaContent": parser.content},
                    },
                    timeout=12,
                )
                if int(getattr(response, "status_code", 0) or 0) >= 400:
                    raise RuntimeError(f"Statsig signer returned HTTP {response.status_code}")
                payload = response.json()
            finally:
                signer.close()
        except Exception as exc:
            if cached and valid_statsig_id(cached[0]):
                return cached[0]
            with self._lock:
                self._failures[key] = (exc, now + self.failure_ttl_seconds)
            raise
        value = str(payload.get("x-statsig-id") or "") if isinstance(payload, dict) else ""
        if not valid_statsig_id(value):
            error = RuntimeError("Statsig signer returned an invalid signature")
            if cached and valid_statsig_id(cached[0]):
                return cached[0]
            with self._lock:
                self._failures[key] = (error, now + self.failure_ttl_seconds)
            raise error
        with self._lock:
            self._cache[key] = (value, now + self.ttl_seconds)
            self._failures.pop(key, None)
        return value


grok_statsig_signer = GrokStatsigSigner()
