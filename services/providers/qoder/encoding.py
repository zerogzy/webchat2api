from __future__ import annotations

import base64

STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
CUSTOM = "_doRTgHZBKcGVjlvpC,@aFSx#DPuNJme&i*MzLOEn)sUrthbf%Y^w.(kIQyXqWA!"
TRANS = str.maketrans(STD + "=", CUSTOM + "$")


def qoder_encode_body(data: bytes | str) -> bytes:
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    text = base64.b64encode(raw).decode("ascii")
    n = len(text)
    a = n // 3
    return (text[n - a:] + text[a:n - a] + text[:a]).translate(TRANS).encode("latin1")
