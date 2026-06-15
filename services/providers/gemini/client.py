from __future__ import annotations

from typing import Any, Iterator

from fastapi import HTTPException

from services.providers.base import ModelSpec
from services.providers.gemini.accounts import (
    cookie_header_from_mapping,
    cookie_header_from_response,
    gemini_cookie_state,
    gemini_session_token,
    merge_cookie_headers,
    merge_response_cookies,
    parse_cookie_header,
    sanitize_cookie_header,
)
from services.providers.gemini.models import gemini_model_metadata

GEMINI_WEB_IMAGE_UNSUPPORTED_DETAIL = "Gemini Web image input is not supported by this upstream adapter"
GEMINI_IMAGE_PART_TYPES = {"image", "image_url", "input_image"}
GEMINI_IMAGE_PAYLOAD_KEYS = {"image_url", "inlineData", "inline_data"}


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


GeminiWebClient = None


def _api_client():
    from services.providers.gemini import api_client

    return api_client


GeminiCompletion = _api_client().GeminiApiCompletion


def _clean(value: Any) -> str:
    return str(value or "").strip()


def account_session_token(account: dict[str, Any]) -> str:
    return gemini_session_token(account)


def account_cookie_header(account: dict[str, Any]) -> str:
    try:
        return gemini_cookie_state(account, require_session_cookies=True).cookie_header
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc


def gemini_session_writeback(account: dict[str, Any], cookie_header: str, session_token: str = "") -> dict[str, Any]:
    cookies = parse_cookie_header(cookie_header)
    updates: dict[str, Any] = {}
    if cookies:
        updates["cookies"] = cookies
    for name in ("__Secure-1PSID", "__Secure-1PSIDTS"):
        if cookies.get(name):
            updates[name] = cookies[name]
    token = session_token or gemini_session_token(account)
    if token:
        updates["session_token"] = token
        updates["SNlM0e"] = token
        updates["at"] = token
    return updates


def persist_gemini_session(account_service: object, access_token: str, account: dict[str, Any], cookie_header: str, session_token: str = "") -> None:
    update_account = getattr(account_service, "update_account", None)
    if not callable(update_account):
        return
    updates = gemini_session_writeback(account, cookie_header, session_token)
    if updates:
        update_account(access_token, updates, provider="gemini")


def session_token_from_response(raw_text: str) -> str:
    return ""


def classify_upstream_error(status_code: int, raw_text: str = "") -> GeminiWebError:
    text = raw_text.lower()
    if status_code in {401, 403} or "auth" in text or "sign in" in text:
        return GeminiWebError("Gemini upstream authentication failed", status_code=401, upstream_status=status_code, code="gemini_auth_failed")
    if status_code == 429:
        return GeminiWebError("Gemini upstream rate limit exceeded", status_code=429, upstream_status=status_code, code="gemini_rate_limited")
    return GeminiWebError("Gemini upstream request failed", status_code=502, upstream_status=status_code, code="gemini_upstream_error")


def build_stream_generate_form_payload(prompt: str, model: str, session_token: str = "") -> dict[str, str]:
    return {"prompt": prompt, "model": model, "session_token": session_token}


def stream_generate_url(session_token: str = "") -> str:
    return "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"


def rotate_psidts_cookie(session: object, cookie_header: str, user_agent: str | None = None) -> str:
    raise GeminiWebError("Gemini cookie rotation is handled by gemini-webapi", code="gemini_webapi_rotation")


