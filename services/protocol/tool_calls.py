from __future__ import annotations

import html
import json
import re
import shlex
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
            continue
        name = str(tool.get("name") or "").strip()
        if name:
            normalized.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("input_schema") or tool.get("parameters") or {},
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


def tool_schemas(tools: object) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for tool in normalize_openai_tools(tools):
        raw_function = tool.get("function")
        function = raw_function if isinstance(raw_function, dict) else {}
        name = str(function.get("name") or "").strip()
        parameters = function.get("parameters")
        if name and isinstance(parameters, dict):
            schemas[name] = parameters
    return schemas


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
        "You have access to Claude Code local tools. To read, write, edit, delete files, or run commands, you MUST call these tools; describing an action in prose does not execute it.",
        "AVAILABLE TOOLS:",
        "\n\n".join(blocks),
        "TOOL CALL FORMAT:",
        "- When calling tools, output ONLY a tool-call structure and no prose or markdown fences.",
        "- Prefer this XML format:",
        '<tool_calls><tool_name>TOOL_NAME</tool_name><parameters>{"key":"value"}</parameters></tool_calls>',
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
    for index, message in enumerate(messages):
        item = dict(message)
        role = item.get("role")
        if role == "assistant" and item.get("tool_calls"):
            content = str(item.get("content") or "").strip()
            xml = tool_calls_to_xml(item.get("tool_calls"))
            item["content"] = f"{content}\n{xml}".strip() if content else xml
            item.pop("tool_calls", None)
        elif role == "tool":
            content_text = str(item.get("content") or "")
            tool_call_id = str(item.get("tool_call_id") or "").strip()
            tool_name = _resolve_tool_name_for_id(tool_call_id, injected)
            if tool_name:
                label = f"[result of {tool_name}]"
            elif tool_call_id:
                label = f"[tool result (id={tool_call_id})]"
            else:
                label = "[tool result]"
            item["role"] = "user"
            item["content"] = f"{label}:\n{content_text}" if content_text else f"{label}."
            item.pop("tool_call_id", None)
        injected.append(item)
    return injected


def _resolve_tool_name_for_id(tool_call_id: str, prev_messages: list[dict[str, Any]]) -> str:
    if not tool_call_id:
        return ""
    for prev in reversed(prev_messages):
        if prev.get("role") != "assistant":
            continue
        tool_calls_list = prev.get("tool_calls")
        if not isinstance(tool_calls_list, list):
            continue
        for tc in tool_calls_list:
            if not isinstance(tc, dict):
                continue
            if str(tc.get("id") or "").strip() != tool_call_id:
                continue
            raw_func = tc.get("function")
            func = raw_func if isinstance(raw_func, dict) else {}
            name = str(func.get("name") or tc.get("name") or "").strip()
            if name:
                return name
    return ""


def make_parsed_tool_call(name: str, arguments: Any) -> ParsedToolCall:
    return _make_call(name, arguments)


def parse_gemini_json_tool_calls(text: str, available_tools: list[str] | None = None) -> ToolParseResult:
    text = _strip_fences(text or "").strip()
    if not text:
        return ToolParseResult()
    obj = _extract_json_value(text, "{")
    if not isinstance(obj, dict):
        return ToolParseResult(saw_tool_syntax='"status"' in text or '"tool_calls"' in text)
    status = str(obj.get("status") or "").strip().lower()
    saw_tool_syntax = status in {"call", "text"} or isinstance(obj.get("tool_calls"), list)
    if status == "text":
        return ToolParseResult(saw_tool_syntax=saw_tool_syntax)
    raw_calls = obj.get("tool_calls")
    calls = _calls_from_items(raw_calls) if isinstance(raw_calls, list) else []
    if available_tools:
        available = set(available_tools)
        calls = [call for call in calls if call.name in available]
    return ToolParseResult(calls=calls, saw_tool_syntax=saw_tool_syntax)


def tool_choice_mode(tool_choice: object) -> tuple[str, str]:
    if tool_choice == "none":
        return "none", ""
    if tool_choice in ("required", "any", True):
        return "required", ""
    if isinstance(tool_choice, dict):
        choice_type = str(tool_choice.get("type") or "").strip()
        if choice_type == "none":
            return "none", ""
        if choice_type == "any":
            return "required", ""
        raw_function = tool_choice.get("function")
        function = raw_function if isinstance(raw_function, dict) else {}
        name = str(function.get("name") or tool_choice.get("name") or "").strip()
        if name:
            return "forced", name
    return "auto", ""


