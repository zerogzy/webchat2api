from __future__ import annotations

import html
import json
import os
import queue
import re
import shlex
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from services.account_service import account_service
from services.openai_backend_api import OpenAIBackendAPI
from services.providers.base import CATPAW_PROVIDER
from services.providers.catpaw import conversation as catpaw_conversation
from services.providers.catpaw.models import type_code_for
from services.providers.registry import resolve_model
from services.protocol.conversation import count_message_tokens, count_text_tokens, normalize_messages
from services.protocol.openai_v1_chat_complete import collect_chat_content, stream_text_chat_completion

@dataclass
class MessageRequest:
    backend: OpenAIBackendAPI | None
    messages: list[dict[str, Any]]
    model: str
    tools: Any = None
    catpaw_mode: bool = False
    """When True, the request routes to CatPaw provider instead of GPT backend."""
    catpaw_conversation_id: str | None = None


def _tool_meta(tool: Mapping[str, object]) -> tuple[str, str, object]:
    function = tool.get("function")
    fn = function if isinstance(function, Mapping) else {}
    name = str(tool.get("name") or fn.get("name") or "").strip()
    desc = str(tool.get("description") or fn.get("description") or "").strip()
    schema = tool.get("input_schema") or tool.get("parameters") or fn.get("input_schema") or fn.get("parameters") or {}
    return name, desc, schema


def _tool_choice_none(tool_choice: object) -> bool:
    return tool_choice == "none" or (isinstance(tool_choice, dict) and str(tool_choice.get("type") or "").strip() == "none")


def _forced_tool_name(tool_choice: object) -> str:
    if not isinstance(tool_choice, dict):
        return ""
    if str(tool_choice.get("type") or "").strip() == "tool":
        return str(tool_choice.get("name") or "").strip()
    function = tool_choice.get("function") if isinstance(tool_choice.get("function"), Mapping) else {}
    return str(function.get("name") or tool_choice.get("name") or "").strip()


def _tool_choice_directive(tool_choice: object) -> str:
    forced = _forced_tool_name(tool_choice)
    if forced:
        return f'- You MUST call the tool named "{forced}" in this response.'
    if tool_choice in ("any", "required", True) or (isinstance(tool_choice, dict) and str(tool_choice.get("type") or "").strip() == "any"):
        return "- You MUST call at least one tool in this response."
    return "- Call a tool when it is needed; otherwise respond in plain text."


def build_tool_prompt(tools: object, tool_choice: object = None) -> str:
    if _tool_choice_none(tool_choice) or not isinstance(tools, list):
        return ""
    blocks = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name, desc, schema = _tool_meta(tool)
        if name:
            blocks.append(
                "Tool: " + name + "\n"
                "Description: " + desc + "\n"
                "Parameters (JSON Schema): " + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            )
    if not blocks:
        return ""
    return (
        "You can call the tools listed below to read/write files, run commands, "
        "and inspect the project. When a task needs the local environment, call a "
        "tool instead of guessing or claiming you cannot access files.\n\n"
        "AVAILABLE TOOLS:\n" + "\n\n".join(blocks) + "\n\n"
        "TO CALL TOOLS, output ONLY this XML and nothing else (no prose, no markdown fences):\n"
        '<tool_calls><tool_name>TOOL_NAME</tool_name>'
        '<parameters>{"param":"value"}</parameters></tool_calls>\n'
        "- <parameters> must be a single valid JSON object using the exact parameter names from the schema.\n"
        "- Do NOT wrap each call in a <tool_call> element; put <tool_name> directly inside <tool_calls>.\n"
        "- To call several tools at once, repeat <tool_name>...</tool_name><parameters>...</parameters> inside the same <tool_calls> block.\n"
        "- Keep multi-line or code values as one JSON string with \\n escapes.\n"
        "- For Windows file paths, either use / separators or escape each backslash as \\\\ inside JSON strings.\n"
        f"{_tool_choice_directive(tool_choice)}\n"
        "- After tool results are returned, keep going until the task is done, then give the final answer."
    )


def merge_system(system: object, extra: str) -> object:
    system = compact_system(system)
    if not extra:
        return system
    if isinstance(system, str) and system.strip():
        return f"{system.strip()}\n\n{extra}"
    if isinstance(system, list):
        return [*system, {"type": "text", "text": extra}]
    return extra


