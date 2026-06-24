"""CatPaw (Meituan MCopilot) upstream client.

Self-contained provider transport for the CatPaw chat API:
- request encryption: AES-128-ECB body + RSA-OAEP(SHA-1) wrapped key in `encrypted-key`
- streaming SSE chat (plaintext response, cumulative `content` -> emitted as deltas)
- model list / S3 image upload (encrypted responses, decrypted with the private key)
- token management (env or data/catpaw_token.json) with auto-refresh

CatPaw accounts are persisted through the shared account pool, while this module
keeps the upstream transport and token refresh details provider-local.
"""
from __future__ import annotations

import base64
import json
import os
import ssl
import time
import urllib.parse
import uuid
from typing import Any, Iterator

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

try:  # project standard HTTP client (impersonates browsers, proxy-aware)
    from curl_cffi import requests as _curl  # type: ignore
    _HAS_CURL = True
except Exception:  # pragma: no cover - fallback for envs without curl_cffi
    _curl = None
    _HAS_CURL = False

try:
    from services.config import DATA_DIR  # type: ignore
    _DATA_DIR = str(DATA_DIR)
except Exception:  # pragma: no cover - standalone use
    _DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data")

HOST = "catpaw.meituan.com"
BASE_URL = f"https://{HOST}"
STREAM_PATH = "/api/gpt/openai/stream"

MIS_ID = os.environ.get("CATPAW_MIS_ID", "").strip()
TENANT = os.environ.get("CATPAW_TENANT", "5282fa6645").strip()

S3_PUBLIC_BASE = "https://s3plus.meituan.net/catpaw-pub-external/"

