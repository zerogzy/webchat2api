from __future__ import annotations

import time
from typing import Any

from curl_cffi import requests
from fastapi import HTTPException

from services.config import config
from services.models import GROK_PROVIDER, ModelSpec
from services.network.client import create_session
from services.network.headers import build_grok_console_headers
from services.network.profiles import build_grok_console_profile
from utils.log import logger

CONSOLE_BASE_URL = "https://console.x.ai"
CONSOLE_RESPONSES_URL = f"{CONSOLE_BASE_URL}/v1/responses"


class GrokConsoleError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_status = upstream_status


def _message_text(content: object, role: str) -> str:
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
        if block_type in {"text", "input_text", "output_text"}:
            text = block.get("text") or block.get("input_text") or block.get("output_text")
            if text:
                parts.append(str(text))
    return "\n".join(part for part in parts if part)


def build_console_input(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user").strip().lower()
        text = _message_text(message.get("content"), role).strip()
        if not text:
            continue
        if role == "system":
            instructions.append(text)
            continue
        if role not in {"user", "assistant"}:
            continue
        content_type = "output_text" if role == "assistant" else "input_text"
        input_items.append({
            "role": role,
            "content": [{"type": content_type, "text": text}],
        })
    return "\n\n".join(instructions).strip(), input_items


def build_console_payload(spec: ModelSpec, body: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    instructions, input_items = build_console_input(messages)
    if not input_items:
        raise HTTPException(status_code=400, detail={"error": "Grok chat requires at least one user or assistant text message"})
    payload: dict[str, Any] = {
        "model": spec.upstream_model or spec.id,
        "input": input_items,
    }
    request_instructions = str(body.get("instructions") or "").strip()
    merged_instructions = "\n\n".join(item for item in [request_instructions, instructions] if item)
    if merged_instructions:
        payload["instructions"] = merged_instructions
    for key in ("temperature", "top_p", "max_output_tokens", "max_tokens"):
        if body.get(key) is not None:
            target_key = "max_output_tokens" if key == "max_tokens" else key
            payload[target_key] = body[key]
    reasoning_effort = str(body.get("reasoning_effort") or spec.default_reasoning_effort or "").strip().lower()
    if reasoning_effort and reasoning_effort != "none":
        payload["reasoning"] = {"effort": "high" if reasoning_effort == "xhigh" else reasoning_effort}
    return payload


def _extract_text_from_content(content: object) -> str:
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
        text = block.get("text") or block.get("output_text") or block.get("input_text")
        if text and str(block.get("type") or "") in {"", "text", "output_text", "input_text"}:
            parts.append(str(text))
    return "".join(parts)


def extract_console_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct:
        return direct
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type in {"message", "output_message", ""}:
            text = _extract_text_from_content(item.get("content"))
            if text:
                parts.append(text)
        elif item_type in {"output_text", "text"} and item.get("text"):
            parts.append(str(item.get("text")))
    return "".join(parts)


def _grok_console_profile():
    return build_grok_console_profile(config.data)


def _headers(access_token: str) -> dict[str, str]:
    return build_grok_console_headers(_grok_console_profile(), access_token=access_token, base_url=CONSOLE_BASE_URL)


def _openai_status(upstream_status: int) -> int:
    if upstream_status in {401, 403, 404}:
        return upstream_status
    if upstream_status in {402, 429}:
        return 429
    if 400 <= upstream_status < 500:
        return 400
    return 502


def _feedback_status(upstream_status: int) -> str | None:
    if upstream_status in {401, 403}:
        return "异常"
    if upstream_status in {402, 429}:
        return "限流"
    return None


class GrokConsoleClient:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self.network_profile = _grok_console_profile()
        self.session = create_session(impersonate=self.network_profile.impersonate, verify=self.network_profile.verify)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "GrokConsoleClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _call_with_retry(self, fn, policy=None, context=""):
        """Wrap a callable with retry handling."""
        from services.network.retry import retry_call, RetryPolicy

        api_policy = policy or RetryPolicy(
            max_attempts=3,
            retry_statuses=frozenset({408, 429, 500, 502, 503, 504}),
        )
        deadline = time.monotonic() + 60.0

        def on_retry(attempt, status_code, exc):
            logger.warning({
                "event": "grok_retry",
                "context": context,
                "attempt": attempt,
                "status_code": status_code,
                "error": str(exc) if exc else None,
            })

        return retry_call(fn, policy=api_policy, deadline=deadline, on_retry=on_retry)

    def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._call_with_retry(
                lambda: self.session.post(
                    CONSOLE_RESPONSES_URL,
                    headers=_headers(self.access_token),
                    json=payload,
                    timeout=self.network_profile.timeout,
                ),
                context="create_response",
            )
        except requests.exceptions.RequestException as exc:
            raise GrokConsoleError(f"Grok upstream request failed: {exc}", 502) from exc
        if response.status_code >= 400:
            status = int(response.status_code)
            feedback_status = _feedback_status(status)
            if feedback_status:
                from services.account_service import account_service

                account_service.update_account(self.access_token, {"status": feedback_status})
            message = f"Grok upstream error (HTTP {status})"
            raise GrokConsoleError(message, _openai_status(status), status)
        data = response.json()
        if not isinstance(data, dict):
            raise GrokConsoleError("Grok upstream returned an invalid response", 502)
        return data


def chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> str:
    from services.account_service import account_service

    access_token = account_service.get_text_access_token(provider=GROK_PROVIDER)
    if not access_token:
        raise HTTPException(status_code=503, detail={"error": "no available Grok account"})
    payload = build_console_payload(spec, body, messages)
    try:
        with GrokConsoleClient(access_token) as client:
            response_json = client.create_response(payload)
    except GrokConsoleError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error": str(exc)}) from exc
    account_service.mark_text_used(access_token)
    text = extract_console_text(response_json)
    if not text:
        raise HTTPException(status_code=502, detail={"error": "Grok upstream response did not contain text"})
    return text
