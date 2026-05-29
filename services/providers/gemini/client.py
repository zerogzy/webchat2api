from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator

from curl_cffi import requests
from fastapi import HTTPException

from services.providers.base import ModelSpec
from services.providers.gemini.models import gemini_model_metadata
from services.network.client import create_session

GEMINI_WEB_BASE_URL = "https://gemini.google.com"
GEMINI_WEB_GENERATE_URL = f"{GEMINI_WEB_BASE_URL}/app/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
GEMINI_REQUIRED_COOKIES = ("__Secure-1PSID", "__Secure-1PSIDTS")
GEMINI_SENSITIVE_COOKIE_NAMES = ("__Secure-1PSID", "__Secure-1PSIDTS", "SNlM0e")
GEMINI_NON_COOKIE_FIELDS = ("SNlM0e", "session_token", "at")
GEMINI_WEB_RPC_ID = "assistant.lamda.BardFrontendService.StreamGenerate"


@dataclass(frozen=True)
class GeminiCompletion:
    content: str
    raw_response: object = None


class GeminiWebError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, upstream_status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_status = upstream_status
        self.code = code

    def to_http_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"error": str(self)}
        if self.upstream_status is not None:
            detail["upstream_status"] = self.upstream_status
        if self.code:
            detail["code"] = self.code
        return detail


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


def cookie_header_from_mapping(cookies: dict[str, Any]) -> str:
    parts: list[str] = []
    for name, value in cookies.items():
        name_text = str(name or "").strip()
        value_text = str(value or "").strip()
        if name_text and value_text:
            parts.append(f"{name_text}={value_text}")
    return "; ".join(parts)


def sanitize_cookie_header(cookie_header: str) -> str:
    cookies = parse_cookie_header(cookie_header)
    for name in list(cookies):
        if name in GEMINI_SENSITIVE_COOKIE_NAMES:
            cookies[name] = "[redacted]"
    return cookie_header_from_mapping(cookies)


def session_token_from_response(raw_text: str) -> str:
    for pattern in (r'"SNlM0e"\s*:\s*"([^"]+)"', r'\[\s*"SNlM0e"\s*,\s*"([^"]+)"\s*\]'):
        match = re.search(pattern, raw_text)
        if match:
            return match.group(1)
    return ""


def classify_upstream_error(status_code: int, raw_text: str = "") -> GeminiWebError:
    text = raw_text.lower()
    if status_code in {401, 403}:
        message = "Gemini upstream authentication failed"
        if "snlm0e" in text or "secure-1psidts" in text:
            message = "Gemini upstream authentication failed; refresh Gemini session cookies"
        return GeminiWebError(message, status_code=401, upstream_status=status_code, code="gemini_auth_failed")
    if status_code == 429:
        return GeminiWebError("Gemini upstream rate limit exceeded", status_code=429, upstream_status=status_code, code="gemini_rate_limited")
    if status_code >= 500:
        return GeminiWebError("Gemini upstream service unavailable", status_code=502, upstream_status=status_code, code="gemini_upstream_unavailable")
    return GeminiWebError("Gemini upstream request failed", status_code=502, upstream_status=status_code, code="gemini_upstream_error")


def build_stream_generate_form_payload(prompt: str, model: str, session_token: str = "") -> dict[str, str]:
    inner = [[prompt], None, None, model]
    outer = [None, json.dumps(inner, ensure_ascii=False, separators=(",", ":"))]
    data = {"f.req": json.dumps(outer, ensure_ascii=False, separators=(",", ":"))}
    if session_token:
        data["at"] = session_token
    return data


def stream_generate_url(session_token: str = "") -> str:
    if not session_token:
        return GEMINI_WEB_GENERATE_URL
    from urllib.parse import quote

    return f"{GEMINI_WEB_GENERATE_URL}?at={quote(session_token, safe='')}"


