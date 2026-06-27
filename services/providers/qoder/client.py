from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from services.protocol import tool_calls
from services.network.client import create_session
from services.providers.codebuddy.client import StreamAggregator
from services.providers.qoder.models import DISPLAY_NAME_BY_ID, UPSTREAM_MODEL_BY_ID
from utils.helper import UpstreamHTTPError, ensure_ok

OPENAPI_BASE = os.environ.get("QODER_OPENAPI_BASE", "https://openapi.qoder.com.cn").rstrip("/")
MODEL_BASE = os.environ.get("QODER_MODEL_BASE", "https://gateway.qoder.com.cn").rstrip("/")
TOKEN_ENDPOINT = "/api/v1/jobToken/exchange"
CHAT_ENDPOINT = "/model/v1/chat/completions"
USER_AGENT = "qoder/1.0.32"
QODER_CLI = os.environ.get("QODER_CLI_BIN", "qoderclicn")
QODER_CONFIG_DIR = os.environ.get("QODER_CONFIG_DIR_BASE", str(Path(tempfile.gettempdir()) / "webchat2api-qoder"))
QODER_TRANSPORT = os.environ.get("QODER_TRANSPORT", "auto").strip().lower()
WASM_HELPER = Path(__file__).with_name("wasm_helper.mjs")
_TOKEN_TTL_SECONDS = 20 * 60
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class QoderError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _clean(value: Any) -> str:
    return str(value or "").strip()


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


