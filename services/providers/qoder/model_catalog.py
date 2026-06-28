from __future__ import annotations

import time
from typing import Any

from services.network.client import create_session
from services.providers.qoder.cosy import build_cosy_headers

MODEL_LIST_URL = "https://api3.qoder.sh/algo/api/v2/model/list"
CACHE_TTL_SECONDS = 3600
_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


def _key(account: dict[str, Any]) -> str:
    return str(account.get("user_id") or account.get("access_token") or "")


def get_model_config(account: dict[str, Any], model_key: str) -> dict[str, Any]:
    cache_key = _key(account)
    cached = _CACHE.get(cache_key)
    if cached and cached[0] > time.time() and model_key in cached[1]:
        return dict(cached[1][model_key], key=model_key)

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        **build_cosy_headers(b"", MODEL_LIST_URL, {
            "user_id": account.get("user_id"),
            "token": account.get("device_token") or account.get("access_token"),
            "machine_id": account.get("machine_id"),
            "name": account.get("name") or account.get("display_name"),
            "email": account.get("email"),
        }),
    }
    with create_session(account=account, verify=True) as session:
        response = session.get(MODEL_LIST_URL, headers=headers, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(f"Qoder model list failed: HTTP {response.status_code}")
        payload = response.json()
    configs = {
        str(item.get("key")): item
        for item in (payload.get("chat") if isinstance(payload, dict) else []) or []
        if isinstance(item, dict) and item.get("key")
    }
    _CACHE[cache_key] = (time.time() + CACHE_TTL_SECONDS, configs)
    if model_key not in configs:
        raise RuntimeError(f"Qoder model_config for {model_key} not found")
    return dict(configs[model_key], key=model_key)
