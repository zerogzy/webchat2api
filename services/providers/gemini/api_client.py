from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import MethodType
from typing import Any

from fastapi import HTTPException

from services.providers.base import ModelSpec
from services.providers.gemini.accounts import gemini_cookie_state
from services.proxy_service import proxy_settings


@dataclass(frozen=True)
class GeminiApiCompletion:
    content: str
    raw_response: object = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GeminiImageResult:
    b64_json: str
    revised_prompt: str = ""


class GeminiApiError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code

    def to_http_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"error": str(self)}
        if self.code:
            detail["code"] = self.code
        return detail


def run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _metadata_from_output(output: object) -> dict[str, str]:
    metadata: dict[str, str] = {}
    raw_metadata = getattr(output, "metadata", None)
    if isinstance(raw_metadata, list):
        for key, index in (("cid", 0), ("rid", 1), ("rcid", 2)):
            if len(raw_metadata) > index and raw_metadata[index]:
                metadata[key] = str(raw_metadata[index])
    rcid = getattr(output, "rcid", None)
    if rcid:
        metadata.setdefault("rcid", str(rcid))
    return metadata


def _cookies_from_client(client: object) -> dict[str, str]:
    cookies: dict[str, str] = {}
    jar = getattr(getattr(client, "cookies", None), "jar", None)
    if jar is None:
        return cookies
    for cookie in jar:
        name = getattr(cookie, "name", None)
        value = getattr(cookie, "value", None)
        if name and value:
            cookies[str(name)] = str(value)
    return cookies


def _model_name(spec: ModelSpec, *, has_files: bool = False) -> str:
    if has_files:
        return "gemini-3-pro"
    return spec.upstream_model or spec.id


def _image_prompt(prompt: str, size: str | None = None) -> str:
    text = _safe_text(prompt)
    if size:
        text = f"{text}\n\n请生成一张图片，宽高比为 {size}。"
    return f"GENERATE an image: {text}"


def _image_edit_prompt(prompt: str, size: str | None = None) -> str:
    text = _safe_text(prompt)
    if size:
        text = f"{text}\n\n请输出一张图片，宽高比为 {size}。"
    return f"请基于上传的参考图片生成或编辑图片：{text}"


def _exception_to_error(exc: Exception) -> GeminiApiError:
    name = type(exc).__name__
    message = _safe_text(exc) or name
    lowered = message.lower()
    if name == "AuthError" or any(marker in lowered for marker in ("auth", "cookie", "unauthorized", "permission denied")):
        return GeminiApiError(message, status_code=401, code="gemini_auth_failed")
    if name == "UsageLimitExceeded" or "usage limit" in lowered or "quota" in lowered:
        return GeminiApiError(message, status_code=429, code="gemini_rate_limited")
    if name == "TemporarilyBlocked" or "temporarily" in lowered:
        return GeminiApiError(message, status_code=429, code="gemini_temporarily_blocked")
    if name == "ModelInvalid" or "model" in lowered and "invalid" in lowered:
        return GeminiApiError(message, status_code=400, code="gemini_model_invalid")
    return GeminiApiError(message, status_code=502, code="gemini_upstream_error")


def _cookie_domain(name: str) -> str:
    if name in {"COMPASS"}:
        return ".gemini.google.com"
    if name in {"__itrace_wid"}:
        return "gemini.google.com"
    return ".google.com"


async def _open_client(account: dict[str, Any], *, auto_refresh: bool = False, init_rpc: bool = True):
    from curl_cffi.requests import Cookies
    from gemini_webapi import GeminiClient

    state = gemini_cookie_state(account, require_session_cookies=True)
    proxy = _safe_text(account.get("proxy")) or proxy_settings.resolve_proxy(account)
    client = GeminiClient(state.psid, state.psidts, proxy=proxy or None, verify=True)
    cookie_jar = Cookies()
    for name, value in state.cookies.items():
        cookie_jar.set(name, value, domain=_cookie_domain(name), path="/", secure=name.startswith("__Secure-") or name == "COMPASS")
    client.cookies = cookie_jar
    if not init_rpc:
        async def _skip_init_rpc(self) -> None:
            return None

        client._init_rpc = MethodType(_skip_init_rpc, client)
    await client.init(auto_refresh=auto_refresh, verbose=False)
    return client


