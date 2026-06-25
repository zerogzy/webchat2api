from __future__ import annotations

from typing import Any, Iterator


def _account_for_chat(excluded_tokens: set[str] | None = None) -> tuple[str, dict[str, Any]]:
    from services.account_service import account_service

    token = account_service.get_text_access_token(excluded_tokens=excluded_tokens, provider="codebuddy")
    if not token:
        raise RuntimeError("no available CodeBuddy account")
    account = account_service.get_account(token, provider="codebuddy") or {}
    return token, account


def _run_with_account(body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    from services.account_service import account_service
    from services.providers.codebuddy.client import CodeBuddyClient, is_quota_exhausted_error

    excluded: set[str] = set()
    last_error: Exception | None = None
    while True:
        token, account = _account_for_chat(excluded)
        try:
            with CodeBuddyClient(account) as client:
                chunks = client.buffered_chunks(body, messages, model)
            account_service.mark_codebuddy_success(token)
            return chunks
        except Exception as exc:
            last_error = exc
            excluded.add(token)
            if is_quota_exhausted_error(exc):
                account_service.mark_codebuddy_quota_exhausted(token, exc)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("no available CodeBuddy account")


def raw_chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    from services.providers.codebuddy.client import StreamAggregator

    aggregator = StreamAggregator()
    for chunk in _run_with_account(body, messages, model):
        aggregator.process(chunk)
    return aggregator.response()


def chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> str:
    response = raw_chat_completion(body, messages, model)
    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    return str(message.get("content") or "")


def chat_completion_deltas(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> Iterator[str]:
    for chunk in _run_with_account(body, messages, model):
        choices = chunk.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        if isinstance(delta.get("content"), str):
            yield delta["content"]
