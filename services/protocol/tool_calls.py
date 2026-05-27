from __future__ import annotations

import html
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParsedToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolParseResult:
    calls: list[ParsedToolCall] = field(default_factory=list)
    saw_tool_syntax: bool = False


def normalize_openai_tools(tools: object) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            function = tool["function"]
            name = str(function.get("name") or "").strip()
            if name:
                normalized.append({"type": "function", "function": dict(function)})
            continue
        if tool.get("type") == "function" and tool.get("name"):
            normalized.append({
                "type": "function",
                "function": {
                    "name": str(tool.get("name") or "").strip(),
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("parameters") or {},
                },
            })
    return normalized


def tool_names(tools: object) -> list[str]:
    names: list[str] = []
    for tool in normalize_openai_tools(tools):
        raw_function = tool.get("function")
        function = raw_function if isinstance(raw_function, dict) else {}
        name = str(function.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def has_function_tools(body: dict[str, Any]) -> bool:
    return bool(tool_names(body.get("tools")))


def build_tool_system_prompt(tools: object, tool_choice: object = None, parallel_tool_calls: object = None) -> str:
    normalized = normalize_openai_tools(tools)
    blocks: list[str] = []
    for tool in normalized:
        raw_function = tool.get("function")
        function = raw_function if isinstance(raw_function, dict) else {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        description = str(function.get("description") or "").strip()
        parameters = function.get("parameters") or {}
        blocks.append(
            "Tool: " + name + "\n"
            "Description: " + description + "\n"
            "Parameters: " + json.dumps(parameters, ensure_ascii=False, separators=(",", ":"))
        )
    if not blocks:
        return ""
    lines = [
        "You have access to the following tools.",
        "AVAILABLE TOOLS:",
        "\n\n".join(blocks),
        "TOOL CALL FORMAT:",
        "- When calling tools, output ONLY a tool-call structure and no prose or markdown fences.",
        "- Prefer this XML format:",
        '<tool_calls><tool_call><tool_name>TOOL_NAME</tool_name><parameters>{"key":"value"}</parameters></tool_call></tool_calls>',
        '- A JSON object {"tool_calls":[{"name":"TOOL_NAME","arguments":{"key":"value"}}]} or JSON array [{"name":"TOOL_NAME","arguments":{"key":"value"}}] is also accepted.',
        "- arguments/parameters must be a valid JSON object.",
    ]
    if parallel_tool_calls is False:
        lines.append("- Call at most one tool.")
    lines.append(_tool_choice_instruction(normalized, tool_choice))
    return "\n".join(lines)


def inject_tool_prompt(messages: list[dict[str, Any]], tools: object, tool_choice: object = None, parallel_tool_calls: object = None) -> list[dict[str, Any]]:
    prompt = build_tool_system_prompt(tools, tool_choice, parallel_tool_calls)
    if not prompt:
        return messages
    injected = _serialize_tool_history(messages)
    for message in injected:
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            message["content"] = f"{str(message.get('content') or '').strip()}\n\n{prompt}".strip()
            return injected
    return [{"role": "system", "content": prompt}, *injected]


def _serialize_tool_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    injected: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        role = item.get("role")
        if role == "assistant" and item.get("tool_calls"):
            content = str(item.get("content") or "").strip()
            xml = tool_calls_to_xml(item.get("tool_calls"))
            item["content"] = f"{content}\n{xml}".strip() if content else xml
            item.pop("tool_calls", None)
        elif role == "tool":
            tool_call_id = str(item.get("tool_call_id") or "").strip()
            label = f"[tool result for {tool_call_id}]" if tool_call_id else "[tool result]"
            item["role"] = "user"
            item["content"] = f"{label}:\n{str(item.get('content') or '')}"
            item.pop("tool_call_id", None)
        injected.append(item)
    return injected


def tool_calls_to_xml(calls: object) -> str:
    if not isinstance(calls, list):
        return ""
    parts = ["<tool_calls>"]
    for call in calls:
        if not isinstance(call, dict):
            continue
        raw_function = call.get("function")
        function = raw_function if isinstance(raw_function, dict) else {}
        name = str(function.get("name") or call.get("name") or "").strip()
        arguments = function.get("arguments") or call.get("arguments") or "{}"
        if not name:
            continue
        parts.append("<tool_call>")
        parts.append(f"<tool_name>{html.escape(name)}</tool_name>")
        parts.append(f"<parameters>{html.escape(_json_arguments(arguments))}</parameters>")
        parts.append("</tool_call>")
    parts.append("</tool_calls>")
    return "".join(parts)


def parse_tool_calls(text: str, available_tools: list[str] | None = None) -> ToolParseResult:
    text = _strip_fences(text or "").strip()
    if not text:
        return ToolParseResult()
    saw_tool_syntax = bool(_TOOL_SYNTAX_RE.search(text))
    if not saw_tool_syntax:
        return ToolParseResult()
    calls = _parse_xml_tool_calls(text) or _parse_json_envelope(text) or _parse_json_array(text) or _parse_alt_xml(text)
    if available_tools:
        available = set(available_tools)
        calls = [call for call in calls if call.name in available]
    return ToolParseResult(calls=calls, saw_tool_syntax=saw_tool_syntax)


def strip_tool_markup(text: str) -> str:
    return _TOOL_MARKUP_RE.sub("", text or "").strip()


def chat_tool_call_response(model: str, calls: list[ParsedToolCall], messages: list[dict[str, Any]] | None = None, created: int | None = None) -> dict[str, Any]:
    prompt_tokens = _count_messages(messages, model) if messages else 0
    completion_tokens = _count_tool_calls(calls, model) if messages else 0
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created or int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": None, "tool_calls": openai_tool_calls(calls)},
            "finish_reason": "tool_calls",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def openai_tool_calls(calls: list[ParsedToolCall]) -> list[dict[str, Any]]:
    return [
        {
            "id": call.call_id,
            "type": "function",
            "function": {"name": call.name, "arguments": call.arguments},
        }
        for call in calls
    ]


def response_function_call_items(calls: list[ParsedToolCall]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"fc_{uuid.uuid4().hex}",
            "type": "function_call",
            "call_id": call.call_id,
            "name": call.name,
            "arguments": call.arguments,
            "status": "completed",
        }
        for call in calls
    ]


def _tool_choice_instruction(tools: list[dict[str, Any]], tool_choice: object) -> str:
    if tool_choice == "none":
        return "WHEN TO CALL: Do not call tools; respond in plain text only."
    if tool_choice in ("required", True):
        return "WHEN TO CALL: You must call one or more tools."
    if isinstance(tool_choice, dict):
        raw_function = tool_choice.get("function")
        function = raw_function if isinstance(raw_function, dict) else {}
        name = str(function.get("name") or tool_choice.get("name") or "").strip()
        if name:
            known = {str(function_item.get("name")) for tool in tools for function_item in [tool.get("function")] if isinstance(function_item, dict) and function_item.get("name")}
            if not known or name in known:
                return f'WHEN TO CALL: You must call the tool named "{name}".'
    return "WHEN TO CALL: Call a tool when it is needed; otherwise respond in plain text."


_TOOL_SYNTAX_RE = re.compile(r"<tool_calls\b|<tool_call\b|<function_call\b|<invoke\b|\"tool_calls\"\s*:|\btool_calls\b|^\s*\[", re.IGNORECASE)
_TOOL_MARKUP_RE = re.compile(r"(?is)<tool_calls\b[^>]*>.*?</tool_calls>|<tool_call\b[^>]*>.*?</tool_call>|<function_call\b[^>]*>.*?</function_call>|<invoke\b[^>]*>.*?</invoke>")
_XML_ROOT_RE = re.compile(r"(?is)<tool_calls\b[^>]*>(.*?)</tool_calls>")
_XML_CALL_RE = re.compile(r"(?is)<tool_call\b[^>]*>(.*?)</tool_call>")
_JSON_DECODER = json.JSONDecoder()


def _parse_xml_tool_calls(text: str) -> list[ParsedToolCall]:
    root = _XML_ROOT_RE.search(text)
    if not root:
        return []
    calls = []
    for match in _XML_CALL_RE.finditer(root.group(1)):
        inner = match.group(1)
        name = _xml_value(inner, "tool_name") or _xml_value(inner, "name") or _xml_value(inner, "function")
        parameters = _xml_value(inner, "parameters") or _xml_value(inner, "arguments") or _xml_value(inner, "input") or "{}"
        arguments = _parse_arguments(parameters)
        if name:
            calls.append(_make_call(name, arguments if isinstance(arguments, dict) else {}))
    return calls


def _parse_json_envelope(text: str) -> list[ParsedToolCall]:
    if '"tool_calls"' not in text and "'tool_calls'" not in text:
        return []
    obj = _extract_json_value(text, "{")
    if not isinstance(obj, dict):
        return []
    raw_calls = obj.get("tool_calls")
    return _calls_from_items(raw_calls) if isinstance(raw_calls, list) else []


def _parse_json_array(text: str) -> list[ParsedToolCall]:
    array = _extract_json_value(text, "[")
    return _calls_from_items(array) if isinstance(array, list) else []


def _parse_alt_xml(text: str) -> list[ParsedToolCall]:
    calls: list[ParsedToolCall] = []
    for match in re.finditer(r"(?is)<function_call\b[^>]*>(.*?)</function_call>", text):
        inner = match.group(1)
        name = _xml_value(inner, "name") or _xml_value(inner, "tool_name")
        arguments = _parse_arguments(_xml_value(inner, "arguments") or _xml_value(inner, "parameters") or "{}")
        if name:
            calls.append(_make_call(name, arguments if isinstance(arguments, dict) else {}))
    for match in re.finditer(r"(?is)<invoke\b[^>]*name=[\"']?([\w.-]+)[\"']?[^>]*>(.*?)</invoke>", text):
        arguments = _parse_arguments(match.group(2).strip())
        calls.append(_make_call(match.group(1).strip(), arguments if arguments is not None else {}))
    return calls


def _calls_from_items(items: list[Any]) -> list[ParsedToolCall]:
    calls = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_function = item.get("function")
        function = raw_function if isinstance(raw_function, dict) else {}
        name = str(item.get("name") or item.get("tool_name") or function.get("name") or "").strip()
        arguments = item.get("input") if "input" in item else item.get("arguments") if "arguments" in item else item.get("parameters") if "parameters" in item else function.get("arguments")
        if name:
            calls.append(_make_call(name, arguments if arguments is not None else {}))
    return calls


def _make_call(name: str, arguments: Any) -> ParsedToolCall:
    return ParsedToolCall(call_id=f"call_{uuid.uuid4().hex}", name=name, arguments=_json_arguments(arguments))


def _json_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        parsed = _parse_arguments(arguments)
        return json.dumps(parsed if isinstance(parsed, dict) else {}, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(arguments if isinstance(arguments, dict) else {}, ensure_ascii=False, separators=(",", ":"))


def _parse_arguments(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    raw = html.unescape(raw.strip())
    if not raw:
        return {}
    cdata = re.fullmatch(r"(?is)<!\[CDATA\[(.*?)]]>", raw)
    if cdata:
        raw = cdata.group(1).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, ValueError):
        xml_args = {match.group(1): _parse_xml_scalar(match.group(2)) for match in re.finditer(r"(?is)<([\w.-]+)\b[^>]*>(.*?)</\1>", raw)}
        return xml_args or None


def _parse_xml_scalar(raw: str) -> Any:
    value = _xml_value(f"<x>{raw}</x>", "x")
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value


def _xml_value(text: str, tag: str) -> str:
    match = re.search(rf"(?is)<{tag}\b[^>]*>(.*?)</{tag}>", text)
    if not match:
        return ""
    value = match.group(1).strip()
    cdata = re.fullmatch(r"(?is)<!\[CDATA\[(.*?)]]>", value)
    return html.unescape(cdata.group(1) if cdata else value).strip()


def _extract_json_value(text: str, opener: str) -> Any:
    start = text.find(opener)
    while start != -1:
        try:
            value, _ = _JSON_DECODER.raw_decode(text, start)
            return value
        except (json.JSONDecodeError, ValueError):
            start = text.find(opener, start + 1)
    return None


def _strip_fences(text: str) -> str:
    fenced = re.fullmatch(r"(?is)\s*```(?:json|xml)?\s*(.*?)\s*```\s*", text)
    return fenced.group(1) if fenced else text


def _count_messages(messages: list[dict[str, Any]], model: str) -> int:
    from services.protocol.conversation import count_message_tokens
    return count_message_tokens(messages, model)


def _count_tool_calls(calls: list[ParsedToolCall], model: str) -> int:
    from services.protocol.conversation import count_text_tokens
    return count_text_tokens(json.dumps(openai_tool_calls(calls), ensure_ascii=False), model)