async def _validate_account(account: dict[str, Any]) -> dict[str, Any]:
    client = await _open_client(account, auto_refresh=False)
    try:
        cookies = _cookies_from_client(client)
        models = client.list_models() or []
        usage_info = getattr(client, "usage_info", None) or {}
        quotas = getattr(client, "quotas", None) or {}
        abuse_status = getattr(client, "abuse_status", None)
        status = getattr(client, "account_status", None)
        updates: dict[str, Any] = {
            "cookies": cookies,
            "account_status": getattr(status, "name", "AVAILABLE"),
            "account_status_code": int(status) if status is not None else None,
            "usage_info": usage_info,
            "quotas": quotas,
            "abuse_status": abuse_status,
            "available_models": [model.model_dump() if hasattr(model, "model_dump") else dict(model) for model in models],
        }
        if cookies.get("__Secure-1PSID"):
            updates["__Secure-1PSID"] = cookies["__Secure-1PSID"]
        if cookies.get("__Secure-1PSIDTS"):
            updates["__Secure-1PSIDTS"] = cookies["__Secure-1PSIDTS"]
        if getattr(client, "access_token", None):
            updates["session_token"] = client.access_token
            updates["SNlM0e"] = client.access_token
            updates["at"] = client.access_token
        return updates
    finally:
        await client.close()


def validate_account(account: dict[str, Any]) -> dict[str, Any]:
    try:
        return run_async(_validate_account(account))
    except Exception as exc:
        raise _exception_to_error(exc) from exc


def _image_extension_from_mime(mime_type: str) -> str:
    subtype = mime_type.split("/", 1)[1].split(";", 1)[0].lower() if "/" in mime_type else "png"
    return "jpg" if subtype == "jpeg" else subtype or "png"


def _materialize_base64_file(temp_dir: Path, value: str, index: int) -> str | None:
    text = value.strip()
    mime_type = "image/png"
    if text.startswith("data:"):
        header, separator, payload = text.partition(",")
        if not separator:
            return None
        mime_type = header.split(";", 1)[0].removeprefix("data:") or mime_type
        text = payload
    try:
        data = base64.b64decode(text, validate=True)
    except Exception:
        return None
    if not data:
        return None
    path = temp_dir / f"image_{index}.{_image_extension_from_mime(mime_type)}"
    path.write_bytes(data)
    return str(path)


def _materialize_files(temp_dir: Path, files: list[object] | None) -> list[object] | None:
    if not files:
        return None
    materialized: list[object] = []
    for index, file in enumerate(files, start=1):
        if hasattr(file, "getvalue"):
            filename = Path(str(getattr(file, "name", "") or f"image_{index}.png")).name
            path = temp_dir / filename
            path.write_bytes(file.getvalue())
            materialized.append(str(path))
            continue
        if isinstance(file, str):
            image_path = _materialize_base64_file(temp_dir, file, index)
            if image_path is not None:
                materialized.append(image_path)
                continue
            try:
                if Path(file).exists():
                    materialized.append(file)
                    continue
            except OSError:
                pass
        materialized.append(file)
    return materialized


async def _chat_completion(account: dict[str, Any], spec: ModelSpec, prompt: str, files: list[object] | None = None) -> tuple[GeminiApiCompletion, dict[str, Any]]:
    client = await _open_client(account, auto_refresh=False, init_rpc=False)
    try:
        with tempfile.TemporaryDirectory(prefix="gemini-input-") as temp_dir:
            upload_files = _materialize_files(Path(temp_dir), files)
            output = await client.generate_content(prompt, files=upload_files, model=_model_name(spec, has_files=bool(upload_files)))
        return GeminiApiCompletion(content=_safe_text(getattr(output, "text", "")), raw_response=output, metadata=_metadata_from_output(output)), {}
    finally:
        await client.close()


async def _validate_account_from_open_client(client: object) -> dict[str, Any]:
    cookies = _cookies_from_client(client)
    status = getattr(client, "account_status", None)
    updates: dict[str, Any] = {
        "cookies": cookies,
        "account_status": getattr(status, "name", "AVAILABLE"),
        "account_status_code": int(status) if status is not None else None,
        "usage_info": getattr(client, "usage_info", None) or {},
        "quotas": getattr(client, "quotas", None) or {},
        "abuse_status": getattr(client, "abuse_status", None),
    }
    if cookies.get("__Secure-1PSID"):
        updates["__Secure-1PSID"] = cookies["__Secure-1PSID"]
    if cookies.get("__Secure-1PSIDTS"):
        updates["__Secure-1PSIDTS"] = cookies["__Secure-1PSIDTS"]
    if getattr(client, "access_token", None):
        updates["session_token"] = client.access_token
        updates["SNlM0e"] = client.access_token
        updates["at"] = client.access_token
    return updates


