from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from services.providers.base import GROK_PROVIDER
from services.providers.registry import normalize_account_provider, normalize_provider


def normalize_sso_import_token(value: str) -> str:
    token = str(value or "").strip()
    if not token or ";" in token:
        return ""
    name, separator, cookie_value = token.partition("=")
    if separator:
        return cookie_value.strip() if name.strip().lower() == "sso" and cookie_value.strip() else ""
    return token


def import_requested(provider: str | None, payloads: list[dict[str, Any]]) -> bool:
    if normalize_provider(provider) == GROK_PROVIDER:
        return True
    for item in payloads:
        provider_value = str(item.get("provider") or "").strip()
        if not provider_value:
            continue
        try:
            if normalize_account_provider(provider_value) == GROK_PROVIDER:
                return True
        except ValueError:
            continue
    return False


def validate_import_payloads(tokens: list[str], payloads: list[dict[str, Any]], provider: str | None) -> list[str]:
    if not import_requested(provider, payloads):
        return tokens
    if not tokens and not payloads:
        raise HTTPException(status_code=400, detail={"error": "Grok 导入只接受裸 SSO 值，或每行一个 sso=<值>"})
    allowed_keys = {"provider", "sso", "proxy"}
    for item in payloads:
        item_provider = str(item.get("provider") or provider or "").strip()
        if item_provider and normalize_account_provider(item_provider) != GROK_PROVIDER:
            raise HTTPException(status_code=400, detail={"error": "Grok 导入不能混用其他供应商账号"})
        extra_keys = {key for key, value in item.items() if value is not None} - allowed_keys
        sso = str(item.get("sso") or "").strip()
        if extra_keys or not normalize_sso_import_token(sso):
            raise HTTPException(status_code=400, detail={"error": "Grok 导入只接受裸 SSO 值，或每行一个 sso=<值>；不支持 sso-rw、完整 Cookie header、其他 Cookie 名称、JSON、CPA 或 cookies"})
    normalized_tokens = [normalize_sso_import_token(token) for token in tokens]
    if not all(normalized_tokens):
        raise HTTPException(status_code=400, detail={"error": "Grok 导入只接受裸 SSO 值，或每行一个 sso=<值>；不支持 sso-rw、完整 Cookie header、其他 Cookie 名称、JSON、CPA 或 cookies"})
    return normalized_tokens
