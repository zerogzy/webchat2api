from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from curl_cffi import requests
from fastapi import HTTPException

from services.config import config
from services.models import GROK_PROVIDER, ModelSpec, is_supported_grok_app_chat_image_model
from services.network.client import create_session
from services.network.headers import build_grok_console_headers
from services.network.profiles import build_grok_console_profile
from services.protocol.conversation import ImageGenerationError, ImageOutput, format_image_result
from utils.log import logger

CONSOLE_BASE_URL = "https://console.x.ai"
CONSOLE_RESPONSES_URL = f"{CONSOLE_BASE_URL}/v1/responses"
APP_CHAT_BASE_URL = "https://grok.com"
APP_CHAT_NEW_CONVERSATION_URL = f"{APP_CHAT_BASE_URL}/rest/app-chat/conversations/new"
GROK_ASSET_BASE_URL = "https://assets.grok.com/"
GROK_APP_CHAT_STATSIG_ID = "0196a8f6-0501-79f8-8d74-a2f2c0f5f5f5"


class GrokConsoleError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_status = upstream_status


@dataclass(frozen=True)
class GrokConsoleCompletion:
    content: str
    reasoning_content: str = ""
    raw_reasoning: object = None
    raw_usage: object = None
    raw_response: dict[str, Any] | None = None


_THINKING_SUMMARY_RE = re.compile(
    r"^\s*(?:\*\*)?\s*(?:思考摘要|思考总结|thinking\s+summary|thought\s+summary|reasoning\s+summary|thinking|reasoning)\s*(?:\*\*\s*[:：]|[:：]\s*(?:\*\*)?)\s*(.*)$",
    re.IGNORECASE,
)
_ANSWER_SUMMARY_RE = re.compile(
    r"^\s*(?:\*\*)?\s*(?:答案|回答|answer|final\s+answer|response)\s*(?:\*\*\s*[:：]|[:：]\s*(?:\*\*)?)\s*(.*)$",
    re.IGNORECASE,
)


