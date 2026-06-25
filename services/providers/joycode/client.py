from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, quote, urlencode, urlparse

from services.network.client import create_session
from services.providers.joycode.models import DEFAULT_MODEL, REASONING_MODEL_IDS, UPSTREAM_MODEL_BY_ID

BASE_URL = "https://joycode-api.jd.com"
DEFAULT_COLOR_BASE_URL = "https://api-ai.jd.com"
CLIENT_VERSION = "2.7.5"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "JoyCode/2.7.5 Chrome/133.0.0.0 Electron/35.2.0 Safari/537.36"
)
COLOR_GATEWAY_APP_ID = "joycode_ide"
COLOR_GATEWAY_PATH = "/api"
COLOR_HMAC_KEY = "0691a3f0b37b4a85aeb63ad0fc7db3ed"

COLOR_ENDPOINTS = {
    "/api/saas/openai/v1/chat/completions": ("chat_completions", "/api/saas/openai/v2/chat/completions"),
    "/api/saas/models/v1/modelList": ("joycode_modelList", "/api/saas/models/v2/modelList"),
    "/api/saas/openai/v1/web-search": ("web_search", "/api/saas/openai/v2/web-search"),
    "/api/saas/user/v1/userInfo": ("joycode_userInfo", "/api/saas/user/v2/userInfo"),
    "/api/saas/anthropic/v1/messages": ("anthropic_completions", "/api/saas/anthropic/v1/messages"),
}


class JoyCodeError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Credentials:
    pt_key: str
    user_id: str = ""
    color_base_url: str = ""
    master_base_url: str = ""
    tenant: str = ""
    login_type: str = ""
    org_full_name: str = ""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _color_sign(function_id: str) -> tuple[str, str]:
    ts = str(int(time.time() * 1000))
    sign_src = f"{COLOR_GATEWAY_APP_ID}&{function_id}&{ts}"
    sign = hmac.new(COLOR_HMAC_KEY.encode(), sign_src.encode(), hashlib.sha256).hexdigest()
    return f"appid={COLOR_GATEWAY_APP_ID}&functionId={function_id}&t={ts}", sign


def parse_oauth_pt_key(value: str) -> str:
    return parse_oauth_credentials(value).pt_key


def parse_oauth_credentials(value: str) -> Credentials:
    text = _clean(value)
    if not text:
        return Credentials(pt_key="")
    if text.startswith(("http://", "https://")):
        query = parse_qs(urlparse(text).query)
        return Credentials(
            pt_key=_clean((query.get("pt_key") or [""])[0]),
            color_base_url=_clean((query.get("base_url") or query.get("colorBaseUrl") or [""])[0]),
            master_base_url=_clean((query.get("master_base_url") or query.get("masterBaseUrl") or [""])[0]),
            tenant=_clean((query.get("tenant") or [""])[0]),
            login_type=_clean((query.get("loginType") or query.get("login_type") or [""])[0]),
            org_full_name=_clean((query.get("orgFullName") or query.get("org_full_name") or [""])[0]),
        )
    return Credentials(pt_key=text)


def default_state_db_path() -> Path:
    if path := os.environ.get("JOYCODE_STATE_DB"):
        return Path(path)
    if Path("/root/.joycode-ide/state.vscdb").exists():
        return Path("/root/.joycode-ide/state.vscdb")
    return Path.home() / "Library/Application Support/JoyCode/User/globalStorage/state.vscdb"


def load_from_state_db(path: str | os.PathLike[str] | None = None) -> Credentials:
    db_path = Path(path) if path else default_state_db_path()
    if not db_path.exists():
        raise JoyCodeError(f"JoyCode state database not found: {db_path}", 400)
    conn = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT value FROM ItemTable WHERE key='JoyCoder.IDE'").fetchone()
    finally:
        conn.close()
    if not row:
        raise JoyCodeError("JoyCode login info not found in state database", 400)
    try:
        data = json.loads(row[0])
        user = data.get("joyCoderUser") or {}
    except Exception as exc:
        raise JoyCodeError(f"cannot parse JoyCode state database: {exc}", 400) from exc
    creds = Credentials(
        pt_key=_clean(user.get("ptKey")),
        user_id=_clean(user.get("userId")),
        color_base_url=_clean(user.get("colorBaseUrl")),
        master_base_url=_clean(user.get("masterBaseUrl")),
        tenant=_clean(user.get("tenant")),
        login_type=_clean(user.get("loginType")),
        org_full_name=_clean(user.get("orgFullName")),
    )
    if not creds.pt_key or not creds.user_id:
        raise JoyCodeError("JoyCode state database is missing ptKey or userId", 400)
    return creds


