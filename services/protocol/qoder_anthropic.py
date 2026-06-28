from __future__ import annotations

import json
import queue
import re
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

from services.protocol import tool_calls
from services.protocol.conversation import count_message_tokens, count_text_tokens
from services.providers.qoder import chat as qoder_chat

_JSON_DECODER = json.JSONDecoder()
_TEXT_WRAPPER_RE = re.compile(
    r'\{[^{}]*"text"\s*:\s*"((?:\\.|[^"\\])*)"[^{}]*"type"\s*:\s*"text"[^{}]*\}'
    r'|\{[^{}]*"type"\s*:\s*"text"[^{}]*"text"\s*:\s*"((?:\\.|[^"\\])*)"[^{}]*\}'
)
_MAX_CONTENT_UNWRAP_DEPTH = 8
STREAM_PING_INTERVAL_SECONDS = 10.0
_PING = object()
_CLAUDE_CODE_TOOL_HINT = (
    "Claude Code tool rules for this environment: use Edit for file writes and file creation; create files with old_string empty and new_string set to the full file content. "
    "Do not use Bash heredocs, shell redirection, or inline multi-line Python to write files; these are blocked by local safety checks. "
    "Do not use unavailable tools such as Write unless they are explicitly listed in the available tools. "
    "After each tool result, continue the original task until all requested files, commands, and tests are complete."
)
_EMPTY_TOOL_REPLY_RETRY = (
    "Continue the original task now. Do not return an empty response. "
    "If files need to be created or changed, use the available Edit tool. "
    "If checks are needed, use Bash only for safe read/test commands."
)