def parse_gemini_openai_tool_response(text: str, body: dict[str, Any]) -> ToolParseResult:
    names = tool_names(body.get("tools"))
    if not names:
        return ToolParseResult()
    mode, forced = tool_choice_mode(body.get("tool_choice"))
    if mode == "none":
        return ToolParseResult()
    allowed = [name for name in names if not forced or name == forced]
    parsed = parse_gemini_json_tool_calls(text, allowed) or ToolParseResult()
    if not parsed.calls:
        parsed = parse_tool_calls(text, allowed)
    if parsed.calls:
        return parsed
    if mode in {"required", "forced"} and allowed and parsed.saw_tool_syntax:
        return ToolParseResult(saw_tool_syntax=parsed.saw_tool_syntax)
    return parsed


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
        parts.append(f"<tool_name>{html.escape(name)}</tool_name>")
        parts.append(f"<parameters>{html.escape(_json_arguments(arguments))}</parameters>")
    parts.append("</tool_calls>")
    return "".join(parts)


def parse_tool_calls(text: str, available_tools: list[str] | None = None) -> ToolParseResult:
    text = _strip_fences(text or "").strip()
    if not text:
        return ToolParseResult()
    saw_tool_syntax = bool(_TOOL_SYNTAX_RE.search(text))
    if not saw_tool_syntax and available_tools:
        saw_tool_syntax = _has_function_style_tool_call(text, available_tools)
    if not saw_tool_syntax:
        return ToolParseResult()
    calls = (
        _parse_xml_tool_calls(text)
        or _parse_json_envelope(text)
        or _parse_json_array(text)
        or _parse_alt_xml(text)
        or _parse_loose_xml_tool_calls(text, available_tools or [])
        or _parse_tool_element_calls(text, available_tools or [])
        or _parse_tagless_tool_call_calls(text, available_tools or [])
        or _parse_loose_tool_call_calls(text, available_tools or [])
        or _parse_function_style_calls(text, available_tools or [])
        or _parse_name_near_json_calls(text, available_tools or [])
    )
    if available_tools:
        available = set(available_tools)
        calls = [call for call in calls if call.name in available]
        if not calls and "Bash" in available:
            calls = _parse_glob_as_bash(text)
    return ToolParseResult(calls=calls, saw_tool_syntax=saw_tool_syntax)


def parse_tool_calls_for_tools(text: str, tools: object) -> ToolParseResult:
    parsed = parse_tool_calls(text, tool_names(tools))
    if not parsed.calls:
        return parsed
    return ToolParseResult(calls=repair_tool_calls(parsed.calls, tools), saw_tool_syntax=parsed.saw_tool_syntax)


def repair_tool_calls(calls: list[ParsedToolCall], tools: object) -> list[ParsedToolCall]:
    schemas = tool_schemas(tools)
    if not schemas:
        return calls
    repaired: list[ParsedToolCall] = []
    for call in calls:
        schema = schemas.get(call.name)
        args = _parse_arguments(call.arguments)
        args = args if isinstance(args, dict) else {}
        args = _normalize_tool_arguments(call.name, args)
        if schema and _tool_arguments_match_schema(args, schema):
            repaired.append(ParsedToolCall(call.call_id, call.name, _json_arguments(args)))
    return repaired


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
        choice_type = str(tool_choice.get("type") or "").strip()
        if choice_type == "none":
            return "WHEN TO CALL: Do not call tools; respond in plain text only."
        if choice_type == "any":
            return "WHEN TO CALL: You must call one or more tools."
        raw_function = tool_choice.get("function")
        function = raw_function if isinstance(raw_function, dict) else {}
        name = str(function.get("name") or tool_choice.get("name") or "").strip()
        if name:
            known = {str(function_item.get("name")) for tool in tools for function_item in [tool.get("function")] if isinstance(function_item, dict) and function_item.get("name")}
            if not known or name in known:
                return f'WHEN TO CALL: You must call the tool named "{name}".'
    return "WHEN TO CALL: Call a tool when it is needed; otherwise respond in plain text."


