from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any, Callable
from urllib.parse import urlencode

from fastapi import HTTPException
from curl_cffi import requests

from services.providers.base import JOYCODE_PROVIDER
from services.providers.joycode.client import JoyCodeClient, JoyCodeError, load_from_state_db, parse_oauth_pt_key

QR_SHOW_URL = "https://qr.m.jd.com/show?appid=133&size=147&t={ts}"
QR_CHECK_URL = "https://qr.m.jd.com/check?appid=133&token={token}&callback=jsonpCallback&_={ts}"
QR_VALID_URL = "https://passport.jd.com/uc/qrCodeTicketValidation?t={ticket}"
JD_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
QR_TTL = 180
_QR_JOBS: dict[str, dict[str, Any]] = {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _user_payload(pt_key: str, user_id: str = "", **extra: Any) -> dict[str, Any]:
    account = {"provider": JOYCODE_PROVIDER, "pt_key": pt_key, "user_id": user_id, **extra}
    with JoyCodeClient(account, timeout=60) as client:
        payload = client.validate()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    resolved_user_id = _clean(data.get("userId")) or user_id
    if not resolved_user_id:
        raise HTTPException(status_code=400, detail={"error": "JoyCode userInfo did not return userId"})
    return {
        **account,
        "user_id": resolved_user_id,
        "account_id": resolved_user_id,
        "name": _clean(data.get("realName")),
        "email": _clean(data.get("email")) or None,
        "status": "正常",
    }


def oauth_login_url(port: str = "83") -> dict[str, str]:
    token = uuid.uuid4().hex
    query = urlencode({
        "ideAppName": "JoyCode",
        "fromIde": "ide",
        "redirect": "0",
        "authPort": port,
        "authKey": token,
    })
    return {"url": f"https://joycode.jd.com/login/?{query}", "token": token}


def import_oauth(value: str, *, account_service: Any, sanitize_account_result: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    pt_key = parse_oauth_pt_key(value)
    if not pt_key:
        raise HTTPException(status_code=400, detail={"error": "pt_key is required"})
    payload = _user_payload(pt_key)
    return sanitize_account_result(account_service.add_account_items([payload]))


def import_state_db(path: str = "", *, account_service: Any, sanitize_account_result: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    try:
        creds = load_from_state_db(path or None)
    except JoyCodeError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error": str(exc)}) from exc
    payload = _user_payload(
        creds.pt_key,
        creds.user_id,
        color_base_url=creds.color_base_url,
        master_base_url=creds.master_base_url,
        tenant=creds.tenant,
        login_type=creds.login_type,
        org_full_name=creds.org_full_name,
    )
    return sanitize_account_result(account_service.add_account_items([payload]))


def _cookie_value(session: requests.Session, name: str) -> str:
    cookies = session.cookies
    for cookie in cookies.jar:  # type: ignore[attr-defined]
        if getattr(cookie, "name", "") == name:
            return _clean(getattr(cookie, "value", ""))
    return ""


def start_qr_login() -> dict[str, Any]:
    session = requests.Session()
    response = session.get(QR_SHOW_URL.format(ts=int(time.time() * 1000)), headers={"User-Agent": JD_USER_AGENT, "Referer": "https://passport.jd.com/new/login.aspx"}, timeout=30)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail={"error": f"JD QR request failed: HTTP {response.status_code}"})
    token = _cookie_value(session, "wlfstk_smdl")
    if not token:
        raise HTTPException(status_code=502, detail={"error": "JD QR token cookie not found"})
    job_id = uuid.uuid4().hex
    _QR_JOBS[job_id] = {"session": session, "token": token, "created_at": time.time()}
    return {"jobId": job_id, "status": "waiting_for_scan", "qrImage": base64.b64encode(response.content).decode()}


def _qr_job(job_id: str) -> dict[str, Any]:
    job = _QR_JOBS.get(job_id)
    if not job or time.time() - float(job.get("created_at") or 0) > QR_TTL:
        _QR_JOBS.pop(job_id, None)
        raise HTTPException(status_code=404, detail={"error": "JoyCode QR login session expired"})
    return job


def cancel_qr_login(job_id: str) -> dict[str, str]:
    _QR_JOBS.pop(job_id, None)
    return {"status": "cancelled"}


def poll_qr_login(job_id: str, *, account_service: Any, sanitize_account_result: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    job = _qr_job(job_id)
    session: requests.Session = job["session"]
    response = session.get(
        QR_CHECK_URL.format(token=job["token"], ts=int(time.time() * 1000)),
        headers={"User-Agent": JD_USER_AGENT, "Referer": "https://passport.jd.com/new/login.aspx"},
        timeout=30,
    )
    text = response.text
    start, end = text.find("("), text.rfind(")")
    if start < 0 or end < 0:
        return {"jobId": job_id, "status": "waiting_for_scan"}
    try:
        payload = json.loads(text[start + 1:end])
    except Exception:
        return {"jobId": job_id, "status": "waiting_for_scan"}
    code = int(payload.get("code") or 0)
    if code == 201:
        return {"jobId": job_id, "status": "waiting_for_scan"}
    if code == 202:
        return {"jobId": job_id, "status": "scanned"}
    if code in {203, 204, 205}:
        _QR_JOBS.pop(job_id, None)
        return {"jobId": job_id, "status": "expired"}
    if code != 200 or not payload.get("ticket"):
        return {"jobId": job_id, "status": "failed", "message": f"JD QR status code {code}"}
    session.get(QR_VALID_URL.format(ticket=payload["ticket"]), headers={"User-Agent": JD_USER_AGENT, "Referer": "https://passport.jd.com/new/login.aspx"}, timeout=30)
    pt_key = _cookie_value(session, "pt_key")
    if not pt_key:
        return {"jobId": job_id, "status": "failed", "message": "JD login succeeded but pt_key cookie was not found"}
    result = sanitize_account_result(account_service.add_account_items([_user_payload(pt_key)]))
    _QR_JOBS.pop(job_id, None)
    return {"jobId": job_id, "status": "success", **result}
