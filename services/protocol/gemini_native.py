from __future__ import annotations

import base64
import binascii
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from fastapi import HTTPException

from services.providers.base import ModelSpec
from services.providers.gemini.models import gemini_model_specs
from services.providers.registry import resolve_model
from services.providers import gemini
from services.protocol import tool_calls


@dataclass(frozen=True)
class NativeTool:
    name: str
    description: str = ""
    parameters: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolConfig:
    mode: str = "AUTO"
    allowed_names: tuple[str, ...] = ()


CompletionFunc = Callable[[dict[str, Any], ModelSpec, list[dict[str, Any]]], gemini.GeminiCompletion]
_INLINE_MEDIA_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
_MAX_INLINE_MEDIA_BYTES = 10 * 1024 * 1024


def list_models() -> dict[str, Any]:
    return {
        "models": [
            {
                "name": f"models/{spec.id}",
                "displayName": spec.id,
                "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
            }
            for spec in gemini_model_specs()
        ]
    }


def generation_body(body: dict[str, Any]) -> dict[str, Any]:
    config = _dict_value(body, "generationConfig", "generation_config")
    payload: dict[str, Any] = {}
    if not config:
        return payload
    mapping = {
        "temperature": "temperature",
        "topP": "top_p",
        "top_p": "top_p",
        "topK": "top_k",
        "top_k": "top_k",
        "maxOutputTokens": "max_tokens",
        "max_output_tokens": "max_tokens",
    }
    for source, target in mapping.items():
        if source in config and config[source] is not None:
            payload[target] = config[source]
    return payload


def generate_content(model: str, body: dict[str, Any], completion_func: CompletionFunc | None = None) -> dict[str, Any]:
    model_id = _model_id(model)
    spec = resolve_model(model_id)
    messages, text = messages_from_contents(body)
    tools = native_tools(body.get("tools"))
    tool_config = native_tool_config(body.get("toolConfig", body.get("tool_config")))
    provider_messages = messages
    if tools and tool_config.mode != "NONE":
        provider_messages = inject_native_tool_prompt(messages, tools, tool_config)
    provider_body = generation_body(body)
    provider_body["model"] = model_id
    completion = (completion_func or gemini.chat_completion)(provider_body, spec, provider_messages)
    parsed = parse_native_tool_response(completion.content, tools, tool_config) if tools else []
    if parsed:
        return gemini_response(model_id, function_call_parts(parsed), "STOP", text)
    content = native_text_response(completion.content) if tools else completion.content
    return gemini_response(model_id, [{"text": tool_calls.strip_tool_markup(content)}], "STOP", text)


