from __future__ import annotations

from typing import Any

from services.providers.base import CATPAW_PROVIDER, GEMINI_PROVIDER, GROK_PROVIDER
from services.providers.registry import account_strategy, normalize_account_provider


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def account_row_id_for_provider(account: dict[str, Any], provider: str | None) -> str:
    if not provider:
        return ""
    try:
        strategy = account_strategy(normalize_account_provider(provider))
    except ValueError:
        return ""
    row_id = getattr(strategy, "account_row_id", None)
    if callable(row_id):
        return _clean_string(row_id(account))
    return ""


def matched_account_tokens_by_identifiers(
    identifiers: list[dict[str, str]],
    accounts: dict[str, dict],
    provider: str | None,
) -> set[str]:
    if provider not in {GEMINI_PROVIDER, GROK_PROVIDER, CATPAW_PROVIDER} or not identifiers:
        return set()
    account_ids = {
        _clean_string(identifier.get("account_id"))
        for identifier in identifiers
        if isinstance(identifier, dict) and _clean_string(identifier.get("account_id"))
    }
    row_ids = {
        _clean_string(identifier.get("row_id"))
        for identifier in identifiers
        if isinstance(identifier, dict) and _clean_string(identifier.get("row_id"))
    }
    if not account_ids and not row_ids:
        return set()
    return {
        account_token
        for account_token, account in accounts.items()
        if (account_ids and _clean_string(account.get("account_id")) in account_ids)
        or (row_ids and account_row_id_for_provider(account, provider) in row_ids)
    }
