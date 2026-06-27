from __future__ import annotations

from typing import Any, Iterator


def _account_for_chat() -> dict[str, Any]:
    from services.account_service import account_service

    token = account_service.get_text_access_token(provider="qoder")
    if not token:
        raise RuntimeError("no available Qoder account")
    return account_service.get_account(token, provider="qoder") or {}


def raw_chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    from services.providers.qoder.client import QoderClient

    with QoderClient(_account_for_chat()) as client:
        return client.chat_completion(body, messages, model)


def chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> str:
    response = raw_chat_completion(body, messages, model)
    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    return str(message.get("content") or "")


def chat_completion_deltas(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> Iterator[str]:
    yield chat_completion(body, messages, model, backend=backend)