def _content_block(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    block = dict(item)
    block_type = _clean(block.get("type"))
    if block_type == "input_text":
        block["type"] = "text"
    elif block_type == "input_image":
        block["type"] = "image_url"
        if "image_url" not in block and block.get("source"):
            block["image_url"] = block.pop("source")
    return block


def _message_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    role = _clean(message.get("role"))
    if role not in {"system", "user", "assistant", "tool"}:
        return None
    item: dict[str, Any] = {"role": role}
    content = message.get("content")
    if isinstance(content, list):
        item["content"] = [_content_block(block) for block in content]
    elif content is None:
        item["content"] = None
    else:
        item["content"] = str(content)
    if role == "assistant" and isinstance(message.get("tool_calls"), list):
        item["tool_calls"] = message["tool_calls"]
    if role == "tool" and message.get("tool_call_id"):
        item["tool_call_id"] = _clean(message.get("tool_call_id"))
    for key in ("name", "reasoning_content"):
        if message.get(key):
            item[key] = message[key]
    return item


def _messages_payload(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for message in messages if isinstance(message, dict) and (item := _message_payload(message)) is not None]


def _allowed_body_fields(body: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "stop",
        "max_tokens",
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "parallel_tool_calls",
        "response_format",
        "tool_choice",
        "context_length",
        "seed",
        "user",
        "reasoning_effort",
        "custom_model",
    }
    return {key: body[key] for key in allowed if key in body}


def _cli_path() -> str:
    if os.path.sep in QODER_CLI and Path(QODER_CLI).exists():
        return QODER_CLI
    path = shutil.which(QODER_CLI)
    if not path:
        raise QoderError(f"{QODER_CLI} executable was not found", 500)
    return path


def _node_path() -> str:
    path = shutil.which(os.environ.get("NODE_BIN", "node"))
    if not path:
        raise QoderError("node executable was not found", 500)
    return path


def _text_from_content(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(
            _clean(item.get("text") if isinstance(item, dict) else item)
            for item in content
            if not isinstance(item, dict) or item.get("type") in {"text", "input_text"}
        )
    return _clean(content)


def _prompt_from_messages(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = _clean(message.get("role")) or "user"
        text = _text_from_content(message.get("content"))
        raw_tool_calls = message.get("tool_calls")
        tool_call_parts: list[str] = []
        if isinstance(raw_tool_calls, list):
            for call in raw_tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = _clean(function.get("name") or call.get("name"))
                arguments = function.get("arguments") if "arguments" in function else call.get("arguments")
                if name:
                    value = arguments if isinstance(arguments, str) else json.dumps(arguments or {}, ensure_ascii=False)
                    tool_call_parts.append(f"{name}({value})")
        if tool_call_parts:
            text = "\n".join([part for part in [text, "tool_calls: " + "; ".join(tool_call_parts)] if part])
        if not text:
            continue
        if role == "tool":
            tool_id = _clean(message.get("tool_call_id"))
            label = f"tool_result({tool_id})" if tool_id else "tool_result"
            parts.append(f"{label}: {text}")
        else:
            parts.append(f"{role}: {text}")
    return "\n\n".join(parts) or "Reply OK only."


def _tool_prompt(body: dict[str, Any]) -> str:
    tools = tool_calls.normalize_openai_tools(body.get("tools"))
    if not tools:
        return ""
    specs = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        specs.append({
            "name": function.get("name"),
            "description": function.get("description", ""),
            "parameters": function.get("parameters") or {},
        })
    return (
        "\n\nWhen a tool is needed, output ONLY JSON in this exact shape: "
        '{"status":"call","tool_calls":[{"name":"tool_name","arguments":{}}]}. '
        "If tool results are already present in the conversation, answer from those results and do not repeat the same tool call unless new information is required. "
        "Available tools:\n" + json.dumps(specs, ensure_ascii=False)
    )


def _tool_result_prompt(messages: list[dict[str, Any]]) -> str:
    if any(_clean(message.get("role")) == "tool" for message in messages):
        return "\n\nA tool result is already present above. Continue from that result; do not call the same tool again for the same file or command."
    return ""


def _result_text(stdout: str) -> str:
    text = stdout.strip()
    if not text:
        return ""
    for line in reversed(text.splitlines()):
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            if payload.get("is_error") and isinstance(payload.get("errors"), list):
                raise QoderError("; ".join(str(item) for item in payload["errors"]) or "Qoder CLI failed")
            return _clean(payload.get("result"))
    return text


class QoderClient:
    def __init__(self, account: dict[str, Any], *, timeout: int = 300) -> None:
        self.account = account
        self.pat_token = _clean(account.get("pat_token") or account.get("access_token"))
        if not self.pat_token:
            raise QoderError("Qoder PAT token is required", 401)
        self.session = create_session(account=account, verify=True)
        self.timeout = timeout

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "QoderClient":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _job_token(self) -> str:
        cached = _TOKEN_CACHE.get(self.pat_token)
        if cached and cached[1] > time.time():
            return cached[0]
        response = self.session.post(
            OPENAPI_BASE + TOKEN_ENDPOINT,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "Cosy-Version": "1.0.32",
                "Cosy-ClientType": "5",
                "Cosy-MachineOS": "x86_64_linux",
            },
            json={"personal_token": self.pat_token},
            timeout=60,
        )
        ensure_ok(response, "Qoder token exchange")
        payload = response.json()
        token = _clean(payload.get("token")) if isinstance(payload, dict) else ""
        if not token:
            raise QoderError("Qoder token exchange returned no job token", 502)
        _TOKEN_CACHE[self.pat_token] = (token, time.time() + _TOKEN_TTL_SECONDS)
        return token

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._job_token()}",
            "User-Agent": USER_AGENT,
            "X-Request-ID": uuid.uuid4().hex,
            "X-Session-ID": str(uuid.uuid4()),
        }

    def chat_body(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        payload = {
            **_allowed_body_fields(body),
            "model": UPSTREAM_MODEL_BY_ID.get(model, model.removeprefix("al-")),
            "messages": _messages_payload(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            "metadata": {
                "context": {
                    "request_id": request_id,
                    "request_set_id": request_id,
                    "session_id": str(uuid.uuid4()),
                    "task_id": "chat",
                    "client_type": "5",
                },
            },
        }
        if "tools" in body:
            payload["tools"] = body.get("tools")
        return payload

    def stream_chunks(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
        response = self.session.post(
            MODEL_BASE + CHAT_ENDPOINT,
            headers=self.headers(),
            json=self.chat_body(body, messages, model),
            stream=True,
            timeout=self.timeout,
        )
        ensure_ok(response, "Qoder chat")
        for line in response.iter_lines():
            payload = _parse_sse_payload(line)
            if payload is not None:
                yield payload

    def _cli_chat_text(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> str:
        upstream_model = DISPLAY_NAME_BY_ID.get(model) or UPSTREAM_MODEL_BY_ID.get(model, model.removeprefix("al-"))
        prompt = _prompt_from_messages(messages) + _tool_result_prompt(messages) + _tool_prompt(body)
        config_key = _clean(self.account.get("access_token")).replace(":", "_") or uuid.uuid4().hex
        env = {
            **os.environ,
            "QODERCN_PERSONAL_ACCESS_TOKEN": self.pat_token,
            "QODER_CONFIG_DIR": str(Path(QODER_CONFIG_DIR) / config_key),
            "NO_BROWSER": "1",
        }
        cmd = [_cli_path(), "--bare", "-p", "--tools", "", "--model", upstream_model, "--output-format", "json", prompt]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, env=env)
        if result.returncode != 0:
            raise QoderError((result.stderr or result.stdout or "Qoder CLI failed").strip())
        return _result_text(result.stdout)

    def _cli_response(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        content = self._cli_chat_text(body, messages, model)
        parsed = tool_calls.parse_tool_calls_for_tools(content, body.get("tools"))
        message: dict[str, Any] = {"role": "assistant", "content": tool_calls.strip_tool_markup(content) if parsed.saw_tool_syntax else content}
        finish_reason = "stop"
        if parsed.calls:
            message["content"] = None
            message["tool_calls"] = tool_calls.openai_tool_calls(parsed.calls)
            finish_reason = "tool_calls"
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        }

    def _wasm_chunks(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
        payload = {
            "pat_token": self.pat_token,
            "body": body,
            "messages": messages,
            "model": model,
            "upstream_model": UPSTREAM_MODEL_BY_ID.get(model, model.removeprefix("al-")),
            "display_name": DISPLAY_NAME_BY_ID.get(model, model),
            "model_source": "system",
        }
        config_key = _clean(self.account.get("access_token")).replace(":", "_") or uuid.uuid4().hex
        env = {
            **os.environ,
            "QODER_CONFIG_DIR": str(Path(QODER_CONFIG_DIR) / config_key),
            "NO_BROWSER": "1",
        }
        result = subprocess.run(
            [_node_path(), str(WASM_HELPER)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=env,
        )
        if result.returncode != 0:
            raise QoderError((result.stderr or result.stdout or "Qoder WASM transport failed").strip())
        chunks = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except Exception:
                continue
            if isinstance(chunk, dict):
                chunks.append(chunk)
        if not chunks:
            raise QoderError("Qoder WASM transport returned no chunks")
        return chunks

    def buffered_chunks(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
        if QODER_TRANSPORT == "wasm":
            return self._wasm_chunks(body, messages, model)
        if QODER_TRANSPORT == "api":
            return list(self.stream_chunks(body, messages, model))
        try:
            return self._wasm_chunks(body, messages, model)
        except QoderError:
            if QODER_TRANSPORT == "auto":
                response = self._cli_response(body, messages, model)
                chunks = [{
                    "id": response.get("id"),
                    "object": "chat.completion.chunk",
                    "created": response.get("created"),
                    "model": response.get("model"),
                    "choices": [{
                        "index": 0,
                        "delta": response.get("choices", [{}])[0].get("message", {}),
                        "finish_reason": response.get("choices", [{}])[0].get("finish_reason"),
                    }],
                }]
                return chunks
            raise

    def chat_completion(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        if QODER_TRANSPORT == "cli":
            return self._cli_response(body, messages, model)
        if QODER_TRANSPORT == "wasm":
            return self._wasm_chat_completion(body, messages, model)
        if QODER_TRANSPORT == "api":
            return self._api_chat_completion(body, messages, model)
        try:
            return self._wasm_chat_completion(body, messages, model)
        except (QoderError, UpstreamHTTPError):
            return self._cli_response(body, messages, model)

    def _api_chat_completion(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        aggregator = StreamAggregator()
        for chunk in self.stream_chunks(body, messages, model):
            aggregator.process(chunk)
        return aggregator.response()

    def _wasm_chat_completion(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        aggregator = StreamAggregator()
        for chunk in self._wasm_chunks(body, messages, model):
            aggregator.process(chunk)
        return aggregator.response()
