"""CatPaw ChatAdapter: thin wrappers the protocol layer calls.

Returns plain text (str) / text deltas (Iterator[str]); tool-call parsing and the
OpenAI/Anthropic envelopes are handled by the protocol layer. Token selection comes
from the multi-account store (account_service); if no CatPaw account is imported it
falls back to the standalone env / data-file token manager in `client`.
"""
from __future__ import annotations

from typing import Any, Iterator

from services.providers.catpaw import client as catpaw_client
from services.providers.catpaw.models import type_code_for


def _chat_credentials() -> tuple[str | None, str | None, Any]:
    """Return (token, mis_id, on_auth_fail) from the account store, or (None, None, None)."""
    try:
        from services.account_service import account_service

        account = account_service.get_catpaw_account_for_chat()
    except Exception:
        account = None
    if not account:
        return None, None, None
    token = account.get("catpaw_access_token") or None
    mis_id = account.get("mis_id") or account.get("login_name") or None
    identity = account.get("access_token") or ""

    def on_auth_fail() -> str | None:
        from services.account_service import account_service

        refreshed = account_service.fetch_catpaw_remote_info(identity)
        return (refreshed or {}).get("catpaw_access_token")

    return token, mis_id, (on_auth_fail if identity else None)


def chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> str:
    token, mis_id, on_auth_fail = _chat_credentials()
    return catpaw_client.chat_text(
        messages,
        type_code_for(model),
        token=token,
        mis_id=mis_id,
        on_auth_fail=on_auth_fail,
        conversation_id=body.get("catpaw_conversation_id"),
    )


def chat_completion_deltas(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> Iterator[str]:
    token, mis_id, on_auth_fail = _chat_credentials()
    yield from catpaw_client.stream_chat_deltas(
        messages,
        type_code_for(model),
        token=token,
        mis_id=mis_id,
        on_auth_fail=on_auth_fail,
        conversation_id=body.get("catpaw_conversation_id"),
    )