def compact_system(system: object) -> object:
    if isinstance(system, str):
        return _compact_system_text(system)
    if isinstance(system, list):
        result = []
        for item in system:
            if isinstance(item, dict) and str(item.get("type") or "") == "text":
                copied = dict(item)
                copied["text"] = _compact_system_text(str(item.get("text") or ""))
                result.append(copied)
            else:
                result.append(item)
        return result
    return system


def _compact_system_text(text: str) -> str:
    return text or ""


def _compact_message_text(text: str) -> str:
    return text or ""


def preprocess_payload(payload: dict[str, object], text_mapper: Callable[[str], str] | None = None) -> dict[str, object]:
    payload["messages"] = preprocess_messages(payload.get("messages"), text_mapper)
    if _tool_choice_none(payload.get("tool_choice")):
        payload["tools"] = None
    payload["system"] = merge_system(payload.get("system"), build_tool_prompt(payload.get("tools"), payload.get("tool_choice")))
    return payload


def message_request(body: dict[str, Any]) -> MessageRequest:
    payload = preprocess_payload(dict(body))
    model = str(payload.get("model") or "auto").strip() or "auto"
    # Route CatPaw provider models directly to CatPaw, bypassing the GPT backend.
    catpaw_mode = resolve_model(model).provider == CATPAW_PROVIDER
    backend = None if catpaw_mode else OpenAIBackendAPI(access_token=account_service.get_text_access_token())
    messages = normalize_messages(payload.get("messages"), payload.get("system"))
    catpaw_conversation_id = (
        catpaw_conversation.conversation_id_for_anthropic_request(
            messages,
            model,
            payload.get("tools"),
            session_key=_catpaw_session_key_from_headers(payload.get("_request_headers")),
        )
        if catpaw_mode
        else None
    )
    return MessageRequest(
        backend=backend,
        messages=messages,
        model=model,
        tools=payload.get("tools"),
        catpaw_mode=catpaw_mode,
        catpaw_conversation_id=catpaw_conversation_id,
    )


def reset_catpaw_conversation_cache_for_tests() -> None:
    catpaw_conversation.reset_cache_for_tests()


def _catpaw_session_key_from_headers(headers: object) -> str:
    if not isinstance(headers, Mapping):
        return ""
    for name in (
        "x-claude-code-session-id",
        "claude-code-session-id",
        "x-catapw-conversation-key",
        "x-catpaw-conversation-key",
    ):
        value = str(headers.get(name) or "").strip()
        if value:
            return value
    return ""


def count_tokens(body: dict[str, Any]) -> dict[str, Any]:
    """Anthropic /v1/messages/count_tokens: estimate input tokens for a request.

    Counts the same prompt the chat path would send (system + injected tool
    definitions + messages), so Claude Code's context bookkeeping stays accurate.
    """
    payload = preprocess_payload(dict(body))
    messages = normalize_messages(payload.get("messages"), payload.get("system"))
    model = str(payload.get("model") or "auto").strip() or "auto"
    return {"input_tokens": count_message_tokens(messages, model)}


def preprocess_messages(messages: object, text_mapper: Callable[[str], str] | None = None) -> object:
    if not isinstance(messages, list):
        return messages
    mapper = text_mapper or (lambda text: text)
    result = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        item = dict(message)
        content = item.get("content")
        if isinstance(content, str):
            item["content"] = _compact_message_text(mapper(content))
        elif isinstance(content, list):
            item["content"] = [_preprocess_block(block, mapper) for block in content]
        result.append(item)
    return result


def _tool_result_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "image":
                    parts.append("[image]")
                elif item.get("type") == "tool_result":
                    parts.append(_tool_result_text(item.get("content")))
                else:
                    parts.append(str(item.get("text") or ""))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    if content is None:
        return ""
    return str(content).strip()


