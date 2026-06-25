from __future__ import annotations

import json
import secrets
import time
import uuid
from typing import Any, Iterator

from services.network.client import create_session
from services.providers.codebuddy.models import UPSTREAM_MODEL_BY_ID
from utils.helper import ensure_ok

BASE_URL = "https://www.codebuddy.cn"
CHAT_ENDPOINT = "/v2/chat/completions"
USER_AGENT = "CLI/1.0.7 CodeBuddy/1.0.7"


class CodeBuddyError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _json_args(value: str) -> str:
    try:
        parsed = json.loads(value)
    except Exception:
        return "{}"
    return json.dumps(parsed if isinstance(parsed, dict) else {}, ensure_ascii=False)


def _parse_sse_payload(line: object) -> dict[str, Any] | None:
    text = line.decode("utf-8", "ignore") if isinstance(line, bytes) else str(line or "")
    text = text.strip()
    if not text.startswith("data:"):
        return None
    data = text[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _codebuddy_tool_choice(value: Any) -> Any:
    if isinstance(value, dict):
        function = value.get("function") if isinstance(value.get("function"), dict) else {}
        name = _clean(function.get("name"))
        return name or "auto"
    return value


def _convert_tool_id(tool_id: str) -> str:
    return f"call_{tool_id[8:]}" if tool_id.startswith("tooluse_") else tool_id


def _convert_tool_call(call: dict[str, Any], index_by_id: dict[str, int]) -> dict[str, Any]:
    item = dict(call)
    tool_id = _clean(item.get("id"))
    if tool_id:
        item["id"] = _convert_tool_id(tool_id)
        index_by_id.setdefault(tool_id, len(index_by_id))
        item["index"] = index_by_id[tool_id]
    elif index_by_id:
        item["index"] = max(index_by_id.values())
    return item


def convert_stream_chunk(chunk: dict[str, Any], index_by_id: dict[str, int] | None = None) -> dict[str, Any]:
    choices = chunk.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    calls = delta.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return chunk
    converted = dict(chunk)
    converted_choices = list(choices)
    converted_choice = dict(choice)
    converted_delta = dict(delta)
    indexes = index_by_id if index_by_id is not None else {}
    converted_delta["tool_calls"] = [_convert_tool_call(call, indexes) for call in calls if isinstance(call, dict)]
    converted_choice["delta"] = converted_delta
    converted_choices[0] = converted_choice
    converted["choices"] = converted_choices
    return converted


class StreamAggregator:
    def __init__(self) -> None:
        self.response_id = ""
        self.model = ""
        self.content: list[str] = []
        self.finish_reason = ""
        self.usage: dict[str, Any] | None = None
        self.tool_order: list[str] = []
        self.tools: dict[str, dict[str, Any]] = {}
        self.current_tool_id = ""

    def process(self, chunk: dict[str, Any]) -> None:
        self.response_id = self.response_id or _clean(chunk.get("id"))
        self.model = self.model or _clean(chunk.get("model"))
        if isinstance(chunk.get("usage"), dict):
            self.usage = chunk["usage"]
        choices = chunk.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        self.finish_reason = _clean(choice.get("finish_reason")) or self.finish_reason
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        if isinstance(delta.get("content"), str):
            self.content.append(delta["content"])
        calls = delta.get("tool_calls")
        if isinstance(calls, list):
            self._process_tools(calls)

    def _process_tools(self, calls: list[Any]) -> None:
        for raw in calls:
            call = raw if isinstance(raw, dict) else {}
            tool_id = _clean(call.get("id"))
            if tool_id:
                tool_id = _convert_tool_id(tool_id)
                self.current_tool_id = tool_id
                if tool_id not in self.tools:
                    self.tools[tool_id] = {"id": tool_id, "type": "function", "function": {"name": "", "arguments": ""}}
                    self.tool_order.append(tool_id)
            elif not self.current_tool_id:
                continue
            tool = self.tools[self.current_tool_id]
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            if function.get("name"):
                tool["function"]["name"] = _clean(function.get("name"))
            if function.get("arguments"):
                tool["function"]["arguments"] += str(function.get("arguments") or "")

    def response(self) -> dict[str, Any]:
        calls = []
        for tool_id in self.tool_order:
            call = self.tools[tool_id]
            call["function"]["arguments"] = _json_args(str(call["function"].get("arguments") or "{}"))
            calls.append(call)
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.content)}
        if calls:
            message["tool_calls"] = calls
        response = {
            "id": self.response_id or f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.model or "codebuddy",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else (self.finish_reason or "stop")}],
        }
        if self.usage:
            response["usage"] = self.usage
        return response


class CodeBuddyClient:
    def __init__(self, account: dict[str, Any], *, timeout: int = 300) -> None:
        self.account = account
        self.bearer_token = _clean(account.get("bearer_token") or account.get("access_token"))
        if not self.bearer_token:
            raise CodeBuddyError("CodeBuddy bearer token is required", 401)
        self.user_id = _clean(account.get("user_id")) or "b5be3a67-237e-4ee6-9b9a-0b9ecd7b454b"
        self.session = create_session(account=account, verify=True)
        self.timeout = timeout

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "CodeBuddyClient":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def headers(self) -> dict[str, str]:
        request_id = uuid.uuid4().hex
        return {
            "Host": "www.codebuddy.cn",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "x-stainless-arch": "x64",
            "x-stainless-lang": "js",
            "x-stainless-os": "Windows",
            "x-stainless-package-version": "5.10.1",
            "x-stainless-retry-count": "0",
            "x-stainless-runtime": "node",
            "x-stainless-runtime-version": "v22.13.1",
            "X-Conversation-ID": _clean(self.account.get("conversation_id")) or str(uuid.uuid4()),
            "X-Conversation-Request-ID": secrets.token_hex(16),
            "X-Conversation-Message-ID": uuid.uuid4().hex,
            "X-Request-ID": request_id,
            "X-Agent-Intent": "craft",
            "X-IDE-Type": "CLI",
            "X-IDE-Name": "CLI",
            "X-IDE-Version": "1.0.7",
            "X-API-Key": self.bearer_token,
            "X-Domain": "www.codebuddy.cn",
            "User-Agent": USER_AGENT,
            "X-Product": "SaaS",
            "X-User-Id": self.user_id,
        }

    def chat_body(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        payload = {key: value for key, value in body.items() if key not in {"stream", "messages"}}
        if "tool_choice" in payload:
            payload["tool_choice"] = _codebuddy_tool_choice(payload.get("tool_choice"))
        payload["model"] = UPSTREAM_MODEL_BY_ID.get(model, model.removeprefix("tx-"))
        payload["messages"] = messages
        payload["stream"] = True
        if len(messages) == 1 and messages[0].get("role") == "user":
            payload["messages"] = [{"role": "system", "content": "You are a helpful assistant."}, *messages]
        return payload

    def stream_chunks(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
        response = self.session.post(
            BASE_URL + CHAT_ENDPOINT,
            headers=self.headers(),
            json=self.chat_body(body, messages, model),
            stream=True,
            timeout=self.timeout,
        )
        ensure_ok(response, "CodeBuddy chat")
        index_by_id: dict[str, int] = {}
        for line in response.iter_lines():
            payload = _parse_sse_payload(line)
            if payload is not None:
                yield convert_stream_chunk(payload, index_by_id)

    def chat_completion(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        aggregator = StreamAggregator()
        for chunk in self.stream_chunks(body, messages, model):
            aggregator.process(chunk)
        return aggregator.response()