_TOOL_SYNTAX_RE = re.compile(r"<tool_calls\b|<tool_call\b|<function_call\b|<invoke\b|\"tool_calls\"\s*:|\btool_calls\b|^\s*\[|<([A-Za-z_][\w.-]*)\b|\btool_call>", re.IGNORECASE)
_TOOL_MARKUP_RE = re.compile(r"(?is)<tool_calls\b[^>]*>.*?</tool_calls>|<tool_call\b[^>]*>.*?</tool_call>|<function_call\b[^>]*>.*?</function_call>|<invoke\b[^>]*>.*?</invoke>")
_XML_ROOT_RE = re.compile(r"(?is)<tool_calls\b[^>]*>(.*?)</tool_calls>")
_XML_CALL_RE = re.compile(r"(?is)<tool_call\b[^>]*>(.*?)</tool_call>")
_JSON_DECODER = json.JSONDecoder()
_PATH_LIKE_JSON_VALUE_RE = re.compile(
    r'(?is)("(?:(?:file_)?path|path|cwd|notebook_path|command)"\s*:\s*")((?:\\.|[^"\\])*)(")'
)
_WINDOWS_PATH_IN_COMMAND_RE = re.compile(r"(?<![\w/])([A-Za-z]):\\+([^\s\"'<>|;&]+(?:\\+[^\s\"'<>|;&]+)*)")


def _parse_xml_tool_calls(text: str) -> list[ParsedToolCall]:
    root = _XML_ROOT_RE.search(text)
    if not root:
        return []
    inner_text = root.group(1)
    calls = _parse_nested_xml_calls(inner_text)
    if calls:
        return calls
    return _parse_flat_xml_calls(inner_text)


def _parse_nested_xml_calls(inner_text: str) -> list[ParsedToolCall]:
    calls = []
    for match in _XML_CALL_RE.finditer(inner_text):
        inner = match.group(1)
        name = _xml_value(inner, "tool_name") or _xml_value(inner, "name") or _xml_value(inner, "function")
        parameters = _xml_value(inner, "parameters") or _xml_value(inner, "arguments") or _xml_value(inner, "input") or "{}"
        arguments = _parse_arguments(parameters)
        if name:
            calls.append(_make_call(name, arguments if isinstance(arguments, dict) else parameters))
    return calls


def _parse_flat_xml_calls(inner_text: str) -> list[ParsedToolCall]:
    names = list(re.finditer(r"(?is)<tool_name\b[^>]*>(.*?)</tool_name\s*>", inner_text))
    if not names:
        return []
    calls: list[ParsedToolCall] = []
    for idx, match in enumerate(names):
        name = _unwrap_xml(match.group(1))
        if not name:
            continue
        end = names[idx + 1].start() if idx + 1 < len(names) else len(inner_text)
        segment = inner_text[match.end():end]
        parameters = _xml_value(segment, "parameters") or _xml_value(segment, "arguments") or _xml_value(segment, "input") or "{}"
        arguments = _parse_arguments(parameters)
        calls.append(_make_call(name, arguments if isinstance(arguments, dict) else parameters))
    return calls


def _unwrap_xml(value: str) -> str:
    value = (value or "").strip()
    cdata = re.fullmatch(r"(?is)<!\[CDATA\[(.*?)]]>", value)
    return html.unescape(cdata.group(1) if cdata else value).strip()


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
        raw_arguments = _xml_value(inner, "arguments") or _xml_value(inner, "parameters") or "{}"
        arguments = _parse_arguments(raw_arguments)
        if name:
            calls.append(_make_call(name, arguments if isinstance(arguments, dict) else raw_arguments))
    for match in re.finditer(r"(?is)<invoke\b[^>]*name=[\"']?([\w.-]+)[\"']?[^>]*>(.*?)</invoke>", text):
        arguments = _parse_arguments(match.group(2).strip())
        calls.append(_make_call(match.group(1).strip(), arguments if arguments is not None else {}))
    return calls