def chat_completion(account: dict[str, Any], spec: ModelSpec, prompt: str, files: list[object] | None = None) -> tuple[GeminiApiCompletion, dict[str, Any]]:
    try:
        return run_async(_chat_completion(account, spec, prompt, files))
    except Exception as exc:
        raise _exception_to_error(exc) from exc


async def _stream_completion(account: dict[str, Any], spec: ModelSpec, prompt: str, files: list[object] | None = None) -> tuple[list[str], dict[str, Any]]:
    client = await _open_client(account, auto_refresh=False, init_rpc=False)
    chunks: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="gemini-input-") as temp_dir:
            upload_files = _materialize_files(Path(temp_dir), files)
            async for output in client.generate_content_stream(prompt, files=upload_files, model=_model_name(spec, has_files=bool(upload_files))):
                delta = _safe_text(getattr(output, "text_delta", ""))
                if delta:
                    chunks.append(delta)
        return chunks, {}
    finally:
        await client.close()


def stream_completion(account: dict[str, Any], spec: ModelSpec, prompt: str, files: list[object] | None = None) -> tuple[list[str], dict[str, Any]]:
    try:
        return run_async(_stream_completion(account, spec, prompt, files))
    except Exception as exc:
        raise _exception_to_error(exc) from exc


async def _save_generated_image(image: object, temp_dir: Path) -> str:
    path = await image.save(path=str(temp_dir), filename="gemini-image", verbose=False, full_size=True)
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


async def _collect_generated_images(output: object, prompt: str, n: int) -> list[GeminiImageResult]:
    images = list(getattr(output, "images", None) or [])
    if not images:
        message = _safe_text(getattr(output, "text", ""))
        raise GeminiApiError(message or "Gemini did not return a generated image", status_code=502, code="gemini_image_generation_failed")
    with tempfile.TemporaryDirectory(prefix="gemini-image-") as temp_dir:
        temp_path = Path(temp_dir)
        return [
            GeminiImageResult(b64_json=await _save_generated_image(image, temp_path), revised_prompt=prompt)
            for image in images[:max(1, int(n or 1))]
        ]


async def _generate_images(
    account: dict[str, Any],
    spec: ModelSpec,
    prompt: str,
    n: int = 1,
    size: str | None = None,
) -> tuple[list[GeminiImageResult], dict[str, Any]]:
    client = await _open_client(account, auto_refresh=True)
    try:
        output = await client.generate_content(_image_prompt(prompt, size), model=_model_name(spec))
        results = await _collect_generated_images(output, prompt, n)
        updates = await _validate_account_from_open_client(client)
        return results, updates
    finally:
        await client.close()


async def _edit_images(
    account: dict[str, Any],
    spec: ModelSpec,
    prompt: str,
    files: list[object],
    n: int = 1,
    size: str | None = None,
) -> tuple[list[GeminiImageResult], dict[str, Any]]:
    client = await _open_client(account, auto_refresh=True)
    try:
        with tempfile.TemporaryDirectory(prefix="gemini-edit-") as temp_dir:
            upload_files = _materialize_files(Path(temp_dir), files)
            output = await client.generate_content(
                _image_edit_prompt(prompt, size),
                files=upload_files,
                model=_model_name(spec, has_files=bool(upload_files)),
            )
        results = await _collect_generated_images(output, prompt, n)
        updates = await _validate_account_from_open_client(client)
        return results, updates
    finally:
        await client.close()


def generate_images(
    account: dict[str, Any],
    spec: ModelSpec,
    prompt: str,
    n: int = 1,
    size: str | None = None,
) -> tuple[list[GeminiImageResult], dict[str, Any]]:
    try:
        return run_async(_generate_images(account, spec, prompt, n, size))
    except GeminiApiError:
        raise
    except Exception as exc:
        raise _exception_to_error(exc) from exc


def edit_images(
    account: dict[str, Any],
    spec: ModelSpec,
    prompt: str,
    files: list[object],
    n: int = 1,
    size: str | None = None,
) -> tuple[list[GeminiImageResult], dict[str, Any]]:
    try:
        return run_async(_edit_images(account, spec, prompt, files, n, size))
    except GeminiApiError:
        raise
    except Exception as exc:
        raise _exception_to_error(exc) from exc


def raise_http_error(exc: GeminiApiError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_http_detail())
