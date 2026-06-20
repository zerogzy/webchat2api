from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from fastapi import HTTPException

from services.providers.base import CATPAW_PROVIDER


_CATPAW_QR_LOGIN_JOBS: dict[str, dict[str, Any]] = {}
_CATPAW_QR_LOGIN_TTL = 600


def remember_qr_job(job_id: str, owner_id: str, code: str, expire_time: Any, proxy: str) -> None:
    now = time.time()
    _CATPAW_QR_LOGIN_JOBS[job_id] = {
        "owner_id": owner_id,
        "created_at": now,
        "code": code,
        "expire_time": expire_time,
        "proxy": proxy,
        "done": False,
        "result": None,
    }
    for key, value in list(_CATPAW_QR_LOGIN_JOBS.items()):
        if now - float(value.get("created_at") or 0) > _CATPAW_QR_LOGIN_TTL:
            _CATPAW_QR_LOGIN_JOBS.pop(key, None)


def require_qr_job(job_id: str, owner_id: str) -> dict[str, Any]:
    job = _CATPAW_QR_LOGIN_JOBS.get(job_id)
    if not job or job.get("owner_id") != owner_id:
        raise HTTPException(status_code=404, detail={"error": "CatPaw 扫码登录会话不存在或已过期"})
    return job


def forget_qr_job(job_id: str) -> None:
    _CATPAW_QR_LOGIN_JOBS.pop(job_id, None)


def build_account_payload(token_data: dict[str, Any], proxy: str) -> dict[str, Any]:
    from services.providers.catpaw import client as catpaw_client

    access_token = str(token_data.get("accessToken") or "").strip()
    try:
        info = catpaw_client.get_user_info(access_token)
    except Exception:
        info = {}
    login_name = str(info.get("loginName") or info.get("login") or info.get("name") or "").strip()
    mis_id = str(info.get("misId") or info.get("mis") or info.get("empId") or info.get("userId") or "").strip()
    email = str(info.get("email") or "").strip()
    catpaw_id = mis_id or login_name or uuid.uuid4().hex
    return {
        "provider": CATPAW_PROVIDER,
        "catpaw_id": catpaw_id,
        "catpaw_access_token": access_token,
        "refresh_token": str(token_data.get("refreshToken") or "").strip(),
        "expires": token_data.get("expires"),
        "refresh_expires": token_data.get("refreshExpires"),
        "login_name": login_name or None,
        "mis_id": mis_id or None,
        "email": email or None,
        "proxy": str(proxy or ""),
    }


def complete_qr_login(
    token_data: dict[str, Any],
    job: dict[str, Any],
    *,
    account_service: Any,
    sanitize_account_result: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    payload = build_account_payload(token_data, str(job.get("proxy") or ""))
    result = account_service.add_account_items([payload])
    sanitized = sanitize_account_result(result)
    return {
        "status": "success",
        "account": True,
        "added": sanitized.get("added", 0),
        "skipped": sanitized.get("skipped", 0),
        "items": sanitized.get("items", []),
    }