class JoyCodeClient:
    def __init__(self, account: dict[str, Any] | None = None, *, timeout: int = 1800) -> None:
        account = account or {}
        self.pt_key = _clean(account.get("pt_key") or account.get("access_token"))
        self.anthropic_pt_key = _clean(account.get("anthropic_pt_key"))
        self.user_id = _clean(account.get("user_id"))
        self.color_base_url = _clean(account.get("color_base_url")) or DEFAULT_COLOR_BASE_URL
        self.master_base_url = _clean(account.get("master_base_url"))
        self.tenant = _clean(account.get("tenant"))
        self.login_type = _clean(account.get("login_type"))
        self.org_full_name = _clean(account.get("org_full_name"))
        self.session_id = secrets.token_hex(16)
        self.session = create_session(account=account)
        self.timeout = timeout

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "JoyCodeClient":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def request_url(self, endpoint: str) -> str:
        mapped = COLOR_ENDPOINTS.get(endpoint)
        if not mapped:
            return BASE_URL + endpoint
        function_id, v2_path = mapped
        parsed = urlparse(self.color_base_url)
        if parsed.scheme and parsed.netloc:
            query, sign = _color_sign(function_id)
            return f"{parsed.scheme}://{parsed.netloc}{COLOR_GATEWAY_PATH}?{query}&sign={sign}"
        base = self.master_base_url or BASE_URL
        return base.rstrip("/") + v2_path

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "source-type": "joycoder-ide",
            "ptKey": self.pt_key,
            "loginType": self.login_type or "N_PIN_PC",
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def _anthropic_headers(self) -> dict[str, str]:
        headers = self._headers()
        headers["Content-Type"] = "application/json; charset=utf-8"
        headers["ptKey"] = self.anthropic_pt_key or self.pt_key
        headers["loginType"] = self.login_type or "PIN_JD_CLOUD"
        return headers

    def prepare_body(self, extra: dict[str, Any]) -> dict[str, Any]:
        body = {
            "tenant": self.tenant or "JOYCODE",
            "orgFullName": self.org_full_name,
            "userId": self.user_id,
            "client": "JoyCode",
            "clientVersion": CLIENT_VERSION,
            "language": "UNKNOWN",
        }
        body.update(extra)
        return body

    def prepare_anthropic_body(self, extra: dict[str, Any]) -> dict[str, Any]:
        body = {
            "tenant": self.tenant or "JD",
            "orgFullName": self.org_full_name,
            "userId": self.user_id,
            "client": "JoyCode",
            "clientVersion": CLIENT_VERSION,
            "language": "UNKNOWN",
            "stream": True,
        }
        body.update(extra)
        return body

    @staticmethod
    def _body_bytes(response: Any) -> bytes:
        data = response.content
        if str(response.headers.get("Content-Encoding") or "").lower() == "gzip":
            try:
                return gzip.decompress(data)
            except gzip.BadGzipFile:
                return data
        return data

    def post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            self.request_url(endpoint),
            headers=self._headers(),
            data=json.dumps(self.prepare_body(body), ensure_ascii=False),
            timeout=self.timeout,
        )
        data = self._body_bytes(response)
        if response.status_code != 200:
            raise JoyCodeError(f"JoyCode API error {response.status_code}: {data.decode('utf-8', 'replace')}", response.status_code)
        try:
            parsed = json.loads(data)
        except Exception as exc:
            raise JoyCodeError(f"invalid JoyCode JSON response: {data.decode('utf-8', 'replace')[:500]}") from exc
        if not isinstance(parsed, dict):
            raise JoyCodeError("unexpected JoyCode JSON response")
        return parsed

    def post_stream(self, endpoint: str, body: dict[str, Any], *, anthropic: bool = False) -> Iterator[str]:
        headers = self._anthropic_headers() if anthropic else self._headers()
        payload = self.prepare_anthropic_body(body) if anthropic else self.prepare_body(body)
        with self.session.stream(
            "POST",
            self.request_url(endpoint),
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False),
            timeout=self.timeout,
        ) as response:
            if response.status_code != 200:
                raise JoyCodeError(f"JoyCode stream API error {response.status_code}: {response.text}", response.status_code)
            for line in response.iter_lines():
                if not line:
                    continue
                yield line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)

    def chat_body(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str, stream: bool) -> dict[str, Any]:
        requested_model = model or DEFAULT_MODEL
        payload: dict[str, Any] = {
            "model": UPSTREAM_MODEL_BY_ID.get(requested_model, requested_model),
            "messages": messages,
            "stream": stream,
        }
        for key in ("max_tokens", "temperature", "top_p", "tools", "tool_choice", "stop"):
            if key in body and body.get(key) is not None:
                payload[key] = body[key]
        if body.get("thinking") is not None and requested_model in REASONING_MODEL_IDS:
            payload["thinking"] = body["thinking"]
        return payload

    def chat_completion(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        return self.post("/api/saas/openai/v1/chat/completions", self.chat_body(body, messages, model, False))

    def chat_completion_deltas(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> Iterator[str]:
        for line in self.post_stream("/api/saas/openai/v1/chat/completions", self.chat_body(body, messages, model, True)):
            data = line.strip()
            if data.startswith("data:"):
                data = data.removeprefix("data:").strip()
            if not data or data == "[DONE]":
                continue
            try:
                chunk = json.loads(data)
            except Exception:
                continue
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            text = delta.get("content")
            if isinstance(text, str) and text:
                yield text

    def list_models(self) -> list[dict[str, Any]]:
        payload = self.post("/api/saas/models/v1/modelList", {})
        data = payload.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def user_info(self) -> dict[str, Any]:
        return self.post("/api/saas/user/v1/userInfo", {})

    def validate(self) -> dict[str, Any]:
        payload = self.user_info()
        if int(payload.get("code") or 0) != 0:
            raise JoyCodeError(f"credential validation failed: {payload.get('msg') or 'unknown error'}", 401)
        return payload

    def user_info_with_refresh(self) -> tuple[dict[str, Any], str]:
        payload = self.validate()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return payload, _clean(data.get("ptKey"))

    def web_search(self, query: str) -> list[Any]:
        payload = self.post("/api/saas/openai/v1/web-search", {
            "messages": [{"role": "user", "content": query}],
            "stream": False,
            "model": "search_pro_jina",
            "language": "UNKNOWN",
        })
        results = payload.get("search_result")
        return results if isinstance(results, list) else []

    def rerank(self, query: str, documents: list[str], top_n: int) -> dict[str, Any]:
        return self.post("/api/saas/openai/v1/rerank", {
            "model": "Qwen3-Reranker-8B",
            "query": query,
            "documents": documents,
            "top_n": top_n,
        })