# Public key is enough for request encryption (the only thing chat needs).
_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAupKxd+EWsvyObw4CQdcK
SaEpvmD17NqqII6TjuuSFL9uf0pkv1dPyVlbeHdaNF9ZljO+cPiS3svL1X/5y9/A
ES+aAsByiexCOwyBQwwtqt1qVIw1C31XMDtDuNAcfPcFuoKJdWs3PZlcfgWtuzAO
9mPr2WQ6Hl6rMMAnYo5diDP/mu6K2DcS5C5vkhFC5t2TqNlB2J8aCYKHkwrD3djW
rAas8/MbYEH80tKUUtTSNKpuUMmaGUPGbt1FTkvnnarb4WKXN52Qyskwv1oC50KB
5kV2EQ2Q+HGRmyvwDQLWp5vWho3DCEZW8/qXPEsmjvx/gfAvWkzgiYiQI5lVs5Pl
CwIDAQAB
-----END PUBLIC KEY-----"""


class CatpawError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def now_ms() -> int:
    return int(time.time() * 1000)


# ----------------------------------------------------------------------------
# Crypto
# ----------------------------------------------------------------------------
_keys: dict[str, Any] | None = None


def _load_keys() -> dict[str, Any]:
    global _keys
    if _keys is not None:
        return _keys
    pub_pem = _PUBLIC_KEY_PEM
    priv_pem = None
    rsa_file = os.environ.get("CATPAW_RSA_FILE") or os.path.join(_DATA_DIR, "catpaw_rsa.json")
    try:
        with open(rsa_file, encoding="utf-8") as f:
            data = json.load(f)
        pub_pem = data.get("publicKey") or pub_pem
        priv_pem = data.get("privateKey")
    except Exception:
        pass
    pub = serialization.load_pem_public_key(pub_pem.encode())
    priv = serialization.load_pem_private_key(priv_pem.encode(), password=None) if priv_pem else None
    _keys = {"public": pub, "private": priv}
    return _keys


def _oaep() -> rsa_padding.OAEP:
    return rsa_padding.OAEP(mgf=rsa_padding.MGF1(algorithm=hashes.SHA1()),
                            algorithm=hashes.SHA1(), label=None)


def encrypt_request(body: dict[str, Any]) -> tuple[str, str]:
    """Return (encrypted_body_b64, encrypted_aes_key_b64)."""
    pub = _load_keys()["public"]
    aes_key = os.urandom(16)
    enc_key = base64.b64encode(pub.encrypt(base64.b64encode(aes_key), _oaep())).decode()
    padder = PKCS7(128).padder()
    plain = padder.update(json.dumps(body, ensure_ascii=False).encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()
    enc_body = base64.b64encode(enc.update(plain) + enc.finalize()).decode()
    return enc_body, enc_key


def decrypt_response(enc_data: str, enc_key: str | None) -> Any:
    if not enc_key:
        return enc_data
    priv = _load_keys()["private"]
    if priv is None:
        raise CatpawError("private key required to decrypt response (set CATPAW_RSA_FILE)", 500)
    dec_key = priv.decrypt(base64.b64decode(enc_key), _oaep())  # ascii base64 of aes key
    aes_key = base64.b64decode(dec_key)
    dec = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor()
    padded = dec.update(base64.b64decode(enc_data)) + dec.finalize()
    unpadder = PKCS7(128).unpadder()
    text = (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
    try:
        return json.loads(text)
    except Exception:
        return text


# ----------------------------------------------------------------------------
# HTTP (curl_cffi when available, stdlib fallback otherwise)
# ----------------------------------------------------------------------------
def _common_headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "client-type": "CatPaw IDE",
        "ide-version": "2026.2.3",
        "plugin-id": "mt-idekit.mt-idekit-code",
        "plugin-version": "2026.2.2",
        "client-env": "LOCAL_IDE",
        "platform-info": "win32-x64",
    }
    if TENANT:
        headers["tenant"] = TENANT
    return headers


def _auth_headers(token: str, mis_id: str | None = None) -> dict[str, str]:
    mis = str(mis_id or MIS_ID).strip()
    if not mis:
        raise CatpawError("CATPAW_MIS_ID is required for CatPaw authenticated requests", 500)
    h = _common_headers()
    h.update({
        "user-mis-id": mis,
        "user-uid": mis,
        "mis-id": mis,
        "Cookie": f"1d47d6ff96_passportid={token}; f32a546874_ssoid={token}",
        "Catpaw-Auth": token,
    })
    return h


def _auth_headers_optional_mis(token: str, mis_id: str | None = None) -> dict[str, str]:
    mis = str(mis_id or MIS_ID).strip()
    h = _common_headers()
    h.update({
        "Cookie": f"1d47d6ff96_passportid={token}; f32a546874_ssoid={token}",
        "Catpaw-Auth": token,
    })
    if mis:
        h.update({
            "user-mis-id": mis,
            "user-uid": mis,
            "mis-id": mis,
        })
    return h


def _http_get(path_or_url: str, headers: dict[str, str], timeout: int = 60) -> tuple[int, str, dict[str, str]]:
    url = path_or_url if path_or_url.startswith("http") else BASE_URL + path_or_url
    if _HAS_CURL:
        r = _curl.get(url, headers=headers, verify=False, timeout=timeout)
        return int(r.status_code), r.text, {k.lower(): v for k, v in r.headers.items()}
    import http.client
    parsed = urllib.parse.urlparse(url)
    conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                       context=ssl._create_unverified_context(), timeout=timeout)
    try:
        conn.request("GET", parsed.path + (("?" + parsed.query) if parsed.query else ""), headers=headers)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
        return resp.status, data, {k.lower(): v for k, v in resp.getheaders()}
    finally:
        conn.close()


def _http_put(url: str, headers: dict[str, str], body: bytes, timeout: int = 60) -> int:
    if _HAS_CURL:
        r = _curl.put(url, data=body, headers=headers, verify=False, timeout=timeout)
        return int(r.status_code)
    import http.client
    parsed = urllib.parse.urlparse(url)
    conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                       context=ssl._create_unverified_context(), timeout=timeout)
    try:
        conn.request("PUT", parsed.path + (("?" + parsed.query) if parsed.query else ""), body=body, headers=headers)
        return conn.getresponse().status
    finally:
        conn.close()


def _http_post_json(path_or_url: str, headers: dict[str, str], body_obj: Any, timeout: int = 30) -> tuple[int, str]:
    url = path_or_url if path_or_url.startswith("http") else BASE_URL + path_or_url
    data = json.dumps(body_obj, ensure_ascii=False)
    if _HAS_CURL:
        r = _curl.post(url, data=data, headers=headers, verify=False, timeout=timeout)
        return int(r.status_code), r.text
    import http.client
    parsed = urllib.parse.urlparse(url)
    conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                       context=ssl._create_unverified_context(), timeout=timeout)
    try:
        body_bytes = data.encode("utf-8")
        h = dict(headers)
        h["Content-Length"] = str(len(body_bytes))
        conn.request("POST", parsed.path + (("?" + parsed.query) if parsed.query else ""), body=body_bytes, headers=h)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


def _parse_json_or_decrypt(text: str, headers: dict[str, str]) -> Any:
    try:
        parsed: Any = json.loads(text)
    except Exception:
        return text
    if isinstance(parsed, str):
        return decrypt_response(parsed, headers.get("encrypted-key"))
    return parsed


class _Stream:
    """Open a streaming POST and iterate decoded SSE lines."""

    def __init__(self, path: str, headers: dict[str, str], body: str, timeout: int = 180) -> None:
        self.url = BASE_URL + path
        self.headers = headers
        self.body = body
        self.timeout = timeout
        self.status = 0
        self._resp = None
        self._conn = None
        self._error_text = ""

    def open(self) -> "_Stream":
        if _HAS_CURL:
            self._resp = _curl.post(self.url, data=self.body, headers=self.headers,
                                    stream=True, verify=False, timeout=self.timeout)
            self.status = int(self._resp.status_code)
            if self.status != 200:
                self._error_text = self._resp.text
                self._resp.close()
        else:
            import http.client
            parsed = urllib.parse.urlparse(self.url)
            self._conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                                     context=ssl._create_unverified_context(), timeout=self.timeout)
            self._conn.request("POST", parsed.path, body=self.body.encode("utf-8") if isinstance(self.body, str) else self.body,
                               headers=self.headers)
            self._resp = self._conn.getresponse()
            self.status = self._resp.status
            if self.status != 200:
                self._error_text = self._resp.read().decode("utf-8", "replace")
                self._conn.close()
        return self

    @property
    def error_text(self) -> str:
        return self._error_text

    def lines(self) -> Iterator[str]:
        if self.status != 200:
            return
        try:
            if _HAS_CURL:
                for raw in self._resp.iter_lines():
                    yield raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            else:
                while True:
                    raw = self._resp.readline()
                    if not raw:
                        break
                    yield raw.decode("utf-8", "replace").rstrip("\r\n")
        finally:
            self.close()

    def close(self) -> None:
        try:
            if self._resp is not None and hasattr(self._resp, "close"):
                self._resp.close()
        except Exception:
            pass
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Token management
# ----------------------------------------------------------------------------
class _TokenManager:
    def __init__(self) -> None:
        self._token: dict[str, Any] | None = None

    def _file(self) -> str:
        return os.environ.get("CATPAW_TOKEN_FILE") or os.path.join(_DATA_DIR, "catpaw_token.json")

    def _load(self) -> dict[str, Any] | None:
        env_at = os.environ.get("CATPAW_ACCESS_TOKEN")
        if env_at:
            return {
                "accessToken": env_at,
                "refreshToken": os.environ.get("CATPAW_REFRESH_TOKEN") or "",
                "expires": 0,
                "from_env": True,
            }
        try:
            with open(self._file(), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _save(self, token: dict[str, Any]) -> None:
        if token.get("from_env"):
            return
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with open(self._file(), "w", encoding="utf-8") as f:
                json.dump(token, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _refresh(self, token: dict[str, Any]) -> bool:
        access = token.get("accessToken")
        refresh = token.get("refreshToken")
        if not access or not refresh:
            return False
        try:
            d = refresh_token_value(access, refresh)
        except Exception:
            return False
        token["accessToken"] = d["accessToken"]
        token["refreshToken"] = d.get("refreshToken") or refresh
        token["expires"] = d.get("expires")
        token["refreshExpires"] = d.get("refreshExpires")
        self._save(token)
        return True

    def access_token(self) -> str:
        if self._token is None:
            self._token = self._load()
        if not self._token or not self._token.get("accessToken"):
            raise CatpawError(
                "no CatPaw token found; set CATPAW_ACCESS_TOKEN or place data/catpaw_token.json", 401)
        expires = self._token.get("expires")
        if expires and now_ms() > expires - 60000 and self._token.get("refreshToken"):
            self._refresh(self._token)
        return self._token["accessToken"]

    def force_refresh(self) -> bool:
        if self._token is None:
            self._token = self._load()
        return bool(self._token) and self._refresh(self._token)


token_manager = _TokenManager()


# ----------------------------------------------------------------------------
# QR login (account import) — plaintext JSON, success == code 0
# ----------------------------------------------------------------------------
def get_qrcode() -> dict[str, Any]:
    """Create a QR login session. Returns {code, qrCodeImageUrl, expireTime}."""
    headers = _common_headers()
    headers["Catpaw-Auth"] = "{}"
    status, text, _ = _http_get("/api/login/qrcode", headers, timeout=30)
    try:
        j = json.loads(text)
    except Exception:
        raise CatpawError(f"qrcode failed (HTTP {status})", 502)
    if not isinstance(j, dict) or j.get("code") != 0 or not isinstance(j.get("data"), dict):
        raise CatpawError((isinstance(j, dict) and j.get("msg")) or "qrcode failed", 502)
    return j["data"]


def poll_access_token(code: str) -> dict[str, Any] | None:
    """Poll a QR session. Returns the raw `data` dict, which is either a pending state
    (e.g. ``{"scanned": false}`` / ``{"scanned": true}``) or the token dict (containing
    ``accessToken``) once the user has scanned AND confirmed. Returns None on HTTP/parse
    error or a non-zero API code. Callers MUST check for ``accessToken`` to detect success
    (a bare ``{"scanned": ...}`` is NOT a login)."""
    headers = _common_headers()
    headers["Catpaw-Auth"] = "{}"
    _status, text = _http_post_json("/api/login/accessToken", headers, {"code": code})
    try:
        j = json.loads(text)
    except Exception:
        return None
    if isinstance(j, dict) and j.get("code") == 0 and isinstance(j.get("data"), dict):
        return j["data"]
    return None


def get_user_info(access_token: str, mis_id: str | None = None) -> dict[str, Any]:
    """Fetch the logged-in user's profile (loginName / mis id / email)."""
    _status, text, _ = _http_get("/api/login/userInfo", _auth_headers_optional_mis(access_token, mis_id), timeout=30)
    try:
        j = json.loads(text)
    except Exception:
        return {}
    if isinstance(j, dict) and j.get("code") == 0 and isinstance(j.get("data"), dict):
        return j["data"]
    return {}