def _parse_loose_xml_tool_calls(text: str, available_tools: list[str]) -> list[ParsedToolCall]:
    if "<tool_name" not in text or "<parameters" not in text:
        return []
    available = {tool for tool in available_tools if tool}
    calls: list[ParsedToolCall] = []
    for match in re.finditer(r"(?is)<tool_name\b[^>]*>(.*?)</tool_name\s*>", text):
        name = _unwrap_xml(match.group(1))
        if available and name not in available:
            continue
        rest = text[match.end():]
        params_match = re.search(r"(?is)<parameters\b[^>]*>(.*?)(?:</parameters\s*>|</tool_calls\s*>|$)", rest)
        if not params_match:
            continue
        raw = _unwrap_xml(params_match.group(1))
        arguments = _parse_arguments(raw)
        if not isinstance(arguments, dict):
            arguments = _parse_jsonish_arguments(raw)
        if arguments:
            calls.append(_make_call(name, arguments))
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
    return ParsedToolCall(call_id=f"call_{uuid.uuid4().hex}", name=name, arguments=_json_arguments(_normalize_tool_arguments(name, arguments)))


def _normalize_tool_arguments(name: str, arguments: Any) -> Any:
    if isinstance(arguments, str):
        value = arguments.strip()
        if name == "Bash" and value:
            return {"command": _clean_bash_command(value)}
        if name == "Read" and value:
            return {"file_path": value}
        if name == "Glob" and value:
            return {"pattern": value}
    if isinstance(arguments, dict) and isinstance(arguments.get("parameters"), dict) and len(arguments) == 1:
        arguments = dict(arguments["parameters"])
    if isinstance(arguments, dict) and isinstance(arguments.get("input"), dict) and len(arguments) == 1:
        arguments = dict(arguments["input"])
    if isinstance(arguments, dict) and isinstance(arguments.get("arguments"), dict) and len(arguments) == 1:
        arguments = dict(arguments["arguments"])
    if name == "Bash" and isinstance(arguments, dict):
        arguments = dict(arguments)
        for alias in ("cmd", "shell_command", "script", "code"):
            if not arguments.get("command") and isinstance(arguments.get(alias), str):
                arguments["command"] = arguments[alias]
            arguments.pop(alias, None)
    if name == "Bash" and isinstance(arguments, dict) and isinstance(arguments.get("command"), str):
        arguments = dict(arguments)
        command = _clean_bash_command(str(arguments.get("command") or ""))
        command = _command_from_broken_json(command) or command
        arguments["command"] = _windows_paths_to_posix_command(command)
    return arguments


def _tool_arguments_match_schema(arguments: dict[str, Any], schema: dict[str, Any]) -> bool:
    required = schema.get("required")
    if not isinstance(required, list):
        return True
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    for raw_key in required:
        key = str(raw_key)
        if key not in arguments:
            return False
        value = arguments.get(key)
        if isinstance(value, str) and not value.strip():
            return False
        spec = properties.get(key)
        if isinstance(spec, dict) and not _value_matches_json_type(value, spec.get("type")):
            return False
    return True


def _value_matches_json_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_value_matches_json_type(value, item) for item in expected)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


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
    for candidate in _json_candidates(raw):
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            pass
    xml_args = {match.group(1): _parse_xml_scalar(match.group(2)) for match in re.finditer(r"(?is)<([\w.-]+)\b[^>]*>(.*?)</\1>", raw)}
    return xml_args or _parse_jsonish_arguments(raw) or None


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
        for candidate in _json_candidates(text):
            try:
                value, _ = _JSON_DECODER.raw_decode(candidate, start)
                return value
            except (json.JSONDecodeError, ValueError):
                pass
        start = text.find(opener, start + 1)
    return None


def _json_candidates(raw: str) -> list[str]:
    repaired = _escape_path_like_json_values(raw)
    return [repaired, raw] if repaired != raw else [raw]


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


