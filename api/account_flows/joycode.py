from __future__ import annotations

import base64
from http.cookiejar import CookieJar
import json
import time
import uuid
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlparse

from fastapi import HTTPException
from curl_cffi import requests

from services.providers.base import JOYCODE_PROVIDER
from services.providers.joycode.client import JoyCodeClient, JoyCodeError, load_from_state_db, parse_oauth_pt_key

QR_SHOW_URL = "https://qr.m.jd.com/show?appid=133&size=147&t={ts}"
QR_CHECK_URL = "https://qr.m.jd.com/check?appid=133&token={token}&callback=jsonpCallback&_={ts}"
QR_VALID_URL = "https://passport.jd.com/uc/qrCodeTicketValidation?t={ticket}"
JD_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
QR_TTL = 180
JD_COOKIE_HOSTS = ("www.jd.com", "passport.jd.com", "home.jd.com", "jd.com", "plogin.m.jd.com", "m.jd.com", "qr.m.jd.com")
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


def _iter_cookie_items(source: Any) -> list[tuple[str, str]]:
    cookies = getattr(source, "cookies", source)
    items: list[tuple[str, str]] = []
    if isinstance(cookies, dict):
        items.extend((_clean(name), _clean(value)) for name, value in cookies.items())
    else:
        jar = getattr(cookies, "jar", cookies)
        try:
            iterator = iter(jar)
        except TypeError:
            iterator = iter(())
        for cookie in iterator:
            items.append((_clean(getattr(cookie, "name", "")), _clean(getattr(cookie, "value", ""))))
    headers = getattr(source, "headers", None)
    if headers:
        values = []
        get_list = getattr(headers, "get_list", None) or getattr(headers, "getlist", None)
        if callable(get_list):
            values = list(get_list("set-cookie") or get_list("Set-Cookie") or [])
        elif hasattr(headers, "get"):
            raw = headers.get("set-cookie") or headers.get("Set-Cookie")
            values = [raw] if raw else []
        for raw in values:
            first = _clean(raw).split(";", 1)[0]
            if "=" in first:
                name, value = first.split("=", 1)
                items.append((_clean(name), _clean(value)))
    return items


def _cookie_from_domain_jar(source: Any, name: str) -> str:
    cookies = getattr(source, "cookies", source)
    getter = getattr(cookies, "get", None)
    if callable(getter):
        for domain in JD_COOKIE_HOSTS:
            for candidate in (domain, f".{domain}"):
                try:
                    value = getter(name, domain=candidate)
                except Exception:
                    value = None
                if value:
                    return _clean(value)
        try:
            value = getter(name)
        except Exception:
            value = None
        if value:
            return _clean(value)
    jar = getattr(cookies, "jar", cookies)
    if isinstance(jar, CookieJar):
        for domain in JD_COOKIE_HOSTS:
            values = jar._cookies.get(domain) or jar._cookies.get(f".{domain}") or {}
            for path_values in values.values():
                cookie = path_values.get(name)
                if cookie:
                    return _clean(cookie.value)
    return ""


def _cookie_value(source: Any, name: str) -> str:
    value = _cookie_from_domain_jar(source, name)
    if value:
        return value
    for cookie_name, value in _iter_cookie_items(source):
        if cookie_name == name:
            return value
    return ""


def _cookie_names(source: Any) -> list[str]:
    return sorted({name for name, value in _iter_cookie_items(source) if name and value})


def _domain_cookie_names(source: Any) -> list[str]:
    cookies = getattr(source, "cookies", source)
    jar = getattr(cookies, "jar", cookies)
    names: set[str] = set()
    if isinstance(jar, CookieJar):
        for domain in JD_COOKIE_HOSTS:
            values = jar._cookies.get(domain) or jar._cookies.get(f".{domain}") or {}
            for path_values in values.values():
                names.update(path_values)
    return sorted(names)


def _jar_cookie_summary(source: Any) -> list[dict[str, str]]:
    cookies = getattr(source, "cookies", source)
    jar = getattr(cookies, "jar", cookies)
    summary: list[dict[str, str]] = []
    if isinstance(jar, CookieJar):
        for domain, paths in jar._cookies.items():
            for path, values in paths.items():
                for name in values:
                    summary.append({"domain": domain, "path": path, "name": name})
    return sorted(summary, key=lambda item: (item["domain"], item["path"], item["name"]))