def _catpaw_api_message(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("msg", "message"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("msg", "message"):
                value = str(data.get(key) or "").strip()
                if value:
                    return value
    return ""


def refresh_token_value(access_token: str, refresh_token: str) -> dict[str, Any]:
    """Exchange a refresh token for a fresh token dict.

    CatPaw expects the current access token in Catpaw-Auth and the rotating
    refresh token in the JSON body. Successful refreshes can invalidate the old
    refresh token, so callers must persist the returned token pair immediately.
    """
    if not access_token:
        raise CatpawError("CatPaw access token is required to refresh token", 400)
    if not refresh_token:
        raise CatpawError("CatPaw refresh token is required to refresh token", 400)
    headers = _common_headers()
    headers["Catpaw-Auth"] = access_token
    status, text = _http_post_json("/api/login/refreshToken", headers, {"refreshToken": refresh_token})
    try:
        j = json.loads(text)
    except Exception:
        raise CatpawError(f"CatPaw token refresh failed (HTTP {status}): invalid JSON response", status)
    if isinstance(j, dict) and j.get("code") == 0 and isinstance(j.get("data"), dict):
        return j["data"]
    message = _catpaw_api_message(j) or "CatPaw token refresh failed"
    code = j.get("code") if isinstance(j, dict) else None
    suffix = f" (code {code})" if code is not None else ""
    raise CatpawError(f"CatPaw token refresh failed (HTTP {status}){suffix}: {message}", status)


def get_user_limit(access_token: str, mis_id: str | None = None) -> dict[str, Any]:
    """Return CatPaw user quota response from /api/user/limit."""
    headers = _auth_headers_optional_mis(access_token, mis_id)
    headers["Accept"] = "application/json, text/plain, */*"
    status, text, response_headers = _http_get("/api/user/limit", headers, timeout=30)
    if status != 200:
        raise CatpawError(f"catpaw quota failed (HTTP {status}): {text[:200]}", status)
    parsed = _parse_json_or_decrypt(text, response_headers)
    if not isinstance(parsed, dict):
        raise CatpawError("catpaw quota response is invalid", 502)
    return parsed


def apply_quota(access_token: str, mis_id: str | None = None) -> dict[str, Any]:
    """Request extra CatPaw quota via /api/user/addQuota."""
    enc_body, enc_key = encrypt_request({})
    headers = _auth_headers_optional_mis(access_token, mis_id)
    headers["Accept"] = "application/json, text/plain, */*"
    headers["encrypted-key"] = enc_key
    url = BASE_URL + "/api/user/addQuota"
    if _HAS_CURL:
        r = _curl.post(url, data=enc_body, headers=headers, verify=False, timeout=30)
        status, text = int(r.status_code), r.text
        response_headers = {k.lower(): v for k, v in r.headers.items()}
    else:
        import http.client
        parsed_url = urllib.parse.urlparse(url)
        conn = http.client.HTTPSConnection(parsed_url.hostname, parsed_url.port or 443,
                                           context=ssl._create_unverified_context(), timeout=30)
        try:
            body_bytes = enc_body.encode("utf-8")
            headers["Content-Length"] = str(len(body_bytes))
            conn.request("POST", parsed_url.path, body=body_bytes, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            text = resp.read().decode("utf-8", "replace")
            response_headers = {k.lower(): v for k, v in resp.getheaders()}
        finally:
            conn.close()
    if status != 200:
        raise CatpawError(f"catpaw quota apply failed (HTTP {status}): {text[:200]}", status)
    parsed = _parse_json_or_decrypt(text, response_headers)
    if not isinstance(parsed, dict):
        raise CatpawError("catpaw quota apply response is invalid", 502)
    return parsed


# ----------------------------------------------------------------------------
# Message conversion: webchat2api-normalized -> CatPaw request
# ----------------------------------------------------------------------------
def _to_catpaw_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user")
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        text_parts: list[str] = []
        images: list[str] = []
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = str(part.get("type") or "")
                if ptype == "text":
                    text_parts.append(str(part.get("text") or ""))
                elif ptype == "image":
                    data = part.get("data")
                    mime = str(part.get("mime") or "image/png")
                    if isinstance(data, (bytes, bytearray)):
                        images.append(f"data:{mime};base64,{base64.b64encode(bytes(data)).decode()}")
                    elif isinstance(data, str) and data.startswith("data:"):
                        images.append(data)
                elif ptype == "image_url":
                    iu = part.get("image_url")
                    url = iu.get("url") if isinstance(iu, dict) else iu
                    if isinstance(url, str) and url.startswith("data:"):
                        images.append(url)
                elif ptype == "file":
                    data = part.get("data")
                    if isinstance(data, (bytes, bytearray)):
                        text_parts.append(f"[file: {part.get('name') or 'attachment'}]\n"
                                          + bytes(data).decode("utf-8", "replace"))
        msg: dict[str, Any] = {"role": role, "content": "\n".join(t for t in text_parts if t)}
        if images and role == "user":
            msg["multiModalContent"] = [{"type": "image_url", "image_url": {"url": u}} for u in images]
        out.append(msg)
    return out


def _has_image(catpaw_messages: list[dict[str, Any]]) -> bool:
    return any(m.get("multiModalContent") for m in catpaw_messages)


# ----------------------------------------------------------------------------
# Chat (streaming)
# ----------------------------------------------------------------------------
def _build_stream_body(
    catpaw_messages: list[dict[str, Any]],
    type_code: int | None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "conversationId": conversation_id or str(uuid.uuid4()),
        "messages": catpaw_messages,
        "triggerMode": "TOOLWINDOW_CHAT",
        "planPromptEnabled": False,
    }
    if type_code is not None:
        body["userModelTypeCode"] = type_code
        body["chatApplyModeType"] = "chat"
        body["agentModeConfig"] = {
            "model": {
                "default": type_code,
                "maxMode": True,
                "autoMode": False,
            }
        }
    _debug_dump_conversation_body(body)
    return body


def _debug_dump_conversation_body(body: dict[str, Any]) -> None:
    path = os.environ.get("CATPAW_DEBUG_CONVERSATION_DUMP")
    if not path:
        return
    try:
        messages = body.get("messages") if isinstance(body.get("messages"), list) else []
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "time": time.time(),
                        "conversationId": body.get("conversationId"),
                        "userModelTypeCode": body.get("userModelTypeCode"),
                        "messageCount": len(messages),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    except OSError:
        pass


def stream_chat_deltas(
    messages: list[dict[str, Any]],
    type_code: int | None,
    token: str | None = None,
    mis_id: str | None = None,
    on_auth_fail: Any = None,
    conversation_id: str | None = None,
) -> Iterator[str]:
    """Yield text deltas. CatPaw streams a cumulative `content`; we emit the suffix diff.

    If `token` is given it is used directly (account-store path); on a 401 the
    `on_auth_fail` callback (if any) is invoked to obtain a refreshed token. When
    `token` is None the module-level token_manager (env / data file) is used.
    """
    catpaw_messages = _to_catpaw_messages(messages)
    if not catpaw_messages:
        raise CatpawError("no messages to send", 400)
    body = _build_stream_body(catpaw_messages, type_code, conversation_id=conversation_id)

    use_manager = token is None
    cur_token = token or ""
    cur_mis = mis_id
    for attempt in range(2):
        if use_manager:
            cur_token = token_manager.access_token()
            cur_mis = MIS_ID
        enc_body, enc_key = encrypt_request(body)
        headers = _auth_headers(cur_token, cur_mis)
        headers["encrypted-key"] = enc_key
        headers["Accept"] = "text/event-stream"
        stream = _Stream(STREAM_PATH, headers, enc_body).open()
        if stream.status != 200:
            err = stream.error_text or ""
            if attempt == 0 and (stream.status == 401 or "auth failed" in err):
                new_token = None
                if use_manager:
                    if token_manager.force_refresh():
                        new_token = token_manager.access_token()
                elif on_auth_fail is not None:
                    try:
                        new_token = on_auth_fail()
                    except Exception:
                        new_token = None
                if new_token:
                    cur_token = new_token
                    continue
            raise CatpawError(f"catpaw upstream error (HTTP {stream.status}): {err[:200]}",
                              stream.status if stream.status >= 400 else 502)
        prev = ""
        for line in stream.lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except Exception:
                continue
            sc = event.get("statusCode")
            if isinstance(sc, int) and sc >= 400:
                raise CatpawError(f"catpaw model error (statusCode {sc}): {event.get('msg') or ''}".strip(), 502)
            ct = event.get("content")
            if isinstance(ct, str) and ct:
                if event.get("lastOne") and ct == "[DONE]":
                    break
                if ct.startswith(prev):
                    delta = ct[len(prev):]
                elif prev == "":
                    delta = ct
                else:
                    continue
                prev = ct
                if delta:
                    yield delta
        return


def chat_text(
    messages: list[dict[str, Any]],
    type_code: int | None,
    token: str | None = None,
    mis_id: str | None = None,
    on_auth_fail: Any = None,
    conversation_id: str | None = None,
) -> str:
    return "".join(
        stream_chat_deltas(
            messages,
            type_code,
            token=token,
            mis_id=mis_id,
            on_auth_fail=on_auth_fail,
            conversation_id=conversation_id,
        )
    )


# ----------------------------------------------------------------------------
# Model list (bonus) + image upload (bonus)
# ----------------------------------------------------------------------------
def list_models() -> list[dict[str, Any]]:
    token = token_manager.access_token()
    status, text, headers = _http_get("/api/chat/get-user-available-models", _auth_headers(token))
    if status != 200:
        raise CatpawError(f"model list failed (HTTP {status})", status)
    try:
        parsed: Any = json.loads(text)
    except Exception:
        parsed = text
    if isinstance(parsed, str):
        parsed = decrypt_response(parsed, headers.get("encrypted-key"))
    if isinstance(parsed, dict) and parsed.get("code") == 0:
        return parsed.get("data") or []
    return []


def upload_image(image_bytes: bytes, mime: str = "image/png", ext: str = "png") -> str:
    """Upload to CatPaw's public S3 bucket via presign; returns the public URL."""
    token = token_manager.access_token()
    key = f"copilot/chat/{uuid.uuid4().hex}.{ext}"
    q = urllib.parse.urlencode({"objectName": key, "contentType": mime, "isUpload": "true"})
    status, text, headers = _http_get(f"/api/s3/presign/generate?{q}", _auth_headers(token))
    if status != 200:
        raise CatpawError(f"presign failed (HTTP {status})", status)
    try:
        parsed: Any = json.loads(text)
    except Exception:
        parsed = text
    if isinstance(parsed, str):
        parsed = decrypt_response(parsed, headers.get("encrypted-key"))
    presign_url = parsed.get("data") if isinstance(parsed, dict) else None
    if not presign_url:
        raise CatpawError("presign response missing url", 502)
    put_url = presign_url.replace("s3plus.sankuai.com", "s3plus.meituan.net").replace("http://", "https://")
    put_status = _http_put(put_url, {"Content-Type": mime}, image_bytes)
    if put_status != 200:
        raise CatpawError(f"S3 PUT failed (HTTP {put_status})", 502)
    return S3_PUBLIC_BASE + key
