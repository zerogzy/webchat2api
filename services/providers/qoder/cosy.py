from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

RSA_PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDA8iMH5c02LilrsERw9t6Pv5Nc
4k6Pz1EaDicBMpdpxKduSZu5OANqUq8er4GM95omAGIOPOh+Nx0spthYA2BqGz+l
6HRkPJ7S236FZz73In/KVuLnwI8JJ2CbuJap8kvheCCZpmAWpb/cPx/3Vr/J6I17
XcW+ML9FoCI6AOvOzwIDAQAB
-----END PUBLIC KEY-----"""


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _aes_cbc_base64(text: str, key: str) -> str:
    key_bytes = key.encode("utf-8")
    raw = text.encode("utf-8")
    pad = 16 - (len(raw) % 16)
    padded = raw + bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key_bytes), modes.CBC(key_bytes)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")


def _rsa_base64(text: str) -> str:
    public_key = serialization.load_pem_public_key(RSA_PUBLIC_KEY)
    encrypted = public_key.encrypt(text.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode("ascii")


def _sig_path(url: str) -> str:
    path = urlparse(url).path or ""
    return path[len("/algo"):] if path.startswith("/algo") else path


def build_cosy_headers(body: bytes, url: str, creds: dict[str, Any]) -> dict[str, str]:
    user_id = str(creds.get("user_id") or "").strip()
    token = str(creds.get("token") or "").strip()
    if not user_id:
        raise ValueError("qoder user_id is required")
    if not token:
        raise ValueError("qoder device token is required")

    aes_key = str(uuid.uuid4())[:16]
    info = _aes_cbc_base64(json.dumps({
        "uid": user_id,
        "security_oauth_token": token,
        "name": str(creds.get("name") or ""),
        "aid": "",
        "email": str(creds.get("email") or ""),
    }, ensure_ascii=False, separators=(",", ":")), aes_key)
    cosy_key = _rsa_base64(aes_key)
    request_id = str(uuid.uuid4())
    payload = base64.b64encode(json.dumps({
        "version": "v1",
        "requestId": request_id,
        "info": info,
        "cosyVersion": "1.0.0",
        "ideVersion": "",
    }, separators=(",", ":")).encode("utf-8")).decode("ascii")
    timestamp = str(int(time.time()))
    sig_path = _sig_path(url)
    sig = _md5(f"{payload}\n{cosy_key}\n{timestamp}\n".encode("latin1") + body + f"\n{sig_path}".encode("latin1"))
    machine_id = str(creds.get("machine_id") or "").strip() or str(uuid.uuid4())
    return {
        "Authorization": f"Bearer COSY.{payload}.{sig}",
        "Cosy-Key": cosy_key,
        "Cosy-User": user_id,
        "Cosy-Date": timestamp,
        "Cosy-Version": "1.0.0",
        "Cosy-Machineid": machine_id,
        "Cosy-Machinetoken": machine_id,
        "Cosy-Machinetype": "5",
        "Cosy-Machineos": "x86_64_windows",
        "Cosy-Clienttype": "5",
        "Cosy-Clientip": "127.0.0.1",
        "Cosy-Bodyhash": _md5(body),
        "Cosy-Bodylength": str(len(body)),
        "Cosy-Sigpath": sig_path,
        "Cosy-Data-Policy": "disagree",
        "Cosy-Organization-Id": "",
        "Cosy-Organization-Tags": "",
        "Login-Version": "v2",
        "X-Request-Id": str(uuid.uuid4()),
    }
