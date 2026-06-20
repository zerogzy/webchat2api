from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from fastapi import HTTPException

from services.config import config
from services.providers.base import GEMINI_PROVIDER
from services.providers.registry import normalize_account_provider, normalize_provider


_GEMINI_BROWSER_LOGIN_JOBS: dict[str, dict[str, Any]] = {}
_GEMINI_BROWSER_LOGIN_TTL = 900


def import_requested(provider: str | None, payloads: list[dict[str, Any]]) -> bool:
    if normalize_provider(provider) == GEMINI_PROVIDER:
        return True
    for item in payloads:
        provider_value = str(item.get("provider") or "").strip()
        if not provider_value:
            continue
        try:
            if normalize_account_provider(provider_value) == GEMINI_PROVIDER:
                return True
        except ValueError:
            continue
    return False


def validate_import_payloads(tokens: list[str], payloads: list[dict[str, Any]], provider: str | None) -> None:
    if not import_requested(provider, payloads):
        return
    if tokens:
        raise HTTPException(status_code=400, detail={"error": "Gemini 账号请使用 Cookie JSON 导入，不再支持 TXT token 导入"})
    if not payloads:
        raise HTTPException(status_code=400, detail={"error": "Gemini 账号请使用 Cookie JSON 导入"})
    top_level_gemini = normalize_provider(provider) == GEMINI_PROVIDER
    for item in payloads:
        item_provider = str(item.get("provider") or "").strip()
        if top_level_gemini and item_provider and normalize_account_provider(item_provider) != GEMINI_PROVIDER:
            raise HTTPException(status_code=400, detail={"error": "Gemini 导入不能混用其他供应商账号"})
        cookies = item.get("cookies") if isinstance(item.get("cookies"), dict) else {}
        psid = str(item.get("__Secure-1PSID") or cookies.get("__Secure-1PSID") or "").strip()
        psidts = str(item.get("__Secure-1PSIDTS") or cookies.get("__Secure-1PSIDTS") or "").strip()
        if not psid or not psidts:
            raise HTTPException(status_code=400, detail={"error": "Gemini Cookie JSON 中必须包含 __Secure-1PSID 和 __Secure-1PSIDTS"})


def browser_bridge_url() -> str:
    bridge_url = config.browser_bridge_url or "http://127.0.0.1:3080"
    return bridge_url.rstrip("/")


def bridge_request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{browser_bridge_url()}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except ValueError:
            detail = {"error": raw or str(exc)}
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail={"error": "Gemini 浏览器桥接服务不可用"}) from exc
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail={"error": "Gemini 浏览器桥接服务返回了无效响应"}) from exc
    return parsed if isinstance(parsed, dict) else {}


def remember_login_job(job_id: str, owner_id: str, proxy: str) -> None:
    now = time.time()
    _GEMINI_BROWSER_LOGIN_JOBS[job_id] = {"owner_id": owner_id, "created_at": now, "proxy": proxy}
    for key, value in list(_GEMINI_BROWSER_LOGIN_JOBS.items()):
        if now - float(value.get("created_at") or 0) > _GEMINI_BROWSER_LOGIN_TTL:
            _GEMINI_BROWSER_LOGIN_JOBS.pop(key, None)


def require_login_job(job_id: str, owner_id: str) -> dict[str, Any]:
    job = _GEMINI_BROWSER_LOGIN_JOBS.get(job_id)
    if not job or job.get("owner_id") != owner_id:
        raise HTTPException(status_code=404, detail={"error": "Gemini browser login job not found"})
    return job


def forget_login_job(job_id: str) -> None:
    _GEMINI_BROWSER_LOGIN_JOBS.pop(job_id, None)


def complete_browser_login(
    status: dict[str, Any],
    job: dict[str, Any],
    *,
    account_service: Any,
    sanitize_account_result: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    if status.get("status") != "success" or status.get("account"):
        return status
    cookies = status.get("cookies") if isinstance(status.get("cookies"), dict) else {}
    if not cookies.get("__Secure-1PSID") or not cookies.get("__Secure-1PSIDTS"):
        return {**status, "status": "failed", "errorCode": "COOKIE_NOT_FOUND", "message": "浏览器登录成功但缺少 Gemini 必需 Cookie"}
    payload = {"provider": GEMINI_PROVIDER, "cookies": cookies, "proxy": str(job.get("proxy") or "")}
    result = account_service.add_account_items([payload])
    access_tokens = [str(item.get("access_token") or "").strip() for item in result.get("items", []) if isinstance(item, dict) and str(item.get("provider") or "") == GEMINI_PROVIDER]
    refresh_result = account_service.refresh_accounts(access_tokens, provider=GEMINI_PROVIDER) if access_tokens else {"refreshed": 0, "errors": [], "items": result.get("items", [])}
    sanitized = sanitize_account_result({
        **result,
        "refreshed": refresh_result.get("refreshed", 0),
        "errors": refresh_result.get("errors", []),
        "items": refresh_result.get("items", result.get("items", [])),
    })
    status.pop("cookies", None)
    status.update({
        "account": True,
        "added": sanitized.get("added", 0),
        "skipped": sanitized.get("skipped", 0),
        "refreshed": sanitized.get("refreshed", 0),
        "errors": sanitized.get("errors", []),
        "items": sanitized.get("items", []),
    })
    return status