def account_session_token(account: dict[str, Any]) -> str:
    for key in ("session_token", "SNlM0e", "at"):
        value = str(account.get(key) or "").strip()
        if value:
            return value
    direct = parse_cookie_header(str(account.get("access_token") or ""))
    for key in ("SNlM0e", "at"):
        value = str(direct.get(key) or "").strip()
        if value:
            return value
    stored_cookies = account.get("cookies")
    if isinstance(stored_cookies, dict):
        for key in ("SNlM0e", "at"):
            value = str(stored_cookies.get(key) or "").strip()
            if value:
                return value
    return ""


def account_cookie_header(account: dict[str, Any]) -> str:
    direct = str(account.get("access_token") or "").strip()
    cookies = parse_cookie_header(direct)
    stored_cookies = account.get("cookies")
    if isinstance(stored_cookies, dict):
        for name, value in stored_cookies.items():
            value_text = str(value or "").strip()
            if value_text:
                cookies[str(name)] = value_text
    for name in GEMINI_REQUIRED_COOKIES:
        value = str(account.get(name) or "").strip()
        if value:
            cookies[name] = value
    for name in GEMINI_NON_COOKIE_FIELDS:
        cookies.pop(name, None)
    missing = [name for name in GEMINI_REQUIRED_COOKIES if not cookies.get(name)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"error": f"Gemini account is missing required cookie(s): {', '.join(missing)}"},
        )
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip()
        if block_type in {"", "text", "input_text", "output_text"}:
            text = block.get("text") or block.get("input_text") or block.get("output_text")
            if text:
                parts.append(str(text))
    return "\n".join(part for part in parts if part)