def split_visible_console_reasoning(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    lines = text.splitlines(keepends=True)
    first_content_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_content_index is None:
        return text, ""
    thinking_match = _THINKING_SUMMARY_RE.match(lines[first_content_index].rstrip("\r\n"))
    if not thinking_match:
        return text, ""
    answer_index = -1
    answer_match: re.Match[str] | None = None
    for index in range(first_content_index + 1, len(lines)):
        candidate = _ANSWER_SUMMARY_RE.match(lines[index].rstrip("\r\n"))
        if candidate:
            answer_index = index
            answer_match = candidate
            break
    if answer_index < 0 or answer_match is None:
        return text, ""

    reasoning_parts: list[str] = []
    if thinking_match.group(1):
        reasoning_parts.append(thinking_match.group(1))
        if lines[first_content_index].endswith(("\n", "\r")) and first_content_index + 1 < answer_index:
            reasoning_parts.append("\n")
    reasoning_parts.extend(lines[first_content_index + 1:answer_index])
    content_parts = lines[:first_content_index]
    if answer_match.group(1):
        content_parts.append(answer_match.group(1))
        if lines[answer_index].endswith(("\n", "\r")):
            content_parts.append("\n")
    content_parts.extend(lines[answer_index + 1:])
    return "".join(content_parts).strip(), "".join(reasoning_parts).strip()


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


def extract_console_completion(payload: dict[str, Any]) -> GrokConsoleCompletion:
    text = extract_console_text(payload)
    content, reasoning_content = split_visible_console_reasoning(text)
    return GrokConsoleCompletion(
        content=content,
        reasoning_content=reasoning_content,
        raw_reasoning=payload.get("reasoning"),
        raw_usage=payload.get("usage"),
        raw_response=payload,
    )


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


def _app_chat_cookie(access_token: str, cf_clearance: str = "") -> str:
    token = str(access_token or "").strip()
    cookies: dict[str, str] = {}
    if token:
        if "=" in token:
            for fragment in token.split(";"):
                name, separator, value = fragment.strip().partition("=")
                if separator and name.strip():
                    cookies[name.strip()] = value.strip()
        else:
            cookies["sso"] = token
            cookies["sso-rw"] = token
    if cookies.get("sso") and not cookies.get("sso-rw"):
        cookies["sso-rw"] = cookies["sso"]
    clearance = str(cf_clearance or "").strip()
    if clearance and "cf_clearance" not in cookies:
        cookies["cf_clearance"] = clearance
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def app_chat_headers(access_token: str) -> dict[str, str]:
    profile = _grok_console_profile()
    headers = {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
        "Content-Type": "application/json",
        "Origin": APP_CHAT_BASE_URL,
        "Referer": f"{APP_CHAT_BASE_URL}/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": profile.user_agent,
        "x-statsig-id": GROK_APP_CHAT_STATSIG_ID,
        "x-xai-request-id": str(uuid.uuid4()),
    }
    cookie = _app_chat_cookie(access_token, profile.cf_clearance)
    if cookie:
        headers["Cookie"] = cookie
    return headers


def latest_user_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if str(message.get("role") or "user").strip().lower() != "user":
            continue
        text = _message_text(message.get("content"), "user").strip()
        if text:
            return text
    return ""


def build_app_chat_payload(
    spec: ModelSpec,
    body: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    image_generation: bool = False,
) -> dict[str, Any]:
    message = str(body.get("prompt") or "").strip() or latest_user_message(messages)
    if not message:
        raise HTTPException(status_code=400, detail={"error": "Grok app-chat requires a user text message"})
    payload: dict[str, Any] = {
        "message": message,
        "modeId": spec.mode_id or spec.upstream_model or spec.id,
        "enableImageGeneration": image_generation,
        "enableImageStreaming": image_generation,
        "imageGenerationCount": int(body.get("n") or 1) if image_generation else 0,
        "sendFinalMetadata": True,
        "temporary": True,
        "fileAttachments": [],
        "toolOverrides": {},
        "disableSearch": True,
        "disableMemory": True,
        "returnImageBytes": False,
    }
    if spec.model_tier:
        payload["modelTier"] = spec.model_tier
    if spec.prefer_best:
        payload["preferBest"] = True
    return payload


def parse_app_chat_payload_line(line: str | bytes) -> dict[str, Any] | None:
    if isinstance(line, bytes):
        payload = line.decode("utf-8", errors="replace").strip()
    else:
        payload = str(line or "").strip()
    if not payload or payload == "[DONE]":
        return None
    if payload.startswith("data:"):
        payload = payload[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def app_chat_line_events(lines: Iterable[str | bytes]) -> Iterator[dict[str, Any]]:
    for line in lines:
        event = parse_app_chat_payload_line(line)
        if event is not None:
            yield event


def extract_app_chat_token(event: dict[str, Any]) -> tuple[str, bool]:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    token = response.get("token")
    if not token:
        return "", False
    return str(token), response.get("isThinking") is True


def is_app_chat_final_event(event: dict[str, Any]) -> bool:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    if response.get("isSoftStop") is True:
        return True
    if "finalMetadata" in response or "finalMetadata" in result:
        return True
    return False


def _app_chat_json_data(attachment: dict[str, Any]) -> dict[str, Any]:
    raw_json_data = attachment.get("jsonData")
    if isinstance(raw_json_data, dict):
        return raw_json_data
    if isinstance(raw_json_data, str):
        try:
            decoded = json.loads(raw_json_data)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def extract_app_chat_image_url(event: dict[str, Any]) -> str:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    attachment = response.get("cardAttachment") if isinstance(response.get("cardAttachment"), dict) else {}
    json_data = _app_chat_json_data(attachment)
    chunk = json_data.get("image_chunk") if isinstance(json_data.get("image_chunk"), dict) else {}
    progress = chunk.get("progress")
    moderated = chunk.get("moderated") is True or chunk.get("isModerated") is True
    image_url = str(chunk.get("imageUrl") or "").strip()
    if progress != 100 or moderated or not image_url:
        return ""
    if image_url.startswith(("http://", "https://")):
        return image_url
    return GROK_ASSET_BASE_URL + image_url.lstrip("/")


def collect_app_chat_response(events: Iterable[dict[str, Any]]) -> dict[str, str]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    for event in events:
        token, thinking = extract_app_chat_token(event)
        if token:
            if thinking:
                reasoning_parts.append(token)
            else:
                content_parts.append(token)
        if is_app_chat_final_event(event):
            break
    return {"content": "".join(content_parts), "reasoning_content": "".join(reasoning_parts)}


class GrokAppChatClient:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self.network_profile = _grok_console_profile()
        self.session = create_session(impersonate=self.network_profile.impersonate, verify=self.network_profile.verify)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "GrokAppChatClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _call_with_retry(self, fn, policy=None, context=""):
        from services.network.retry import retry_call, RetryPolicy

        api_policy = policy or RetryPolicy(
            max_attempts=3,
            retry_statuses=frozenset({408, 429, 500, 502, 503, 504}),
        )
        deadline = time.monotonic() + 60.0

        def on_retry(attempt, status_code, exc):
            logger.warning({
                "event": "grok_app_chat_retry",
                "context": context,
                "attempt": attempt,
                "status_code": status_code,
                "error": str(exc) if exc else None,
            })

        return retry_call(fn, policy=api_policy, deadline=deadline, on_retry=on_retry)

    def stream_events(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        try:
            response = self._call_with_retry(
                lambda: self.session.post(
                    APP_CHAT_NEW_CONVERSATION_URL,
                    headers=app_chat_headers(self.access_token),
                    json=payload,
                    timeout=self.network_profile.timeout,
                    stream=True,
                ),
                context="app_chat",
            )
        except requests.exceptions.RequestException as exc:
            raise GrokConsoleError(f"Grok app-chat upstream request failed: {exc}", 502) from exc
        if response.status_code >= 400:
            status = int(response.status_code)
            raise GrokConsoleError(f"Grok app-chat upstream error (HTTP {status})", _openai_status(status), status)
        yield from app_chat_line_events(response.iter_lines())


def app_chat_completion_events(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    from services.account_service import account_service

    access_token = account_service.get_text_access_token(provider=GROK_PROVIDER)
    if not access_token:
        raise HTTPException(status_code=503, detail={"error": "no available Grok account"})
    payload = build_app_chat_payload(spec, body, messages)
    try:
        with GrokAppChatClient(access_token) as client:
            yield from client.stream_events(payload)
    except GrokConsoleError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error": str(exc)}) from exc
    account_service.mark_text_used(access_token)


def app_chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> dict[str, str]:
    return collect_app_chat_response(app_chat_completion_events(body, spec, messages))


def app_chat_image_outputs(body: dict[str, Any], spec: ModelSpec, prompt: str, n: int = 1) -> Iterator[ImageOutput]:
    if not is_supported_grok_app_chat_image_model(spec.id):
        raise ImageGenerationError(
            f"unsupported Grok image model: {spec.id}",
            status_code=400,
            error_type="invalid_request_error",
            code="unsupported_model",
            param="model",
        )
    request_body = dict(body)
    request_body["prompt"] = prompt
    request_body["n"] = n
    payload = build_app_chat_payload(spec, request_body, [{"role": "user", "content": prompt}], image_generation=True)

    from services.account_service import account_service

    access_token = account_service.get_text_access_token(provider=GROK_PROVIDER)
    if not access_token:
        raise ImageGenerationError("no available Grok account", status_code=503, code="no_available_account")
    image_items: list[dict[str, Any]] = []
    try:
        with GrokAppChatClient(access_token) as client:
            for event in client.stream_events(payload):
                token, thinking = extract_app_chat_token(event)
                if token and not thinking:
                    yield ImageOutput(kind="progress", model=spec.id, index=1, total=n, text=token, upstream_event_type="app_chat.token")
                image_url = extract_app_chat_image_url(event)
                if image_url:
                    image_items.append({"url": image_url, "revised_prompt": prompt})
                if is_app_chat_final_event(event):
                    break
    except GrokConsoleError as exc:
        raise ImageGenerationError(str(exc), status_code=exc.status_code) from exc
    account_service.mark_text_used(access_token)
    if image_items:
        yield ImageOutput(kind="result", model=spec.id, index=1, total=n, data=format_image_result(image_items, prompt, "url")["data"])
        return
    raise ImageGenerationError("Grok image generation did not return an image", status_code=502)


def console_chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> GrokConsoleCompletion:
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
    completion = extract_console_completion(response_json)
    if not completion.content and not completion.reasoning_content:
        raise HTTPException(status_code=502, detail={"error": "Grok upstream response did not contain text"})
    return completion


def chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> str:
    return console_chat_completion(body, spec, messages).content