def contains_image_content(value: object) -> bool:
    if isinstance(value, dict):
        block_type = _clean(value.get("type"))
        if block_type in GEMINI_IMAGE_PART_TYPES:
            return True
        if any(key in value for key in GEMINI_IMAGE_PAYLOAD_KEYS):
            return True
        return any(contains_image_content(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_image_content(item) for item in value)
    return False


def raise_unsupported_image_input() -> None:
    raise HTTPException(status_code=400, detail={"error": GEMINI_WEB_IMAGE_UNSUPPORTED_DETAIL})


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
        if _clean(block.get("type")) in {"", "text", "input_text", "output_text"}:
            text = block.get("text") or block.get("input_text") or block.get("output_text")
            if text:
                parts.append(str(text))
    return "\n".join(part for part in parts if part)


def build_prompt(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        if contains_image_content(message.get("content")):
            raise_unsupported_image_input()
        role = _clean(message.get("role")).lower() or "user"
        text = message_text(message.get("content")).strip()
        if not text:
            continue
        label = "User" if role == "user" else "Assistant" if role == "assistant" else "System"
        parts.append(f"{label}: {text}")
    if not parts:
        raise HTTPException(status_code=400, detail={"error": "Gemini chat requires at least one text message"})
    return "\n\n".join(parts)


def build_web_payload(spec: ModelSpec, body: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"model": spec.upstream_model or spec.id, "prompt": build_prompt(messages)}


def parse_web_response_text(raw_text: str) -> object:
    return raw_text


def extract_stream_generate_text(payload: object) -> str:
    return _clean(payload)


def extract_stream_generate_metadata(payload: object) -> dict[str, str]:
    return {}


def extract_text(payload: object) -> str:
    if hasattr(payload, "text"):
        return _clean(getattr(payload, "text"))
    return _clean(payload)


def extract_completion(payload: object):
    return _api_client().GeminiApiCompletion(content=extract_text(payload), raw_response=payload)


def fetch_authenticated_init_body() -> str:
    from services.account_service import account_service

    access_token = account_service.get_text_access_token(provider="gemini")
    if not access_token:
        return ""
    account = account_service.get_account(access_token) or {"access_token": access_token, "provider": "gemini"}
    try:
        updates = _api_client().validate_account(account)
    except Exception:
        return ""
    models = updates.get("available_models")
    if not isinstance(models, list):
        return ""
    return " ".join(_clean(item.get("model_name")) for item in models if isinstance(item, dict))


def list_model_metadata() -> list[dict[str, Any]]:
    return [dict(item) for item in gemini_model_metadata()]


def chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]):
    from services.account_service import account_service

    prompt = build_prompt(messages)
    access_token = account_service.get_text_access_token(provider="gemini")
    if not access_token:
        raise HTTPException(status_code=503, detail={"error": "no available Gemini account"})
    account = account_service.get_account(access_token) or {"access_token": access_token, "provider": "gemini"}
    try:
        completion, updates = _api_client().chat_completion(account, spec, prompt)
    except Exception as exc:
        if hasattr(exc, "to_http_detail"):
            raise HTTPException(status_code=getattr(exc, "status_code", 502), detail=exc.to_http_detail()) from exc
        raise
    if updates:
        account_service.update_account(access_token, updates, provider="gemini")
    if not completion.content:
        raise HTTPException(status_code=502, detail={"error": "Gemini upstream response did not contain text"})
    account_service.mark_text_used(access_token)
    return completion


def chat_completion_deltas(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> Iterator[str]:
    from services.account_service import account_service

    prompt = build_prompt(messages)
    access_token = account_service.get_text_access_token(provider="gemini")
    if not access_token:
        raise HTTPException(status_code=503, detail={"error": "no available Gemini account"})
    account = account_service.get_account(access_token) or {"access_token": access_token, "provider": "gemini"}
    try:
        chunks, updates = _api_client().stream_completion(account, spec, prompt)
    except Exception as exc:
        if hasattr(exc, "to_http_detail"):
            raise HTTPException(status_code=getattr(exc, "status_code", 502), detail=exc.to_http_detail()) from exc
        raise
    if updates:
        account_service.update_account(access_token, updates, provider="gemini")
    account_service.mark_text_used(access_token)
    yield from chunks


def synthetic_stream_content(content: str, chunk_size: int = 120) -> Iterator[str]:
    if not content:
        yield ""
        return
    for index in range(0, len(content), chunk_size):
        yield content[index:index + chunk_size]
