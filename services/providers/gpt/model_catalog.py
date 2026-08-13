from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from typing import Any

from services.providers.base import GPT_PROVIDER
from services.providers.gpt.models import gpt_upstream_model_id


class GPTModelCatalog:
    def __init__(self, ttl_seconds: float = 300) -> None:
        self._ttl_seconds = ttl_seconds
        self._expires_at = 0.0
        self._signature: tuple[tuple[str, str], ...] = ()
        self._models_by_type: dict[str, set[str]] = {}
        self._anonymous_models: set[str] = set()
        self._lock = RLock()

    @staticmethod
    def _model_ids(result: object) -> set[str]:
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            raise TypeError("upstream model response has no data list")
        return {
            str(item.get("id") or "").strip()
            for item in data
            if isinstance(item, dict) and item.get("id")
        }

    def _accounts(self) -> list[dict[str, Any]]:
        from services.account_service import account_service

        return [
            item for item in account_service.list_accounts(GPT_PROVIDER)
            if item.get("status") not in {"禁用", "异常"} and item.get("access_token")
        ]

    @staticmethod
    def _fetch(access_token: str = "") -> set[str]:
        from services.openai_backend_api import OpenAIBackendAPI

        with OpenAIBackendAPI(access_token) as backend:
            return GPTModelCatalog._model_ids(backend.list_models())

    def _refresh(self, accounts: list[dict[str, Any]], signature: tuple[tuple[str, str], ...]) -> None:
        accounts_by_type: dict[str, list[str]] = {}
        for account in accounts:
            account_type = str(account.get("type") or "free")
            accounts_by_type.setdefault(account_type, []).append(str(account["access_token"]))

        def fetch_type(tokens: list[str]) -> set[str]:
            last_error: Exception | None = None
            for token in tokens:
                try:
                    return self._fetch(token)
                except Exception as exc:
                    last_error = exc
            raise last_error or RuntimeError("no ChatGPT account")

        models_by_type: dict[str, set[str]] = {}
        with ThreadPoolExecutor(max_workers=min(4, len(accounts_by_type) + 1)) as executor:
            anonymous = executor.submit(self._fetch)
            futures = {
                account_type: executor.submit(fetch_type, tokens)
                for account_type, tokens in accounts_by_type.items()
            }
            for account_type, future in futures.items():
                try:
                    models_by_type[account_type] = future.result()
                except Exception:
                    if account_type in self._models_by_type:
                        models_by_type[account_type] = self._models_by_type[account_type]
            try:
                anonymous_models = anonymous.result()
            except Exception:
                anonymous_models = self._anonymous_models
        self._models_by_type = models_by_type
        self._anonymous_models = anonymous_models
        self._signature = signature
        self._expires_at = time.monotonic() + self._ttl_seconds

    def _ensure(self) -> None:
        accounts = self._accounts()
        signature = tuple(sorted(
            (str(item.get("type") or "free"), str(item["access_token"]))
            for item in accounts
        ))
        with self._lock:
            if signature != self._signature or time.monotonic() >= self._expires_at:
                self._refresh(accounts, signature)

    def account_types_for_model(self, model: str) -> tuple[set[str], bool]:
        upstream_model = gpt_upstream_model_id(model)
        self._ensure()
        return (
            {account_type for account_type, models in self._models_by_type.items() if upstream_model in models},
            upstream_model in self._anonymous_models,
        )

    def list_models(self) -> dict[str, Any]:
        self._ensure()
        model_ids = set(self._anonymous_models)
        for models in self._models_by_type.values():
            model_ids.update(models)
        return {
            "object": "list",
            "data": [
                {"id": model, "object": "model", "created": 0, "owned_by": "chatgpt", "permission": [], "root": model, "parent": None}
                for model in sorted(model_ids)
            ],
        }


gpt_model_catalog = GPTModelCatalog()
