from __future__ import annotations

from typing import Any, Iterator


def _account_for_chat() -> dict[str, Any]:
    from services.account_service import account_service

    token = account_service.get_text_access_token(provider="joycode")
    if not token:
        raise RuntimeError("no available JoyCode account")
    account = account_service.get_account(token, provider="joycode") or {}
    account_service.mark_text_used(token)
    return account


def chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> str:
    from services.providers.joycode.client import JoyCodeClient

    with JoyCodeClient(_account_for_chat()) as client:
        response = client.chat_completion(body, messages, model)
    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    return str(message.get("content") or "")


def raw_chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    from services.providers.joycode.client import JoyCodeClient

    with JoyCodeClient(_account_for_chat()) as client:
        return client.chat_completion(body, messages, model)


def chat_completion_deltas(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> Iterator[str]:
    from services.providers.joycode.client import JoyCodeClient

    with JoyCodeClient(_account_for_chat()) as client:
        yield from client.chat_completion_deltas(body, messages, model)
