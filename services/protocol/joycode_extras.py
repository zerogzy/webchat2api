from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from services.account_service import account_service
from services.providers.joycode.client import JoyCodeClient


def _client() -> JoyCodeClient:
    token = account_service.get_text_access_token(provider="joycode")
    if not token:
        raise HTTPException(status_code=429, detail={"error": "no available JoyCode account"})
    account = account_service.get_account(token, provider="joycode") or {}
    account_service.mark_text_used(token)
    return JoyCodeClient(account)


def web_search(body: dict[str, Any]) -> dict[str, Any]:
    query = str(body.get("query") or body.get("prompt") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail={"error": "query is required"})
    with _client() as client:
        return {"search_result": client.web_search(query)}


def rerank(body: dict[str, Any]) -> dict[str, Any]:
    query = str(body.get("query") or "").strip()
    documents = body.get("documents")
    if not query or not isinstance(documents, list) or not documents:
        raise HTTPException(status_code=400, detail={"error": "query and documents are required"})
    top_n = int(body.get("top_n") or len(documents))
    with _client() as client:
        return client.rerank(query, [str(item) for item in documents], top_n)
