from __future__ import annotations

import json
import time
import uuid
from hashlib import sha256
from typing import Any, Iterator

from services.network.client import create_session
from services.providers.codebuddy.client import StreamAggregator
from services.providers.qoder.cosy import build_cosy_headers
from services.providers.qoder.encoding import qoder_encode_body
from services.providers.qoder.model_catalog import get_model_config
from services.providers.qoder.models import UPSTREAM_MODEL_BY_ID
from utils.helper import ensure_ok

CHAT_URL = "https://api3.qoder.sh/algo/api/v2/service/pro/sse/agent_chat_generation?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1"


class QoderError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
    return "" if content is None else str(content)


def _normalize_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = _clean(message.get("role")) or "user"
        text = _text(message.get("content"))
        if role == "system":
            if text:
                system_parts.append(text)
            continue
        item = dict(message)
        item["content"] = text
        out.append(item)
    return out, "\n\n".join(system_parts)


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return _text(message.get("content"))
    return ""


def _stable_hash(prefix: str, *parts: Any) -> str:
    h = sha256(prefix.encode())
    for part in parts:
        h.update(b"\0")
        h.update(str(part or "").encode())
    return h.hexdigest()[:16]


def _request_id(model_key: str, messages: list[dict[str, Any]], tools: Any, max_tokens: int) -> str:
    return _stable_hash("qoder-record", model_key, json.dumps(messages, ensure_ascii=False, sort_keys=True), json.dumps(tools or [], ensure_ascii=False, sort_keys=True), max_tokens)


def _parse_qoder_sse(line: object) -> dict[str, Any] | None:
    text = line.decode("utf-8", "ignore") if isinstance(line, bytes) else str(line or "")
    text = text.strip()
    if not text.startswith("data:"):
        return None
    data = text[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        envelope = json.loads(data)
    except Exception:
        return None
    status = int(envelope.get("statusCodeValue") or 200) if isinstance(envelope, dict) else 200
    inner = envelope.get("body") if isinstance(envelope, dict) else ""
    if status != 200:
        raise QoderError(str(inner or f"Qoder upstream status {status}"), status)
    if not inner or inner == "[DONE]":
        return None
    try:
        payload = json.loads(str(inner))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


class QoderClient:
    def __init__(self, account: dict[str, Any], *, timeout: int = 300) -> None:
        self.account = account
        self.device_token = _clean(account.get("device_token") or account.get("access_token") or account.get("token"))
        self.user_id = _clean(account.get("user_id") or account.get("account_id"))
        self.machine_id = _clean(account.get("machine_id"))
        if not self.device_token:
            raise QoderError("Qoder device token is required", 401)
        if not self.user_id:
            raise QoderError("Qoder user_id is required", 401)
        self.session = create_session(account=account, verify=True)
        self.timeout = timeout

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "QoderClient":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _body(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> tuple[str, dict[str, Any]]:
        model_key = UPSTREAM_MODEL_BY_ID.get(model, model.removeprefix("al-"))
        model_config = get_model_config(self.account, model_key)
        q_messages, system = _normalize_messages(messages)
        tools = body.get("tools") if isinstance(body.get("tools"), list) else []
        max_tokens = int(body.get("max_tokens") or model_config.get("max_output_tokens") or 32768)
        last_user = _last_user_text(q_messages)
        record_id = _request_id(model_key, q_messages, tools, max_tokens)
        session_id = _stable_hash("qoder-session", self.user_id, model_key)
        return model_key, {
            "request_id": str(uuid.uuid4()),
            "request_set_id": record_id,
            "chat_record_id": record_id,
            "session_id": session_id,
            "stream": True,
            "chat_task": "FREE_INPUT",
            "is_reply": True,
            "is_retry": False,
            "source": 1,
            "version": "3",
            "session_type": "qodercli",
            "agent_id": "agent_common",
            "task_id": "common",
            "code_language": "",
            "chat_prompt": "",
            "image_urls": None,
            "aliyun_user_type": "",
            "system": system,
            "messages": q_messages,
            "tools": tools,
            "parameters": {"max_tokens": max_tokens},
            "chat_context": {
                "chatPrompt": "",
                "imageUrls": None,
                "extra": {"context": [], "modelConfig": {"key": model_key, "is_reasoning": bool(model_config.get("is_reasoning"))}, "originalContent": last_user},
                "features": [],
                "text": last_user,
            },
            "model_config": model_config,
            "business": {
                "product": "cli",
                "version": "1.0.0",
                "type": "agent",
                "stage": "start",
                "id": str(uuid.uuid4()),
                "name": last_user[:30],
                "begin_at": int(time.time() * 1000),
            },
        }

    def stream_chunks(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
        model_key, payload = self._body(body, messages, model)
        encoded_body = qoder_encode_body(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "Accept-Encoding": "identity",
            "X-Model-Key": model_key,
            "X-Model-Source": str(payload.get("model_config", {}).get("source") or "system"),
            **build_cosy_headers(encoded_body, CHAT_URL, {
                "user_id": self.user_id,
                "token": self.device_token,
                "machine_id": self.machine_id,
                "name": self.account.get("name") or self.account.get("display_name"),
                "email": self.account.get("email"),
            }),
        }
        response = self.session.post(CHAT_URL, headers=headers, data=encoded_body, stream=True, timeout=self.timeout)
        ensure_ok(response, "Qoder chat")
        for line in response.iter_lines():
            payload = _parse_qoder_sse(line)
            if payload is not None:
                yield payload

    def chat_completion(self, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        aggregator = StreamAggregator()
        for chunk in self.stream_chunks(body, messages, model):
            aggregator.process(chunk)
        return aggregator.response()