def _windows_paths_to_posix_command(command: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return f"/{match.group(1).lower()}/{match.group(2).replace('\\', '/').lstrip('/')}"

    return _WINDOWS_PATH_IN_COMMAND_RE.sub(repl, command)


def _clean_bash_command(command: str) -> str:
    command = command.strip()
    assignment = re.fullmatch(r"(?is)command\s*=\s*(.+)", command)
    if assignment:
        command = assignment.group(1).strip()
    while len(command) >= 2 and command[0] == command[-1] and command[0] in {'"', "'"}:
        command = command[1:-1].strip()
    return command


def _parse_jsonish_arguments(raw: str) -> dict[str, str]:
    file_path = _jsonish_string_value(raw, "file_path")
    content = _jsonish_string_value(raw, "content")
    command = _jsonish_string_value(raw, "command")
    description = _jsonish_string_value(raw, "description")
    return {key: value for key, value in {"file_path": file_path, "content": content, "command": command, "description": description}.items() if value}


def _jsonish_string_value(raw: str, key: str) -> str:
    match = re.search(rf'(?is)"{re.escape(key)}"\s*:\s*', raw)
    if not match:
        return ""
    rest = raw[match.end():]
    if key in {"content", "command"}:
        next_key = re.search(r'(?is)",\s*"[\w.-]+"\s*:', rest)
        value = rest[:next_key.start() + 1] if next_key else rest.rstrip().removesuffix("}")
        return _clean_jsonish_string(value)
    match = re.match(r'(?is)"((?:\\.|[^"\\])*)"', rest)
    if match:
        return html.unescape(match.group(1))
    bare = re.match(r"(?is)([^,}\s<>]+)", rest)
    return _clean_jsonish_string(html.unescape(bare.group(1).strip())) if bare else ""


def _clean_jsonish_string(value: str) -> str:
    value = value.strip().rstrip(",").strip()
    for quote in ('"""', "'''", '"', "'"):
        while value.startswith(quote) and value.endswith(quote) and len(value) >= len(quote) * 2:
            value = value[len(quote):-len(quote)].strip()
    try:
        value = json.loads(f'"{value}"')
    except (json.JSONDecodeError, ValueError):
        value = value.replace("\\n", "\n").replace('\\"', '"')
    value = value.removeprefix('""').strip()
    if value.startswith('"') and not value.endswith('"'):
        value = value[1:].strip()
    if value.endswith('"') and value.count('"') % 2 == 1:
        value = value[:-1].strip()
    return value


def _parse_tool_element_calls(text: str, available_tools: list[str]) -> list[ParsedToolCall]:
    available = {tool for tool in available_tools if tool}
    if not available:
        return []
    region = _tool_calls_region(text)
    calls: list[ParsedToolCall] = []
    for match in re.finditer(r"(?is)<([A-Za-z_][\w.\-]*)\b[^>]*>(.*?)</\1\s*>", region):
        name = match.group(1)
        if name not in available:
            continue
        args = {child.group(1): _parse_xml_scalar(child.group(2)) for child in re.finditer(r"(?is)<([\w.\-]+)\b[^>]*>(.*?)</\1\s*>", match.group(2))}
        parsed = _parse_arguments(match.group(2))
        calls.append(_make_call(name, args or (parsed if isinstance(parsed, dict) else match.group(2).strip())))
    return calls


def _parse_loose_tool_call_calls(text: str, available_tools: list[str]) -> list[ParsedToolCall]:
    available = {tool for tool in available_tools if tool}
    calls: list[ParsedToolCall] = []
    for match in re.finditer(r"(?is)(?:<)?tool_call>\s*([A-Za-z_][\w.\-]*)", text):
        rest = text[match.end():]
        obj = _extract_json_value(rest, "{")
        name = match.group(1)
        if not isinstance(obj, dict):
            if name == "Bash" and name in available:
                command = _command_from_broken_json(rest)
                if command:
                    calls.append(_make_call(name, {"command": command}))
            continue
        if name in available:
            calls.append(_make_call(name, obj))
        elif name == "Glob" and "Bash" in available:
            bash_args = _glob_args_to_bash(obj)
            if bash_args:
                calls.append(_make_call("Bash", bash_args))
    return calls


def _parse_tagless_tool_call_calls(text: str, available_tools: list[str]) -> list[ParsedToolCall]:
    available = {tool for tool in available_tools if tool}
    calls: list[ParsedToolCall] = []
    for name in sorted(available, key=len, reverse=True):
        pattern = rf"(?is)<tool_call>\s*{re.escape(name)}\s*(.*?)</{re.escape(name)}\s*>"
        for match in re.finditer(pattern, text):
            args = {child.group(1): _parse_xml_scalar(child.group(2)) for child in re.finditer(r"(?is)<([\w.\-]+)\b[^>]*>(.*?)</\1\s*>", match.group(1))}
            calls.append(_make_call(name, args))
    return calls


def _parse_function_style_calls(text: str, available_tools: list[str]) -> list[ParsedToolCall]:
    available = {tool for tool in available_tools if tool}
    if not available:
        return []
    for name in sorted(available, key=len, reverse=True):
        match = re.search(rf"(?is)(^|[^\w.\-]){re.escape(name)}\s*\((.*)\)\s*$", text)
        if not match:
            continue
        raw = match.group(2).strip()
        parsed = _parse_arguments(raw)
        if isinstance(parsed, dict):
            return [_make_call(name, parsed)]
        if name == "Bash":
            command = _command_from_broken_json(raw) or raw
            return [_make_call(name, {"command": command})]
    return []


def _has_function_style_tool_call(text: str, available_tools: list[str]) -> bool:
    return any(re.search(rf"(?is)(^|[^\w.\-]){re.escape(str(name))}\s*\(", text) for name in available_tools if name)


def _command_from_broken_json(raw: str) -> str:
    match = re.search(r'(?is)["\']command["\']\s*:\s*(.+?)(?:,\s*["\']description["\']|[}\s]*$)', raw)
    if not match:
        return ""
    return _clean_bash_command(match.group(1).strip().rstrip("}").strip())


def _parse_name_near_json_calls(text: str, available_tools: list[str]) -> list[ParsedToolCall]:
    if not available_tools:
        return []
    obj = _extract_json_value(text, "{")
    if not isinstance(obj, dict):
        return []
    start = text.find("{")
    prefix = re.sub(r"(?is)<[^>]+>", " ", text[:start])
    for name in sorted({str(item) for item in available_tools if item}, key=len, reverse=True):
        if re.search(rf"(?is)(^|[^\w.\-]){re.escape(name)}([^\w.\-]|$)", prefix):
            return [_make_call(name, obj)]
    return []


def _parse_glob_as_bash(text: str) -> list[ParsedToolCall]:
    obj = _extract_json_value(text, "{")
    if not isinstance(obj, dict):
        return []
    if not re.search(r"(?is)\bGlob\b", text):
        return []
    args = _glob_args_to_bash(obj)
    return [_make_call("Bash", args)] if args else []


def _glob_args_to_bash(args: dict[str, Any]) -> dict[str, str]:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return {}
    normalized = re.sub(r"^([A-Za-z]):[\\/]+", lambda m: f"/{m.group(1).lower()}/", pattern)
    normalized = normalized.replace("\\", "/")
    parent, leaf = normalized.rsplit("/", 1) if "/" in normalized else (".", normalized)
    leaf = leaf or "*"
    return {
        "command": f"find {_quote_shell_path(parent)} -maxdepth 1 -iname {shlex.quote(leaf)} -print",
        "description": f"Find files matching {normalized}",
    }


def _quote_shell_path(path: str) -> str:
    if path == "~" or path.startswith("~/"):
        return "~" + (shlex.quote(path[1:]) if len(path) > 1 else "")
    return shlex.quote(path)


def _tool_calls_region(text: str) -> str:
    match = re.search(r"(?is)<tool_calls\b[^>]*>", text)
    if not match:
        return text
    rest = text[match.end():]
    close = re.search(r"(?is)</tool_calls\s*>", rest)
    return rest[:close.start()] if close else rest


def _strip_fences(text: str) -> str:
    fenced = re.fullmatch(r"(?is)\s*```(?:json|xml)?\s*(.*?)\s*```\s*", text)
    return fenced.group(1) if fenced else text


def _count_messages(messages: list[dict[str, Any]], model: str) -> int:
    from services.protocol.conversation import count_message_tokens
    return count_message_tokens(messages, model)


def _count_tool_calls(calls: list[ParsedToolCall], model: str) -> int:
    from services.protocol.conversation import count_text_tokens
    return count_text_tokens(json.dumps(openai_tool_calls(calls), ensure_ascii=False), model)