def _preprocess_block(block: object, text_mapper: Callable[[str], str]) -> object:
    if not isinstance(block, dict):
        return block
    block_type = str(block.get("type") or "")
    if block_type == "text":
        item = dict(block)
        item["text"] = _compact_message_text(text_mapper(str(block.get("text") or "")))
        return item
    # Convert thinking/redacted_thinking blocks to plain text for non-Claude upstream.
    # (mirrors cc-switch's normalize_anthropic_tool_thinking_history)
    if block_type == "thinking":
        thinking_text = str(block.get("thinking") or "").strip()
        if thinking_text:
            return {"type": "text", "text": thinking_text}
        return {"type": "text", "text": ""}  # strip empty thinking
    if block_type == "redacted_thinking":
        # redacted_thinking cannot be forwarded to non-Claude upstream; drop it
        return {"type": "text", "text": ""}
    if block_type == "tool_use":
        return {"type": "text", "text": f"<tool_calls><tool_name>{block.get('name') or ''}</tool_name><parameters>{json.dumps(block.get('input') or {}, ensure_ascii=False)}</parameters></tool_calls>"}
    if block_type == "tool_result":
        label = "Tool error" if block.get("is_error") else "Tool result"
        tool_use_id = str(block.get("tool_use_id") or "").strip()
        if tool_use_id:
            label = f"{label} (id={tool_use_id})"
        body = _tool_result_text(block.get("content"))
        return {"type": "text", "text": f"{label}:\n{body}" if body else f"{label}."}
    if block_type == "image":
        source = block.get("source")
        if isinstance(source, dict):
            source_type = str(source.get("type") or "")
            if source_type == "base64":
                media = str(source.get("media_type") or "image/png")
                data = str(source.get("data") or "")
                if data:
                    return {"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}}
            elif source_type == "url":
                url = str(source.get("url") or "")
                if url:
                    return {"type": "image_url", "image_url": {"url": url}}
    return block


def message_response(model: str, text: str, input_tokens: int, output_tokens: int, tools: object = None) -> dict[str, object]:
    content, stop_reason = content_blocks(text, tools)
    return {
        "id": f"msg_{uuid.uuid4()}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _available_tool_names(tools: object) -> list[str]:
    if not isinstance(tools, list):
        return []
    names: list[str] = []
    for tool in tools:
        if isinstance(tool, dict):
            name, _desc, _schema = _tool_meta(tool)
            if name:
                names.append(name)
    return names


def content_blocks(text: str, tools: object = None) -> tuple[list[dict[str, object]], str]:
    raw = text or ""
    calls = parse_tool_calls(raw, _available_tool_names(tools)) if isinstance(tools, list) and tools else []
    if calls:
        # If the model used an explicit marker, the visible text is whatever sits
        # outside it. If it emitted a bare JSON tool envelope, the whole output was
        # the call, so there is no visible text.
        visible = strip_tool_markup(raw) if _TOOL_OPEN_RE.search(raw) else ""
        content: list[dict[str, object]] = []
        if visible:
            content.append({"type": "text", "text": visible})
        content.extend(
            {"type": "tool_use", "id": f"toolu_{uuid.uuid4()}", "name": name, "input": normalize_tool_input(name, args)}
            for name, args in calls
        )
        return content, "tool_use"
    return [{"type": "text", "text": strip_tool_markup(raw)}], "end_turn"


def normalize_tool_input(name: str, args: dict[str, object]) -> dict[str, object]:
    if isinstance(args.get("parameters"), dict) and len(args) == 1:
        args = dict(args["parameters"])
    if name != "Bash" or not isinstance(args.get("command"), str):
        return args
    result = dict(args)
    result["command"] = _windows_paths_to_posix_command(str(args.get("command") or ""))
    return result


def _windows_paths_to_posix_command(command: str) -> str:
    def repl(match: re.Match[str]) -> str:
        drive = match.group(1).lower()
        path = match.group(2).replace("\\", "/")
        return f"/{drive}/{path.lstrip('/')}"

    return _WINDOWS_PATH_IN_COMMAND_RE.sub(repl, command)


def strip_tool_markup(text: str) -> str:
    text = text or ""
    # closed tool/function blocks
    text = re.sub(r"(?is)<tool_calls\b[^>]*>.*?</tool_calls>|<tool_call\b[^>]*>.*?</tool_call>|<function_call\b[^>]*>.*?</function_call>|<invoke\b[^>]*>.*?</invoke>", "", text)
    # an unclosed tool block: drop from the opening marker to the end of the text
    text = re.sub(r"(?is)<tool_calls\b.*$|<tool_call\b.*$|<function_call\b.*$|<invoke\b.*$", "", text)
    # leftover stray tags
    text = re.sub(r"(?is)</?(?:tool_calls|tool_call|tool_name|parameters|function_call|invoke)\b[^>]*>", "", text)
    return text.strip()


_TOOL_OPEN_RE = re.compile(r"(?is)<tool_calls\b|<tool_call\b|<tool_name\b|<function_call\b|<invoke\b|\btool_call>\s*[A-Za-z_]")
_TOOL_MARKER_TOKENS = ("<tool_calls", "<tool_call", "<tool_name", "<function_call", "<invoke", "tool_call>")
_TOOL_MARKER_MAX = max(len(token) for token in _TOOL_MARKER_TOKENS)
_JSON_DECODER = json.JSONDecoder()
STREAM_PING_INTERVAL_SECONDS = 10.0
_PING = object()
_PATH_LIKE_JSON_VALUE_RE = re.compile(
    r'(?is)("(?:(?:file_)?path|path|cwd|notebook_path|command)"\s*:\s*")((?:\\.|[^"\\])*)(")'
)
_WINDOWS_PATH_IN_COMMAND_RE = re.compile(r"(?<![\w/])([A-Za-z]):\\+([^\s\"'<>|;&]+(?:\\+[^\s\"'<>|;&]+)*)")


def streamable_text(text: str) -> str:
    """Visible-text prefix that is safe to stream right now.

    Stops at the first complete tool marker, and also holds back a trailing run
    that could be the *start* of a marker split across stream chunks, so a partial
    ``<tool_c`` never leaks into the assistant text. The held-back tail is flushed
    (or recognised as a tool call) once more text arrives or the turn finishes.
    """
    text = text or ""
    match = _TOOL_OPEN_RE.search(text)
    if match:
        return text[:match.start()]
    lowered = text.lower()
    window = max(0, len(text) - _TOOL_MARKER_MAX + 1)
    for i in range(window, len(text)):
        if lowered[i] not in ("<", "t"):
            continue
        tail = lowered[i:]
        if any(token.startswith(tail) for token in _TOOL_MARKER_TOKENS):
            return text[:i]
    return text


def parse_tool_calls(text: str, available_tools: list[str] | None = None) -> list[tuple[str, dict[str, object]]]:
    """Extract (name, args) tool calls from model output.

    Tuned to what the CatPaw models actually emit: a ``<tool_calls>`` block with
    ``<tool_name>NAME</tool_name><parameters>{json}</parameters>`` pairs. The inner
    ``<tool_call>`` wrapper is intentionally ignored because the models frequently
    corrupt it into a garbage token. JSON-envelope and ``name=`` attribute forms are
    accepted as fallbacks.
    """
    region = _tool_calls_region(text or "")
    calls = _name_param_calls(region)
    if calls:
        return calls
    calls = _attr_calls(region)
    if calls:
        return calls
    calls = _json_tool_calls(region)
    if calls:
        return calls
    calls = _tool_element_calls(region, available_tools or [])
    if calls:
        return calls
    calls = _loose_tool_call_calls(region, available_tools or [])
    if calls:
        return calls
    return _name_near_json_calls(region, available_tools or [])


def _tool_calls_region(text: str) -> str:
    """Return the content inside the first <tool_calls> marker (handles a missing
    closing tag); if there is no marker, return the whole text for JSON fallbacks."""
    open_match = re.search(r"(?is)<tool_calls\b[^>]*>", text)
    if not open_match:
        return text
    rest = text[open_match.end():]
    close_match = re.search(r"(?is)</tool_calls\s*>", rest)
    return rest[:close_match.start()] if close_match else rest


def _name_param_calls(region: str) -> list[tuple[str, dict[str, object]]]:
    names = list(re.finditer(r"(?is)<tool_name\b[^>]*>(.*?)</tool_name\s*>", region))
    result: list[tuple[str, dict[str, object]]] = []
    for index, match in enumerate(names):
        name = _unwrap_xml(match.group(1))
        if not name:
            continue
        end = names[index + 1].start() if index + 1 < len(names) else len(region)
        result.append((name, _params_from_segment(region[match.end():end])))
    return result


def _params_from_segment(segment: str) -> dict[str, object]:
    closed = re.search(r"(?is)<parameters\b[^>]*>(.*?)</parameters\s*>", segment)
    if closed:
        return parse_tool_params(closed.group(1))
    opened = re.search(r"(?is)<parameters\b[^>]*>(.*)$", segment)
    candidate = opened.group(1) if opened else segment
    obj, _ = _first_json_object(candidate)
    return obj if isinstance(obj, dict) else parse_tool_params(candidate)


def _attr_calls(region: str) -> list[tuple[str, dict[str, object]]]:
    result: list[tuple[str, dict[str, object]]] = []
    pattern = r"(?is)<(?:tool_call|invoke|function_call)\b[^>]*\bname\s*=\s*[\"']?([\w.\-]+)[\"']?[^>]*>(.*?)(?:</(?:tool_call|invoke|function_call)\s*>|$)"
    for match in re.finditer(pattern, region):
        obj, _ = _first_json_object(match.group(2))
        args = obj if isinstance(obj, dict) else parse_tool_params(match.group(2))
        result.append((match.group(1), args))
    return result


def _json_tool_calls(region: str) -> list[tuple[str, dict[str, object]]]:
    obj, _ = _first_json_object(region)
    items: list[object] = []
    if isinstance(obj, dict):
        if isinstance(obj.get("tool_calls"), list):
            items = list(obj["tool_calls"])
        elif obj.get("name") or obj.get("tool_name"):
            items = [obj]
    if not items:
        array, _ = _first_json_array(region)
        if isinstance(array, list):
            items = array
    result: list[tuple[str, dict[str, object]]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(item.get("name") or item.get("tool_name") or function.get("name") or "").strip()
        args = item.get("arguments") if "arguments" in item else item.get("parameters") if "parameters" in item else item.get("input") if "input" in item else function.get("arguments")
        if isinstance(args, str):
            parsed, _ = _first_json_object(args)
            args = parsed if isinstance(parsed, dict) else {}
        if name:
            result.append((name, args if isinstance(args, dict) else {}))
    return result


def _tool_element_calls(region: str, available_tools: list[str]) -> list[tuple[str, dict[str, object]]]:
    available = {tool for tool in available_tools if tool}
    if not available:
        return []
    result: list[tuple[str, dict[str, object]]] = []
    for match in re.finditer(r"(?is)<([A-Za-z_][\w.\-]*)\b[^>]*>(.*?)</\1\s*>", region):
        name = match.group(1)
        if name not in available:
            continue
        args = {
            child.group(1): parse_tool_value(child.group(2))
            for child in re.finditer(r"(?is)<([\w.\-]+)\b[^>]*>(.*?)</\1\s*>", match.group(2))
        }
        if not args:
            args = parse_tool_params(match.group(2))
        result.append((name, args))
    return result


def _loose_tool_call_calls(region: str, available_tools: list[str]) -> list[tuple[str, dict[str, object]]]:
    result: list[tuple[str, dict[str, object]]] = []
    for match in re.finditer(r"(?is)(?:<)?tool_call>\s*([A-Za-z_][\w.\-]*)", region):
        obj, _ = _first_json_object(region[match.end():])
        if not isinstance(obj, dict):
            continue
        coerced = _coerce_tool_call(match.group(1), obj, available_tools)
        if coerced:
            result.append(coerced)
    return result


def _coerce_tool_call(
    name: str,
    args: dict[str, object],
    available_tools: list[str],
) -> tuple[str, dict[str, object]] | None:
    available = {tool for tool in available_tools if tool}
    if name in available:
        return name, args
    if name == "Glob" and "Bash" in available:
        bash_args = _glob_args_to_bash(args)
        if bash_args:
            return "Bash", bash_args
    return None


def _glob_args_to_bash(args: dict[str, object]) -> dict[str, object]:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return {}
    normalized = re.sub(r"^([A-Za-z]):[\\/]+", lambda m: f"/{m.group(1).lower()}/", pattern)
    normalized = normalized.replace("\\", "/")
    parent, leaf = normalized.rsplit("/", 1) if "/" in normalized else (".", normalized)
    if not leaf:
        leaf = "*"
    return {
        "command": f"find {_quote_shell_path(parent)} -maxdepth 1 -iname {shlex.quote(leaf)} -print",
        "description": f"Find files matching {normalized}",
    }


def _quote_shell_path(path: str) -> str:
    if path == "~" or path.startswith("~/"):
        return "~" + (shlex.quote(path[1:]) if len(path) > 1 else "")
    return shlex.quote(path)


def _name_near_json_calls(region: str, available_tools: list[str]) -> list[tuple[str, dict[str, object]]]:
    """Recover from corrupted wrapper tags by pairing a known tool name before
    the first JSON object with that object as the tool input.

    Some CatPaw models can replace XML wrapper tokens with junk under the old
    nested format while still emitting the tool name and argument JSON. This is a
    last-resort compatibility path; the normal flat XML format does not rely on it.
    """
    obj, start = _first_json_object(region)
    if not isinstance(obj, dict) or start < 0:
        return []
    prefix = re.sub(r"(?is)<[^>]+>", " ", region[:start])
    for name in sorted({str(item) for item in available_tools if item}, key=len, reverse=True):
        if re.search(rf"(?is)(^|[^\w.\-]){re.escape(name)}([^\w.\-]|$)", prefix):
            return [(name, obj)]
    return []


def _first_json_object(text: str) -> tuple[object, int]:
    index = text.find("{")
    while index != -1:
        value, end = _raw_decode_json_object(text, index)
        if isinstance(value, dict):
            return value, end
        index = text.find("{", index + 1)
    return None, -1


def _first_json_array(text: str) -> tuple[object, int]:
    index = text.find("[")
    while index != -1:
        value, end = _raw_decode_json_array(text, index)
        if isinstance(value, list):
            return value, end
        index = text.find("[", index + 1)
    return None, -1


def _unwrap_xml(value: str) -> str:
    value = (value or "").strip()
    cdata = re.fullmatch(r"(?is)<!\[CDATA\[(.*?)]]>", value)
    return html.unescape(cdata.group(1) if cdata else value).strip()


def xml_value(text: str, tag: str) -> str:
    match = re.search(rf"(?is)<{tag}\b[^>]*>(.*?)</{tag}>", text)
    if not match:
        return ""
    value = match.group(1).strip()
    cdata = re.fullmatch(r"(?is)<!\[CDATA\[(.*?)]]>", value)
    return html.unescape(cdata.group(1) if cdata else value).strip()


def parse_tool_params(raw: str) -> dict[str, object]:
    raw = html.unescape(raw.strip())
    parsed = _json_object_from_text(raw)
    if isinstance(parsed, dict):
        return parsed
    xml_args = {m.group(1): parse_tool_value(m.group(2)) for m in re.finditer(r"(?is)<([\w.-]+)\b[^>]*>(.*?)</\1>", raw)}
    return xml_args


def _json_object_from_text(raw: str) -> dict[str, object] | None:
    for candidate in _json_candidates(raw):
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_candidates(raw: str) -> Iterator[str]:
    path_repaired = _escape_path_like_json_values(raw)
    yield path_repaired
    if path_repaired != raw:
        yield raw


def _escape_path_like_json_values(raw: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return match.group(1) + _escape_single_backslashes(match.group(2)) + match.group(3)

    return _PATH_LIKE_JSON_VALUE_RE.sub(repl, raw)


def _escape_single_backslashes(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue
        result.append("\\\\")
        if index + 1 < len(value) and value[index + 1] == "\\":
            index += 2
        else:
            index += 1
    return "".join(result)


def _raw_decode_json_object(text: str, index: int) -> tuple[object, int]:
    candidates = [text]
    path_repaired = _escape_path_like_json_values(text)
    if path_repaired != text:
        candidates.insert(0, path_repaired)
    for candidate in candidates:
        try:
            value, end = _JSON_DECODER.raw_decode(candidate, index)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value, end
    return None, -1


def _raw_decode_json_array(text: str, index: int) -> tuple[object, int]:
    candidates = [text]
    path_repaired = _escape_path_like_json_values(text)
    if path_repaired != text:
        candidates.insert(0, path_repaired)
    for candidate in candidates:
        try:
            value, end = _JSON_DECODER.raw_decode(candidate, index)
        except ValueError:
            continue
        if isinstance(value, list):
            return value, end
    return None, -1


def parse_tool_value(raw: str) -> object:
    value = xml_value(f"<x>{raw}</x>", "x")
    try:
        return json.loads(value)
    except Exception:
        return value


def _first_choice(chunk: Mapping[str, object]) -> Mapping[str, object]:
    choices = chunk.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        return choices[0]
    return {}


def _chunks_with_ping(chunks: Iterable[dict[str, object]]) -> Iterator[dict[str, object] | object]:
    items: queue.Queue[tuple[str, object]] = queue.Queue()

    def produce() -> None:
        try:
            for chunk in chunks:
                items.put(("chunk", chunk))
        except Exception as exc:
            items.put(("error", exc))
        finally:
            items.put(("done", None))

    threading.Thread(target=produce, daemon=True).start()
    interval = max(float(STREAM_PING_INTERVAL_SECONDS), 0.001)
    while True:
        try:
            kind, payload = items.get(timeout=interval)
        except queue.Empty:
            yield _PING
            continue
        if kind == "chunk":
            yield payload
        elif kind == "error":
            raise payload
        else:
            return


def stream_events(chunks: Iterable[dict[str, object]], model: str, input_tokens: int, output_tokens: Callable[[str], int], tools: object = None) -> Iterator[dict[str, object]]:
    message_id = f"msg_{uuid.uuid4()}"
    created = int(time.time())
    current_text = ""
    streamed_text = ""
    tool_mode = isinstance(tools, list) and bool(tools)
    text_open = False
    finished = False
    yield {"type": "message_start", "message": {"id": message_id, "type": "message", "role": "assistant", "model": model, "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": input_tokens, "output_tokens": 0}}}
    if not tool_mode:
        text_open = True
        yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
    for chunk in _chunks_with_ping(chunks):
        if chunk is _PING:
            yield {"type": "ping"}
            continue
        if not isinstance(chunk, Mapping):
            continue
        choice = _first_choice(chunk)
        delta_value = choice.get("delta")
        delta = delta_value if isinstance(delta_value, Mapping) else {}
        content_delta = delta.get("content")
        text_delta = content_delta if isinstance(content_delta, str) else ""
        if text_delta:
            current_text += text_delta
            visible_text = current_text if not tool_mode else streamable_text(current_text)
            if visible_text.startswith(streamed_text) and len(visible_text) > len(streamed_text):
                piece = visible_text[len(streamed_text):]
                if not text_open:
                    text_open = True
                    yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
                streamed_text = visible_text
                yield {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": piece}}
        if choice.get("finish_reason"):
            finished = True
            break
    content, stop_reason = content_blocks(current_text, tools)
    final_text = str(content[0].get("text") or "") if content and content[0].get("type") == "text" else ""
    if final_text.startswith(streamed_text) and len(final_text) > len(streamed_text):
        if not text_open:
            text_open = True
            yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
        yield {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": final_text[len(streamed_text):]}}
    if text_open:
        yield {"type": "content_block_stop", "index": 0}
    if stop_reason == "tool_use":
        tool_blocks = [block for block in content if block.get("type") == "tool_use"]
        yield from _stream_buffered_blocks(tool_blocks, 1 if text_open else 0)
    yield {"type": "message_delta", "delta": {"stop_reason": stop_reason if finished else "end_turn", "stop_sequence": None}, "usage": {"output_tokens": output_tokens(current_text)}}
    yield {"type": "message_stop", "created": created}


def _stream_buffered_blocks(content: list[dict[str, object]], start_index: int = 0) -> Iterator[dict[str, object]]:
    for offset, block in enumerate(content):
        index = start_index + offset
        if block["type"] == "tool_use":
            start = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block.get("input") or {}, ensure_ascii=False)}
        else:
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block.get("text") or ""}
        yield {"type": "content_block_start", "index": index, "content_block": start}
        yield {"type": "content_block_delta", "index": index, "delta": delta}
        yield {"type": "content_block_stop", "index": index}


def _catpaw_text_deltas(messages: list[dict[str, Any]], model: str, conversation_id: str | None = None) -> Iterator[str]:
    """Stream text deltas from the CatPaw provider."""
    from services.providers.registry import chat_adapter as _chat_adapter
    catpaw_chat = _chat_adapter("catpaw")
    try:
        yield from catpaw_chat.chat_completion_deltas(
            body={"catpaw_conversation_id": conversation_id} if conversation_id else {},
            messages=messages,
            model=model,
        )
    except Exception as e:
        # Log the error and yield an empty string to avoid breaking the stream
        import logging
        logging.getLogger(__name__).error(f"CatPaw stream error: {e}")
        raise


def _catpaw_text(messages: list[dict[str, Any]], model: str, conversation_id: str | None = None) -> str:
    """Collect full text response from the CatPaw provider."""
    from services.providers.registry import chat_adapter as _chat_adapter
    catpaw_chat = _chat_adapter("catpaw")
    return catpaw_chat.chat_completion(
        body={"catpaw_conversation_id": conversation_id} if conversation_id else {},
        messages=messages,
        model=model,
    )


def _catpaw_stream_chunks(
    messages: list[dict[str, Any]],
    model: str,
    tools: object = None,
    conversation_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield OpenAI-format chat completion chunks from CatPaw text deltas.

    CatPaw streams raw text; we wrap each delta into the chunk format that
    ``stream_events`` expects, so the Anthropic SSE envelope layer stays uniform.
    """
    import time as _time
    import uuid as _uuid
    completion_id = f"chatcmpl-{_uuid.uuid4().hex}"
    created = int(_time.time())
    sent_role = False
    full_text_parts: list[str] = []
    for delta_text in _catpaw_text_deltas(messages, model, conversation_id):
        full_text_parts.append(delta_text)
        if not sent_role:
            sent_role = True
            yield {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": delta_text}, "finish_reason": None}],
            }
        else:
            yield {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": delta_text}, "finish_reason": None}],
            }
    # Determine finish_reason based on whether tool calls are present
    full_text = "".join(full_text_parts)
    _debug_dump_catpaw_text(full_text)
    tool_mode = isinstance(tools, list) and bool(tools)
    finish_reason = "stop"
    if tool_mode:
        # Check if the response contains tool calls
        calls = parse_tool_calls(full_text, _available_tool_names(tools))
        if calls:
            finish_reason = "tool_calls"
    if not sent_role:
        yield {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        }
    yield {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
    }


def _debug_dump_catpaw_text(text: str) -> None:
    path = os.environ.get("CATPAW_DEBUG_TEXT_DUMP")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": time.time(), "text": text}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    request = message_request(body)
    input_tokens = count_message_tokens(request.messages, request.model)

    if request.catpaw_mode:
        # --- CatPaw provider path (claude-* / catpaw models) ---
        if body.get("stream"):
            def catpaw_events() -> Iterator[dict[str, Any]]:
                try:
                    yield from stream_events(
                        _catpaw_stream_chunks(
                            request.messages,
                            request.model,
                            request.tools,
                            request.catpaw_conversation_id,
                        ),
                        request.model,
                        input_tokens,
                        lambda text: count_text_tokens(text, request.model),
                        request.tools,
                    )
                except Exception as e:
                    # Log error and yield error event to gracefully close the stream
                    import logging
                    logging.getLogger(__name__).error(f"CatPaw stream error: {e}")
                    # Yield a message_stop event to properly close the stream
                    yield {"type": "message_stop", "error": str(e)}
                    raise
            return catpaw_events()
        text = _catpaw_text(request.messages, request.model, request.catpaw_conversation_id)
        return message_response(
            request.model,
            text,
            input_tokens,
            count_text_tokens(text, request.model),
            request.tools,
        )

    # --- GPT backend path (original logic) ---
    if body.get("stream"):
        def events() -> Iterator[dict[str, Any]]:
            try:
                yield from stream_events(
                    stream_text_chat_completion(request.backend, request.messages, request.model),
                    request.model,
                    input_tokens,
                    lambda text: count_text_tokens(text, request.model),
                    request.tools,
                )
            finally:
                if request.backend is not None:
                    request.backend.close()

        return events()
    try:
        text = collect_chat_content(stream_text_chat_completion(request.backend, request.messages, request.model))
        return message_response(
            request.model,
            text,
            input_tokens,
            count_text_tokens(text, request.model),
            request.tools,
        )
    finally:
        if request.backend is not None:
            request.backend.close()
