from __future__ import annotations

import base64
import hashlib
import time
import uuid
from typing import Any, Callable
from urllib.parse import urlencode

from fastapi import HTTPException

from services.network.client import create_session
from services.providers.base import QODER_PROVIDER

LOGIN_URL = "https://qoder.com/device/selectAccounts"
DEVICE_TOKEN_URL = "https://openapi.qoder.sh/api/v1/deviceToken/poll"
USERINFO_URL = "https://openapi.qoder.sh/api/v1/userinfo"
_JOBS: dict[str, dict[str, Any]] = {}
_TTL = 300


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _prune_jobs() -> None:
    now = time.time()
    for job_id, job in list(_JOBS.items()):
        if now - float(job.get("created_at") or 0) > _TTL:
            _JOBS.pop(job_id, None)


def start_device_login(owner_id: str) -> dict[str, Any]:
    verifier = _b64url(uuid.uuid4().bytes + uuid.uuid4().bytes)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    nonce = str(uuid.uuid4())
    machine_id = str(uuid.uuid4())
    job_id = uuid.uuid4().hex
    query = urlencode({
        "challenge": challenge,
        "challenge_method": "S256",
        "machine_id": machine_id,
        "nonce": nonce,
    })
    _JOBS[job_id] = {
        "owner_id": owner_id,
        "created_at": time.time(),
        "nonce": nonce,
        "verifier": verifier,
        "machine_id": machine_id,
    }
    _prune_jobs()
    return {
        "jobId": job_id,
        "status": "waiting_for_authorization",
        "verificationUri": LOGIN_URL,
        "verificationUriComplete": f"{LOGIN_URL}?{query}",
        "expiresIn": _TTL,
        "interval": 2,
    }


def require_job(job_id: str, owner_id: str) -> dict[str, Any]:
    _prune_jobs()
    job = _JOBS.get(job_id)
    if not job or job.get("owner_id") != owner_id:
        raise HTTPException(status_code=404, detail={"error": "Qoder 登录会话不存在或已过期"})
    return job


def forget_job(job_id: str) -> None:
    _JOBS.pop(job_id, None)


def _fetch_user_info(device_token: str) -> dict[str, str]:
    try:
        with create_session(timeout=15) as session:
            response = session.get(
                USERINFO_URL,
                headers={
                    "Authorization": f"Bearer {device_token}",
                    "Accept": "application/json",
                    "User-Agent": "Go-http-client/2.0",
                },
            )
            if int(getattr(response, "status_code", 0) or 0) >= 400:
                return {}
            data = response.json()
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "name": _clean(data.get("name") or data.get("username")),
        "email": _clean(data.get("email")),
        "organization_id": _clean(data.get("organization_id")),
    }


def poll_device_login(
    job_id: str,
    owner_id: str,
    *,
    account_service: Any,
    sanitize_account_result: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    job = require_job(job_id, owner_id)
    url = (
        f"{DEVICE_TOKEN_URL}?nonce={job['nonce']}"
        f"&verifier={job['verifier']}&challenge_method=S256"
    )
    with create_session(timeout=15) as session:
        response = session.get(url, headers={"Accept": "application/json", "User-Agent": "Go-http-client/2.0"})
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code in {202, 404}:
        return {"jobId": job_id, "status": "waiting_for_authorization"}
    text = getattr(response, "text", "") or ""
    if status_code >= 400:
        return {"jobId": job_id, "status": "failed", "message": f"Qoder device token poll failed: HTTP {status_code}"}
    try:
        payload = response.json()
    except Exception:
        return {"jobId": job_id, "status": "failed", "message": "Qoder device token poll returned invalid JSON"}
    if not isinstance(payload, dict) or not _clean(payload.get("token")):
        return {"jobId": job_id, "status": "waiting_for_authorization" if not text else "failed", "message": "Qoder device token poll returned no token"}
    device_token = _clean(payload.get("token"))
    user_id = _clean(payload.get("user_id"))
    if not user_id:
        return {"jobId": job_id, "status": "failed", "message": "Qoder device token poll returned no user_id"}
    info = _fetch_user_info(device_token)
    account = {
        "provider": QODER_PROVIDER,
        "device_token": device_token,
        "user_id": user_id,
        "account_id": user_id,
        "machine_id": _clean(job.get("machine_id")),
        "email": info.get("email") or None,
        "name": info.get("name") or None,
        "organization_id": info.get("organization_id") or None,
        "status": "正常",
    }
    result = sanitize_account_result(account_service.add_account_items([account]))
    forget_job(job_id)
    return {
        "jobId": job_id,
        "status": "success",
        "added": result.get("added", 0),
        "skipped": result.get("skipped", 0),
        "items": result.get("items", []),
    }