def _json_payload(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        try:
            payload = json.loads(response.text)
        except Exception:
            return {}
    return payload if isinstance(payload, dict) else {}


def _safe_url_info(value: Any) -> dict[str, str]:
    parsed = urlparse(_clean(value))
    if not parsed.scheme or not parsed.netloc:
        return {}
    return {"scheme": parsed.scheme, "host": parsed.netloc, "path": parsed.path}


def _can_follow_validation_url(payload: dict[str, Any]) -> bool:
    if int(payload.get("returnCode") or 0) != 0:
        return False
    url_info = _safe_url_info(payload.get("url"))
    risk_code = int(payload.get("riskCode") or 0)
    return bool(url_info) and (risk_code == 0 or (risk_code == 1100 and url_info.get("host") == "passport.jd.com" and url_info.get("path") == "/relay/loginRelay"))


def _validation_debug(session: requests.Session, response: Any) -> dict[str, Any]:
    payload = _json_payload(response)
    return {
        "validation_status": getattr(response, "status_code", None),
        "validation_url": _safe_url_info(getattr(response, "url", "")),
        "response_cookie_names": _cookie_names(response),
        "session_cookie_names": _domain_cookie_names(session),
        "json_keys": sorted(payload) if payload else [],
        "returnCode": payload.get("returnCode") if payload else None,
        "riskCode": payload.get("riskCode") if payload else None,
        "has_url": bool(_clean(payload.get("url"))) if payload else False,
        "url": _safe_url_info(payload.get("url")) if payload else {},
    }


def _follow_validation_url(session: requests.Session, response: Any) -> str:
    payload = _json_payload(response)
    if not isinstance(payload, dict) or not _can_follow_validation_url(payload):
        return ""
    url = _clean(payload.get("url"))
    if not url:
        return ""
    if url.startswith("http://"):
        url = "https://" + url[7:]
    follow = session.get(url, headers={"User-Agent": JD_USER_AGENT, "Referer": "https://passport.jd.com/new/login.aspx", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}, timeout=30)
    return _cookie_value(session, "pt_key") or _cookie_value(follow, "pt_key")


def _follow_validation_debug(session: requests.Session, response: Any) -> dict[str, Any]:
    payload = _json_payload(response)
    if not isinstance(payload, dict) or not _can_follow_validation_url(payload):
        return {}
    url = _clean(payload.get("url"))
    if not url:
        return {}
    if url.startswith("http://"):
        url = "https://" + url[7:]
    follow = session.get(url, headers={"User-Agent": JD_USER_AGENT, "Referer": "https://passport.jd.com/new/login.aspx", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}, timeout=30)
    return {
        "follow_status": getattr(follow, "status_code", None),
        "follow_url": _safe_url_info(getattr(follow, "url", "")),
        "follow_response_cookie_names": _cookie_names(follow),
        "follow_session_cookie_names": _domain_cookie_names(session),
        "follow_cookie_summary": _jar_cookie_summary(session),
    }


def _validation_pt_key(session: requests.Session, response: Any) -> str:
    return _cookie_value(session, "pt_key") or _cookie_value(response, "pt_key") or _follow_validation_url(session, response)


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
    validation = session.get(QR_VALID_URL.format(ticket=quote(_clean(payload["ticket"]), safe="")), headers={"User-Agent": JD_USER_AGENT, "Referer": "https://passport.jd.com/new/login.aspx"}, timeout=30)
    pt_key = _validation_pt_key(session, validation)
    if not pt_key:
        debug = _validation_debug(session, validation)
        debug["follow"] = _follow_validation_debug(session, validation)
        return {"jobId": job_id, "status": "failed", "message": "JD login succeeded but pt_key cookie was not found", "debug": debug}
    result = sanitize_account_result(account_service.add_account_items([_user_payload(pt_key)]))
    _QR_JOBS.pop(job_id, None)
    return {"jobId": job_id, "status": "success", **result}