def build_prompt(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user").strip().lower()
        text = message_text(message.get("content")).strip()
        if not text:
            continue
        label = "User" if role == "user" else "Assistant" if role == "assistant" else "System"
        parts.append(f"{label}: {text}")
    if not parts:
        raise HTTPException(status_code=400, detail={"error": "Gemini chat requires at least one text message"})
    return "\n\n".join(parts)


def build_web_payload(spec: ModelSpec, body: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = build_prompt(messages)
    payload: dict[str, Any] = {
        "model": spec.upstream_model or spec.id,
        "prompt": prompt,
    }
    for key in ("temperature", "top_p", "max_tokens"):
        if body.get(key) is not None:
            payload[key] = body[key]
    return payload


def _json_loads(value: str) -> object | None:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _parse_nested_json(value: object) -> object:
    current = value
    while isinstance(current, str):
        text = current.strip()
        if not text.startswith(("[", "{")):
            return current
        parsed = _json_loads(text)
        if parsed is None:
            return current
        current = parsed
    return current


def _wrb_payload_from_entry(entry: object) -> object | None:
    if not isinstance(entry, list) or len(entry) < 3:
        return None
    if entry[0] != "wrb.fr" or entry[1] != GEMINI_WEB_RPC_ID:
        return None
    payload = entry[2]
    if isinstance(payload, str):
        return _parse_nested_json(payload)
    return payload


def _wrb_payloads(value: object) -> Iterator[object]:
    payload = _wrb_payload_from_entry(value)
    if payload is not None:
        yield payload
        return
    if isinstance(value, list):
        for item in value:
            yield from _wrb_payloads(item)


def _is_incidental_stream_string(value: str) -> bool:
    text = value.strip()
    lowered = text.lower()
    if not text:
        return True
    if lowered.startswith(("rc_", "wrb.fr", "assistant.lamda.")):
        return True
    if lowered in {"snlm0e", "at"}:
        return True
    if lowered.startswith("gemini-"):
        return True
    if lowered in {"generic", "rpc-id", "request-id", "conversation-id", "response-id"}:
        return True
    if lowered.endswith("-id"):
        return True
    return False


def _response_candidates_from_stream_generate(value: object) -> Iterator[str]:
    value = _parse_nested_json(value)
    if isinstance(value, dict):
        for key in ("content", "text", "answer", "response"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                yield candidate.strip()
        for item in value.values():
            yield from _response_candidates_from_stream_generate(item)
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text and not text.startswith(("[", "{")):
                    if not _is_incidental_stream_string(text):
                        yield text
                else:
                    yield from _response_candidates_from_stream_generate(item)
            elif isinstance(item, (list, dict)):
                yield from _response_candidates_from_stream_generate(item)


def extract_stream_generate_text(payload: object) -> str:
    for wrb_payload in _wrb_payloads(payload):
        candidates = [candidate for candidate in _response_candidates_from_stream_generate(wrb_payload) if candidate]
        if candidates:
            return max(candidates, key=len).strip()
    return ""


def _string_candidates(value: object) -> Iterator[str]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("[", "{")):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                yield from _string_candidates(parsed)
                return
        if text:
            yield text
        return
    if isinstance(value, dict):
        for key in ("content", "text", "output", "answer", "response"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                yield item
        for item in value.values():
            yield from _string_candidates(item)
        return
    if isinstance(value, list):
        for item in reversed(value):
            yield from _string_candidates(item)


def extract_text(payload: object) -> str:
    targeted = extract_stream_generate_text(payload)
    if targeted:
        return targeted
    if isinstance(payload, dict):
        for key in ("content", "text", "output", "answer", "response"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for text in _string_candidates(payload):
        stripped = text.strip()
        if stripped:
            return stripped
    return ""


def parse_web_response_text(raw_text: str) -> object:
    text = raw_text.strip()
    if not text:
        return {}
    parsed = _json_loads(text)
    if parsed is not None:
        return parsed
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line or line.startswith(")]}'"):
            continue
        parsed = _json_loads(line)
        if parsed is not None:
            return parsed
    matches = re.findall(r"\[[\s\S]*\]|\{[\s\S]*\}", text)
    for candidate in reversed(matches):
        parsed = _json_loads(candidate)
        if parsed is not None:
            return parsed
    return text


class GeminiWebClient:
    def __init__(self, cookie_header: str, user_agent: str | None = None) -> None:
        self.cookie_header = cookie_header
        self.user_agent = user_agent or "Mozilla/5.0"
        self.session = create_session()

    def __enter__(self) -> "GeminiWebClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()

    def generate(self, payload: dict[str, Any]) -> object:
        headers = {
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "cookie": self.cookie_header,
            "user-agent": self.user_agent,
            "origin": GEMINI_WEB_BASE_URL,
            "referer": f"{GEMINI_WEB_BASE_URL}/app",
            "x-same-domain": "1",
        }
        session_token = str(payload.get("session_token") or "").strip()
        response = self.session.post(
            stream_generate_url(session_token),
            headers=headers,
            data=build_stream_generate_form_payload(payload["prompt"], payload["model"], session_token),
            timeout=120,
        )
        status_code = int(getattr(response, "status_code", getattr(response, "status", 0)) or 0)
        raw_text = str(getattr(response, "text", "") or "")
        if status_code >= 400:
            raise classify_upstream_error(status_code, raw_text)
        return parse_web_response_text(raw_text)


def list_model_metadata() -> list[dict[str, Any]]:
    return [dict(item) for item in gemini_model_metadata()]


def extract_completion(payload: object) -> GeminiCompletion:
    return GeminiCompletion(content=extract_text(payload), raw_response=payload)


def chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> GeminiCompletion:
    from services.account_service import account_service

    access_token = account_service.get_text_access_token(provider="gemini")
    if not access_token:
        raise HTTPException(status_code=503, detail={"error": "no available Gemini account"})
    account = account_service.get_account(access_token) or {"access_token": access_token, "provider": "gemini"}
    cookie_header = account_cookie_header(account)
    payload = build_web_payload(spec, body, messages)
    session_token = account_session_token(account)
    if session_token:
        payload["session_token"] = session_token
    try:
        with GeminiWebClient(cookie_header, account.get("user_agent")) as client:
            response_payload = client.generate(payload)
    except GeminiWebError as exc:
        account_service.mark_text_used(access_token)
        raise HTTPException(status_code=exc.status_code, detail=exc.to_http_detail()) from exc
    completion = extract_completion(response_payload)
    if not completion.content:
        raise HTTPException(status_code=502, detail={"error": "Gemini upstream response did not contain text"})
    account_service.mark_text_used(access_token)
    return completion


def synthetic_stream_content(content: str, chunk_size: int = 120) -> Iterator[str]:
    if not content:
        yield ""
        return
    for index in range(0, len(content), chunk_size):
        yield content[index:index + chunk_size]