def _system_text(system: object) -> str:
    if isinstance(system, str):
        return system.strip()
    if isinstance(system, list):
        return "\n".join(
            str(item.get("text") or "")
            for item in system
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
    return ""


def _json_args(value: object) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = {}
        return json.dumps(parsed if isinstance(parsed, dict) else {}, ensure_ascii=False)
    return json.dumps(value if isinstance(value, dict) else {}, ensure_ascii=False)


def _tool_input(arguments: object) -> dict[str, object]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _tool_names(tools: object) -> set[str]:
    return set(tool_calls.tool_names(tools))


def _tool_use_block(call_id: str, name: str, arguments: object, tools: object) -> dict[str, object] | None:
    args = _tool_input(arguments)
    available = _tool_names(tools)
    if name == "Write" and "Write" not in available and "Edit" in available:
        file_path = str(args.get("file_path") or "").strip()
        content = str(args.get("content") or "")
        if not file_path:
            return None
        return {
            "type": "tool_use",
            "id": call_id,
            "name": "Edit",
            "input": {"file_path": file_path, "old_string": "", "new_string": content, "replace_all": False},
        }
    if available and name not in available:
        return None
    return {"type": "tool_use", "id": call_id, "name": name, "input": args}


def _text_from_tool_result(content: object) -> str:
    blocks = _content_blocks(content)
    return "\n".join(str(block.get("text") or "") for block in blocks if block.get("type") == "text")


def _assistant_message_from_blocks(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    text_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in blocks:
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block_type == "thinking":
            text_parts.append(str(block.get("thinking") or ""))
        elif block_type == "tool_use":
            calls.append({
                "id": str(block.get("id") or f"call_{uuid.uuid4().hex}"),
                "type": "function",
                "function": {
                    "name": str(block.get("name") or ""),
                    "arguments": _json_args(block.get("input")),
                },
            })
    message: dict[str, Any] = {"role": "assistant", "content": "\n".join(part for part in text_parts if part)}
    if calls:
        message["tool_calls"] = calls
        if not message["content"]:
            message["content"] = None
    return message


def _user_messages_from_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in blocks:
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block_type == "tool_result":
            tool_use_id = str(block.get("tool_use_id") or "")
            content = _text_from_tool_result(block.get("content"))
            if tool_use_id:
                messages.append({"role": "tool", "tool_call_id": tool_use_id, "content": content})
            else:
                text_parts.append(content)
        elif block_type == "image":
            text_parts.append(json.dumps(block, ensure_ascii=False))
    if text_parts or not messages:
        messages.append({"role": "user", "content": "\n".join(text_parts)})
    return messages


def _message_to_qoder(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = str(message.get("role") or "user")
    content = message.get("content")
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
    if role == "assistant":
        return [_assistant_message_from_blocks(blocks)]
    if role == "user":
        return _user_messages_from_blocks(blocks)
    return [{"role": role, "content": "" if content is None else str(content)}]


def _openai_tool_choice(choice: object) -> object:
    mode, forced = tool_calls.tool_choice_mode(choice)
    if mode == "none":
        return "none"
    if mode == "required":
        return "required"
    if mode == "forced":
        return {"type": "function", "function": {"name": forced}}
    return None


def qoder_body(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system = _system_text(body.get("system"))
    if body.get("tools") is not None:
        system = "\n\n".join(part for part in (system, _CLAUDE_CODE_TOOL_HINT) if part)
    if system:
        messages.append({"role": "system", "content": system})
    raw_messages = body.get("messages")
    if isinstance(raw_messages, list):
        for message in raw_messages:
            if isinstance(message, dict):
                messages.extend(_message_to_qoder(message))
    payload: dict[str, Any] = {
        "model": str(body.get("model") or "auto").strip() or "auto",
        "messages": messages,
        "stream": False,
    }
    for src, dst in (
        ("max_tokens", "max_tokens"),
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("stop_sequences", "stop"),
    ):
        if src in body and body.get(src) is not None:
            payload[dst] = body[src]
    if body.get("tools") is not None:
        payload["tools"] = tool_calls.normalize_openai_tools(body.get("tools"))
    tool_choice = _openai_tool_choice(body.get("tool_choice"))
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    return payload


def count_tokens(body: dict[str, Any]) -> dict[str, Any]:
    payload = qoder_body(dict(body))
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    return {"input_tokens": count_message_tokens(messages, str(payload.get("model") or "auto"))}


def _loads_typed_json(text: str) -> object:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return text
    try:
        return json.loads(stripped)
    except Exception:
        return text


def _is_typed_content(value: object) -> bool:
    if isinstance(value, dict):
        item_type = str(value.get("type") or "")
        return item_type in {"text", "input_text", "output_text"} or "content" in value
    if isinstance(value, list):
        return any(_is_typed_content(item) for item in value)
    return False


def _is_typed_text(text: str) -> bool:
    parsed = _loads_typed_json(text)
    return (parsed is not text and _is_typed_content(parsed)) or bool(_typed_text_fragments(text))


def _typed_text_fragments(text: str) -> list[str]:
    if '"type"' not in text or '"text"' not in text:
        return []
    values: list[str] = []
    for match in _TEXT_WRAPPER_RE.finditer(text):
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        try:
            values.append(json.loads(f'"{raw}"'))
        except Exception:
            values.append(raw)
    return values


def _json_typed_fragments(text: str) -> list[object]:
    values: list[object] = []
    index = 0
    while index < len(text):
        next_positions = [pos for pos in (text.find("{", index), text.find("[", index)) if pos >= 0]
        if not next_positions:
            break
        start = min(next_positions)
        try:
            parsed, end = _JSON_DECODER.raw_decode(text[start:])
        except Exception:
            index = start + 1
            continue
        if _is_typed_content(parsed):
            values.append(parsed)
        index = start + max(end, 1)
    return values


def _unescaped_text(value: str) -> str:
    if "\\\"" not in value and "\\n" not in value and "\\\\" not in value:
        return value
    try:
        return value.encode("utf-8").decode("unicode_escape")
    except Exception:
        return value


def _content_blocks(value: object, depth: int = 0) -> list[dict[str, object]]:
    if depth > _MAX_CONTENT_UNWRAP_DEPTH:
        return [{"type": "text", "text": "" if value is None else str(value)}]
    if isinstance(value, str):
        parsed = _loads_typed_json(value)
        if parsed is not value and _is_typed_content(parsed):
            return _content_blocks(parsed, depth + 1)
        json_fragments = _json_typed_fragments(value)
        if json_fragments:
            blocks: list[dict[str, object]] = []
            for fragment in json_fragments:
                blocks.extend(_content_blocks(fragment, depth + 1))
            return blocks or [{"type": "text", "text": ""}]
        fragments = _typed_text_fragments(value)
        if fragments:
            blocks: list[dict[str, object]] = []
            for text in fragments:
                blocks.extend(_content_blocks(text, depth + 1) if text != value and _is_typed_text(text) else [{"type": "text", "text": text}])
            return blocks or [{"type": "text", "text": ""}]
        unescaped = _unescaped_text(value)
        if unescaped != value and (_is_typed_text(unescaped) or _json_typed_fragments(unescaped)):
            return _content_blocks(unescaped, depth + 1)
        return [{"type": "text", "text": value}]
    if isinstance(value, list):
        blocks: list[dict[str, object]] = []
        for item in value:
            if isinstance(item, dict):
                item_type = str(item.get("type") or "text")
                if item_type in {"text", "input_text", "output_text"}:
                    text = str(item.get("text") or "")
                    if _is_typed_text(text):
                        blocks.extend(_content_blocks(text, depth + 1))
                    else:
                        blocks.append({"type": "text", "text": text})
                elif "content" in item:
                    blocks.extend(_content_blocks(item.get("content"), depth + 1))
                else:
                    blocks.append({"type": "text", "text": json.dumps(item, ensure_ascii=False)})
            elif item is not None:
                blocks.append({"type": "text", "text": str(item)})
        return blocks or [{"type": "text", "text": ""}]
    if isinstance(value, dict):
        item_type = str(value.get("type") or "")
        if item_type in {"text", "input_text", "output_text"}:
            text = str(value.get("text") or "")
            return _content_blocks(text, depth + 1) if _is_typed_text(text) else [{"type": "text", "text": text}]
        if "content" in value:
            return _content_blocks(value.get("content"), depth + 1)
        return [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]
    return [{"type": "text", "text": "" if value is None else str(value)}]


def _anthropic_stop_reason(finish_reason: str) -> str:
    if finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return "end_turn"


def _response_parts(response: dict[str, Any]) -> tuple[list[dict[str, object]], str, dict[str, Any]]:
    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = _content_blocks(message.get("content"))
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = str(function.get("name") or call.get("name") or "")
            if not name:
                continue
            block = _tool_use_block(
                str(call.get("id") or f"toolu_{uuid.uuid4().hex}"),
                name,
                function.get("arguments") if "arguments" in function else call.get("arguments"),
                response.get("_qoder_tools"),
            )
            if block:
                content.append(block)
    if any(block.get("type") == "tool_use" for block in content):
        content = [block for block in content if block.get("type") != "text" or str(block.get("text") or "").strip()]
    text = "\n".join(str(block.get("text") or "") for block in content if block.get("type") == "text")
    parsed = tool_calls.parse_tool_calls_for_tools(text, response.get("_qoder_tools"))
    if parsed.calls:
        content = []
        for call in parsed.calls:
            block = _tool_use_block(call.call_id, call.name, call.arguments, response.get("_qoder_tools"))
            if block:
                content.append(block)
        if not content:
            return [{"type": "text", "text": ""}], "stop", response.get("usage") if isinstance(response.get("usage"), dict) else {}
        return content, "tool_calls", response.get("usage") if isinstance(response.get("usage"), dict) else {}
    return content or [{"type": "text", "text": ""}], str(choice.get("finish_reason") or ""), response.get("usage") if isinstance(response.get("usage"), dict) else {}


def _is_empty_tool_response(content: list[dict[str, object]], finish_reason: str, tools: object) -> bool:
    if not _tool_names(tools) or finish_reason == "tool_calls":
        return False
    return not any(str(block.get("text") or "").strip() for block in content if block.get("type") == "text")


def _raw_completion_with_empty_retry(payload: dict[str, Any]) -> dict[str, Any]:
    response = qoder_chat.raw_chat_completion(payload, payload["messages"], str(payload["model"]))
    if payload.get("tools") is not None:
        response["_qoder_tools"] = payload.get("tools")
    content, finish_reason, _ = _response_parts(response)
    if not _is_empty_tool_response(content, finish_reason, payload.get("tools")):
        return response
    retry_payload = {**payload, "messages": [*payload["messages"], {"role": "user", "content": _EMPTY_TOOL_REPLY_RETRY}]}
    retry = qoder_chat.raw_chat_completion(retry_payload, retry_payload["messages"], str(retry_payload["model"]))
    if payload.get("tools") is not None:
        retry["_qoder_tools"] = payload.get("tools")
    return retry


def non_stream_response(body: dict[str, Any]) -> dict[str, Any]:
    payload = qoder_body(dict(body))
    response = _raw_completion_with_empty_retry(payload)
    content, finish_reason, usage = _response_parts(response)
    text = "\n".join(str(block.get("text") or "") for block in content if block.get("type") == "text")
    return {
        "id": f"msg_{uuid.uuid4()}",
        "type": "message",
        "role": "assistant",
        "model": str(body.get("model") or payload["model"]),
        "content": content,
        "stop_reason": _anthropic_stop_reason(finish_reason),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or count_message_tokens(payload["messages"], str(payload["model"]))),
            "output_tokens": int(usage.get("completion_tokens") or count_text_tokens(text, str(payload["model"]))),
        },
    }


def stream_events(body: dict[str, Any]) -> Iterator[dict[str, object]]:
    payload = qoder_body({**body, "stream": False})
    message_id = f"msg_{uuid.uuid4()}"
    input_tokens = count_message_tokens(payload["messages"], str(payload["model"]))
    yield {"type": "message_start", "message": {"id": message_id, "type": "message", "role": "assistant", "model": str(body.get("model") or payload["model"]), "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": input_tokens, "output_tokens": 0}}}
    response: dict[str, Any] | None = None
    for item in _completion_with_ping(payload):
        if item is _PING:
            yield {"type": "ping"}
        else:
            response = item
    if response is None:
        response = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {}}
    content, finish_reason, usage = _response_parts(response)
    output_text: list[str] = []
    for index, block in enumerate(content):
        if block.get("type") == "tool_use":
            input_json = json.dumps(block.get("input") if isinstance(block.get("input"), dict) else {}, ensure_ascii=False)
            yield {"type": "content_block_start", "index": index, "content_block": {"type": "tool_use", "id": str(block.get("id") or f"toolu_{uuid.uuid4().hex}"), "name": str(block.get("name") or ""), "input": {}}}
            yield {"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": input_json}}
            yield {"type": "content_block_stop", "index": index}
            continue
        text = str(block.get("text") or "")
        output_text.append(text)
        yield {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}
        if text:
            yield {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}}
        yield {"type": "content_block_stop", "index": index}
    output_tokens = int(usage.get("completion_tokens") or count_text_tokens("\n".join(output_text), str(payload["model"])))
    yield {"type": "message_delta", "delta": {"stop_reason": _anthropic_stop_reason(finish_reason), "stop_sequence": None}, "usage": {"output_tokens": output_tokens}}
    yield {"type": "message_stop"}


def _completion_with_ping(payload: dict[str, Any]) -> Iterator[dict[str, Any] | object]:
    items: queue.Queue[tuple[str, object]] = queue.Queue()

    def produce() -> None:
        try:
            response = _raw_completion_with_empty_retry(payload)
            items.put(("response", response))
        except Exception as exc:
            items.put(("error", exc))

    threading.Thread(target=produce, daemon=True).start()
    interval = max(float(STREAM_PING_INTERVAL_SECONDS), 0.001)
    while True:
        try:
            kind, value = items.get(timeout=interval)
        except queue.Empty:
            yield _PING
            continue
        if kind == "error":
            raise value
        yield value if isinstance(value, dict) else {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {}}
        return


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, object]]:
    return stream_events(body) if body.get("stream") else non_stream_response(body)