def stream_generate_content(model: str, body: dict[str, Any], completion_func: CompletionFunc | None = None, first_event: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    if first_event is not None:
        response = first_event
    else:
        response = generate_content(model, body, completion_func=completion_func)
    candidates = response.get("candidates") if isinstance(response, dict) else []
    candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
    content = candidate.get("content") if isinstance(candidate, dict) else {}
    parts = content.get("parts") if isinstance(content, dict) else []
    if not isinstance(parts, list):
        parts = []
    if parts and isinstance(parts[0], dict) and parts[0].get("function_call"):
        yield response
        return
    text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
    for chunk in gemini.synthetic_stream_content(text):
        if chunk:
            yield gemini_response(_model_id(model), [{"text": chunk}], None, text)
    yield gemini_response(_model_id(model), [], "STOP", text)


def _inline_media_part(part: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    inline = _dict_value(part, "inline_data", "inlineData")
    if not isinstance(inline, dict):
        return None
    data = inline.get("data")
    if not isinstance(data, str) or not data:
        return None
    mime_type = str(inline.get("mime_type") or inline.get("mimeType") or "application/octet-stream").strip().lower() or "application/octet-stream"
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"
    if mime_type not in _INLINE_MEDIA_MIME_TYPES:
        raise HTTPException(status_code=400, detail={"error": "unsupported inline media mime type"})
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid inline media data"}) from exc
    if not decoded:
        raise HTTPException(status_code=400, detail={"error": "inline media is empty"})
    if len(decoded) > _MAX_INLINE_MEDIA_BYTES:
        raise HTTPException(status_code=400, detail={"error": "inline media is too large"})
    content_part = {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{data}"}}
    return content_part, f"[image:{mime_type}]"


def messages_from_contents(body: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    contents = body.get("contents")
    if not isinstance(contents, list) or not contents:
        raise HTTPException(status_code=400, detail={"error": "contents is required"})
    messages: list[dict[str, Any]] = []
    text_parts: list[str] = []
    non_text_parts: list[str] = []
    media_previews: list[str] = []
    for content in contents:
        if not isinstance(content, dict):
            continue
        role = _native_role_to_openai(str(content.get("role") or "user"))
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        message_parts: list[dict[str, Any]] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                text = str(part.get("text") or "")
                if text:
                    message_parts.append({"type": "text", "text": text})
                    text_parts.append(text)
                continue
            inline_media = _inline_media_part(part)
            if inline_media is not None:
                media_part, preview = inline_media
                message_parts.append(media_part)
                media_previews.append(preview)
                continue
            call = _dict_value(part, "function_call", "functionCall")
            if call:
                serialized = "Function call: " + json.dumps(call, ensure_ascii=False, separators=(",", ":"))
                message_parts.append({"type": "text", "text": serialized})
                non_text_parts.append(serialized)
                continue
            response = _dict_value(part, "function_response", "functionResponse")
            if response:
                serialized = "Function response: " + json.dumps(response, ensure_ascii=False, separators=(",", ":"))
                message_parts.append({"type": "text", "text": serialized})
                non_text_parts.append(serialized)
        if not message_parts:
            continue
        if all(part.get("type") == "text" for part in message_parts):
            messages.append({"role": role, "content": "\n".join(str(part.get("text") or "") for part in message_parts)})
        else:
            messages.append({"role": role, "content": message_parts})
    request_text = "\n".join(text_parts).strip()
    if not request_text and non_text_parts:
        request_text = "\n".join(non_text_parts).strip()
    if not request_text and media_previews:
        request_text = "\n".join(media_previews).strip()
    if not request_text:
        raise HTTPException(status_code=400, detail={"error": "Gemini generateContent requires at least one text part"})
    if not messages:
        raise HTTPException(status_code=400, detail={"error": "Gemini generateContent requires at least one text part"})
    return messages, request_text


def request_text_from_body(body: dict[str, Any]) -> str:
    try:
        _, text = messages_from_contents(body)
        return text
    except HTTPException:
        return ""


def native_tools(value: object) -> list[NativeTool]:
    if not isinstance(value, list):
        return []
    tools: list[NativeTool] = []
    for tool in value:
        if not isinstance(tool, dict):
            continue
        declarations = tool.get("functionDeclarations", tool.get("function_declarations"))
        if not isinstance(declarations, list):
            continue
        for declaration in declarations:
            if not isinstance(declaration, dict):
                continue
            name = str(declaration.get("name") or "").strip()
            if not name:
                continue
            parameters = declaration.get("parameters")
            tools.append(NativeTool(name, str(declaration.get("description") or ""), parameters if isinstance(parameters, dict) else {}))
    return tools


def native_tool_config(value: object) -> ToolConfig:
    if not isinstance(value, dict):
        return ToolConfig()
    config = value.get("functionCallingConfig", value.get("function_calling_config"))
    if not isinstance(config, dict):
        return ToolConfig()
    mode = str(config.get("mode") or "AUTO").strip().upper() or "AUTO"
    if "allowedFunctionNames" in config:
        allowed = config.get("allowedFunctionNames")
    else:
        allowed = config.get("allowed_function_names")
    names = tuple(str(name).strip() for name in allowed if str(name).strip()) if isinstance(allowed, list) else ()
    return ToolConfig(mode if mode in {"AUTO", "ANY", "NONE"} else "AUTO", names)


def inject_native_tool_prompt(messages: list[dict[str, Any]], tools: list[NativeTool], config: ToolConfig) -> list[dict[str, Any]]:
    declarations = [
        {"name": item.name, "description": item.description, "parameters": item.parameters or {}}
        for item in tools
        if not config.allowed_names or item.name in config.allowed_names
    ]
    if not declarations:
        return messages
    prompt = (
        "You have access to Gemini function declarations. Return JSON only. "
        "Use {\"status\":\"call\",\"tool_calls\":[{\"name\":\"function_name\",\"arguments\":{}}]} to call tools, "
        "or {\"status\":\"text\",\"content\":\"final answer\"} to answer. "
        "Do not include markdown fences. Declarations: "
        + json.dumps(declarations, ensure_ascii=False, separators=(",", ":"))
    )
    if config.mode == "ANY":
        prompt += " You must call one of the declared functions."
    if config.allowed_names:
        prompt += " Allowed functions: " + ", ".join(config.allowed_names) + "."
    return [{"role": "system", "content": prompt}, *messages]


def _extract_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        return value if isinstance(value, dict) else {}
    return {}


def native_text_response(text: str) -> str:
    stripped = _strip_fences(text or "").strip()
    obj = _extract_json_object(stripped)
    if not obj:
        return text
    status = str(obj.get("status") or "").strip().lower()
    if status != "text":
        return text
    content = obj.get("content")
    return str(content).strip() if content is not None else ""


def parse_native_tool_response(text: str, tools: list[NativeTool], config: ToolConfig | None = None) -> list[tool_calls.ParsedToolCall]:
    config = config or ToolConfig()
    available = [item.name for item in tools]
    if config.allowed_names:
        available = [name for name in available if name in config.allowed_names]
    if config.mode == "NONE" or not available:
        return []
    parsed = tool_calls.parse_gemini_json_tool_calls(text, available)
    if parsed.calls:
        return parsed.calls
    generic = tool_calls.parse_tool_calls(text, available)
    if generic.calls:
        return generic.calls
    if config.mode == "ANY" and not parsed.saw_tool_syntax:
        return []
    return []


def gemini_response(model: str, parts: list[dict[str, Any]], finish_reason: str | None, request_text: str = "") -> dict[str, Any]:
    candidate: dict[str, Any] = {"content": {"role": "model", "parts": parts}}
    if finish_reason:
        candidate["finishReason"] = finish_reason
    return {
        "candidates": [candidate],
        "usageMetadata": {
            "promptTokenCount": 0,
            "candidatesTokenCount": 0,
            "totalTokenCount": 0,
        },
        "modelVersion": model,
    }


def function_call_parts(calls: list[tool_calls.ParsedToolCall]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for call in calls:
        try:
            args = json.loads(call.arguments)
        except json.JSONDecodeError:
            args = {}
        parts.append({"functionCall": {"name": call.name, "args": args if isinstance(args, dict) else {}}})
    return parts


def complete_text(model: str, prompt: str, completion_func: CompletionFunc | None = None) -> str:
    spec = resolve_model(_model_id(model))
    response = (completion_func or gemini.chat_completion)({"model": spec.id}, spec, [{"role": "user", "content": prompt}])
    return response.content


def sse_events(items: Iterator[tuple[str, dict[str, Any]]]) -> Iterator[str]:
    for event, data in items:
        yield f"event: {event}\n"
        yield "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


def _model_id(model: str) -> str:
    value = str(model or "gemini-2.5-pro").strip()
    return value.removeprefix("models/") or "gemini-2.5-pro"


def _native_role_to_openai(role: str) -> str:
    lowered = role.strip().lower()
    if lowered in {"model", "assistant"}:
        return "assistant"
    if lowered == "system":
        return "system"
    return "user"


def _strip_fences(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _dict_value(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return {}
