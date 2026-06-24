from __future__ import annotations

import json
import os
from typing import Any, Iterator

from services.account_service import account_service
from services.protocol.conversation import count_text_tokens
from services.providers.joycode.client import JoyCodeClient


def enabled() -> bool:
    return str(os.environ.get("JOYCODE_NATIVE_ANTHROPIC", "1")).strip().lower() not in {"0", "false", "no", "off"}


def is_native_model(model: str) -> bool:
    text = str(model or "").strip()
    if text == "Claude-Opus-4.7":
        return True
    return text.startswith("claude-") and str(os.environ.get("JOYCODE_CLAUDE_ROUTE", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _anthropic_stop_reason(finish_reason: str) -> str:
    if finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return finish_reason or "end_turn"


def _account() -> dict[str, Any]:
    token = account_service.get_text_access_token(provider="joycode")
    if not token:
        raise RuntimeError("no available JoyCode account")
    account = account_service.get_account(token, provider="joycode") or {}
    account_service.mark_text_used(token)
    return account


def _body(body: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "model": "Claude-Opus-4.7",
        "messages": body.get("messages") if isinstance(body.get("messages"), list) else [],
        "stream": True,
        "max_tokens": int(body.get("max_tokens") or 8192),
        "thinking": {"type": "disabled"},
    }
    for key in ("system", "stop_sequences", "tools", "tool_choice", "temperature", "top_p"):
        if body.get(key) is not None:
            payload[key] = body[key]
    return payload


def _payloads(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    pending_event = ""
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if text.startswith("event:"):
            pending_event = text.removeprefix("event:").strip()
            continue
        if text.startswith("data:"):
            text = text.removeprefix("data:").strip()
        if not text or text == "[DONE]" or not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        if pending_event and not payload.get("type"):
            payload["type"] = pending_event
        pending_event = ""
        if isinstance(payload, dict):
            yield payload


def stream_events(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    with JoyCodeClient(_account()) as client:
        yield from _payloads(client.post_stream("/api/saas/anthropic/v1/messages", _body(body), anthropic=True))


def non_stream_response(body: dict[str, Any]) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    stop_reason = "end_turn"
    input_tokens = 0
    output_tokens = 0
    text_parts: list[str] = []
    for event in stream_events({**body, "stream": True}):
        event_type = str(event.get("type") or "")
        if event_type == "message_start":
            usage = ((event.get("message") or {}).get("usage") or {}) if isinstance(event.get("message"), dict) else {}
            input_tokens = int(usage.get("input_tokens") or input_tokens)
        elif event_type == "content_block_start":
            block = event.get("content_block")
            current = dict(block) if isinstance(block, dict) else {"type": "text", "text": ""}
        elif event_type == "content_block_delta":
            if current is None:
                current = {"type": "text", "text": ""}
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            if delta.get("type") == "text_delta":
                current["type"] = "text"
                current["text"] = str(current.get("text") or "") + str(delta.get("text") or "")
            elif delta.get("type") == "input_json_delta":
                try:
                    current["input"] = json.loads(str(delta.get("partial_json") or "{}"))
                except Exception:
                    current["input"] = {}
        elif event_type == "content_block_stop":
            if current is not None:
                content.append(current)
                if current.get("type") == "text":
                    text_parts.append(str(current.get("text") or ""))
                current = None
        elif event_type == "message_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            stop_reason = str(delta.get("stop_reason") or stop_reason)
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            output_tokens = int(usage.get("output_tokens") or output_tokens)
    if current is not None:
        content.append(current)
    if not content:
        content = [{"type": "text", "text": ""}]
    if not output_tokens:
        output_tokens = count_text_tokens("\n".join(text_parts), str(body.get("model") or "Claude-Opus-4.7"))
    return {
        "id": f"msg_joycode",
        "type": "message",
        "role": "assistant",
        "model": str(body.get("model") or "Claude-Opus-4.7"),
        "content": content,
        "stop_reason": _anthropic_stop_reason(stop_reason),
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
