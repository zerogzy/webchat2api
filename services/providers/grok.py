from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from curl_cffi import requests
from fastapi import HTTPException

from services.config import config
from services.models import GROK_PROVIDER, ModelSpec, is_supported_grok_app_chat_image_model
from services.network.client import create_session
from services.network.flaresolverr import FlareSolverrClearanceProvider
from services.network.headers import build_grok_console_headers
from services.network.profiles import build_grok_app_chat_profile, build_grok_console_profile, infer_chromium_impersonate
from services.protocol.conversation import ImageGenerationError, ImageOutput, format_image_result
from utils.log import logger

CONSOLE_BASE_URL = "https://console.x.ai"
CONSOLE_RESPONSES_URL = f"{CONSOLE_BASE_URL}/v1/responses"
APP_CHAT_BASE_URL = "https://grok.com"
APP_CHAT_NEW_CONVERSATION_URL = f"{APP_CHAT_BASE_URL}/rest/app-chat/conversations/new"
GROK_ASSET_BASE_URL = "https://assets.grok.com/"
GROK_APP_CHAT_STATSIG_ID = "0196a8f6-0501-79f8-8d74-a2f2c0f5f5f5"
_APP_CHAT_CLEARANCE_LOCK = threading.Lock()

_BRIDGE_DEFAULT_URL = "http://127.0.0.1:3080"
_bridge_detected_url: str | None = None
_bridge_probed = False


class GrokConsoleError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_status = upstream_status


@dataclass(frozen=True)
class GrokConsoleCompletion:
    content: str
    reasoning_content: str = ""
    raw_reasoning: object = None
    raw_usage: object = None
    raw_response: dict[str, Any] | None = None


_THINKING_SUMMARY_RE = re.compile(
    r"^\s*(?:\*\*)?\s*(?:思考摘要|思考总结|thinking\s+summary|thought\s+summary|reasoning\s+summary|thinking|reasoning)\s*(?:\*\*\s*[:：]|[:：]\s*(?:\*\*)?)\s*(.*)$",
    re.IGNORECASE,
)
_ANSWER_SUMMARY_RE = re.compile(
    r"^\s*(?:\*\*)?\s*(?:答案|回答|answer|final\s+answer|response)\s*(?:\*\*\s*[:：]|[:：]\s*(?:\*\*)?)\s*(.*)$",
    re.IGNORECASE,
)
_CONSOLE_SEARCH_TOOL_TYPES = {"web_search", "x_search"}


def split_visible_console_reasoning(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    lines = text.splitlines(keepends=True)
    first_content_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_content_index is None:
        return text, ""
    thinking_match = _THINKING_SUMMARY_RE.match(lines[first_content_index].rstrip("\r\n"))
    if not thinking_match:
        return text, ""
    answer_index = -1
    answer_match: re.Match[str] | None = None
    for index in range(first_content_index + 1, len(lines)):
        candidate = _ANSWER_SUMMARY_RE.match(lines[index].rstrip("\r\n"))
        if candidate:
            answer_index = index
            answer_match = candidate
            break
    if answer_index < 0 or answer_match is None:
        return text, ""

    reasoning_parts: list[str] = []
    if thinking_match.group(1):
        reasoning_parts.append(thinking_match.group(1))
        if lines[first_content_index].endswith(("\n", "\r")) and first_content_index + 1 < answer_index:
            reasoning_parts.append("\n")
    reasoning_parts.extend(lines[first_content_index + 1:answer_index])
    content_parts = lines[:first_content_index]
    if answer_match.group(1):
        content_parts.append(answer_match.group(1))
        if lines[answer_index].endswith(("\n", "\r")):
            content_parts.append("\n")
    content_parts.extend(lines[answer_index + 1:])
    return "".join(content_parts).strip(), "".join(reasoning_parts).strip()


def _message_text(content: object, role: str) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip()
        if block_type in {"text", "input_text", "output_text"}:
            text = block.get("text") or block.get("input_text") or block.get("output_text")
            if text:
                parts.append(str(text))
    return "\n".join(part for part in parts if part)


def build_console_input(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user").strip().lower()
        text = _message_text(message.get("content"), role).strip()
        if not text:
            continue
        if role == "system":
            instructions.append(text)
            continue
        if role not in {"user", "assistant"}:
            continue
        content_type = "output_text" if role == "assistant" else "input_text"
        input_items.append({
            "role": role,
            "content": [{"type": content_type, "text": text}],
        })
    return "\n\n".join(instructions).strip(), input_items


def _console_search_tools(tools: object) -> list[dict[str, Any]]:
    search_tools: list[dict[str, Any]] = []
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if str(tool.get("type") or "") in _CONSOLE_SEARCH_TOOL_TYPES:
                search_tools.append(dict(tool))
    if not any(tool.get("type") == "web_search" for tool in search_tools):
        search_tools.append({"type": "web_search"})
    return search_tools


def build_console_payload(spec: ModelSpec, body: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    instructions, input_items = build_console_input(messages)
    if not input_items:
        raise HTTPException(status_code=400, detail={"error": "Grok chat requires at least one user or assistant text message"})
    payload: dict[str, Any] = {
        "model": spec.upstream_model or spec.id,
        "input": input_items,
        "tools": _console_search_tools(body.get("tools")),
    }
    request_instructions = str(body.get("instructions") or "").strip()
    merged_instructions = "\n\n".join(item for item in [request_instructions, instructions] if item)
    if merged_instructions:
        payload["instructions"] = merged_instructions
    for key in ("temperature", "top_p", "max_output_tokens", "max_tokens"):
        if body.get(key) is not None:
            target_key = "max_output_tokens" if key == "max_tokens" else key
            payload[target_key] = body[key]
    reasoning_effort = str(body.get("reasoning_effort") or spec.default_reasoning_effort or "").strip().lower()
    if reasoning_effort and reasoning_effort != "none":
        payload["reasoning"] = {"effort": "high" if reasoning_effort == "xhigh" else reasoning_effort}
    return payload


def _extract_text_from_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        text = block.get("text") or block.get("output_text") or block.get("input_text")
        if text and str(block.get("type") or "") in {"", "text", "output_text", "input_text"}:
            parts.append(str(text))
    return "".join(parts)


def extract_console_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct:
        return direct
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type in {"message", "output_message", ""}:
            text = _extract_text_from_content(item.get("content"))
            if text:
                parts.append(text)
        elif item_type in {"output_text", "text"} and item.get("text"):
            parts.append(str(item.get("text")))
    return "".join(parts)


def extract_console_completion(payload: dict[str, Any]) -> GrokConsoleCompletion:
    text = extract_console_text(payload)
    content, reasoning_content = split_visible_console_reasoning(text)
    return GrokConsoleCompletion(
        content=content,
        reasoning_content=reasoning_content,
        raw_reasoning=payload.get("reasoning"),
        raw_usage=payload.get("usage"),
        raw_response=payload,
    )


def _grok_console_profile():
    return build_grok_console_profile(config.data)


def _account_text(account: dict[str, Any] | None, *keys: str) -> str:
    if not isinstance(account, dict):
        return ""
    for key in keys:
        value = account.get(key)
        if isinstance(value, dict):
            continue
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _app_chat_profile_value(profile: object, account: dict[str, Any] | None, field: str, *account_keys: str) -> str:
    return _account_text(account, *account_keys) or str(getattr(profile, field, "") or "")


def _app_chat_impersonate(profile: object, account: dict[str, Any] | None) -> str:
    return _account_text(account, "impersonate", "browser") or str(getattr(profile, "impersonate", "") or "")


def _grok_app_chat_profile():
    return build_grok_app_chat_profile(config.data)


def _headers(access_token: str) -> dict[str, str]:
    return build_grok_console_headers(_grok_console_profile(), access_token=access_token, base_url=CONSOLE_BASE_URL)


def _openai_status(upstream_status: int) -> int:
    if upstream_status in {401, 403, 404}:
        return upstream_status
    if upstream_status in {402, 429}:
        return 429
    if 400 <= upstream_status < 500:
        return 400
    return 502


def _feedback_status(upstream_status: int) -> str | None:
    if upstream_status in {401, 403}:
        return "异常"
    if upstream_status in {402, 429}:
        return "限流"
    return None


class GrokConsoleClient:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self.network_profile = _grok_console_profile()
        self.session = create_session(impersonate=self.network_profile.impersonate, verify=self.network_profile.verify)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "GrokConsoleClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _call_with_retry(self, fn, policy=None, context=""):
        """Wrap a callable with retry handling."""
        from services.network.retry import retry_call, RetryPolicy

        api_policy = policy or RetryPolicy(
            max_attempts=3,
            retry_statuses=frozenset({408, 429, 500, 502, 503, 504}),
        )
        deadline = time.monotonic() + 60.0

        def on_retry(attempt, status_code, exc):
            logger.warning({
                "event": "grok_retry",
                "context": context,
                "attempt": attempt,
                "status_code": status_code,
                "error": str(exc) if exc else None,
            })

        return retry_call(fn, policy=api_policy, deadline=deadline, on_retry=on_retry)

    def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._call_with_retry(
                lambda: self.session.post(
                    CONSOLE_RESPONSES_URL,
                    headers=_headers(self.access_token),
                    json=payload,
                    timeout=self.network_profile.timeout,
                ),
                context="create_response",
            )
        except requests.exceptions.RequestException as exc:
            raise GrokConsoleError(f"Grok upstream request failed: {exc}", 502) from exc
        if response.status_code >= 400:
            status = int(response.status_code)
            feedback_status = _feedback_status(status)
            if feedback_status:
                from services.account_service import account_service

                account_service.update_account(self.access_token, {"status": feedback_status})
            message = f"Grok upstream error (HTTP {status})"
            raise GrokConsoleError(message, _openai_status(status), status)
        data = response.json()
        if not isinstance(data, dict):
            raise GrokConsoleError("Grok upstream returned an invalid response", 502)
        return data


def _cookie_items(cookie_header: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for fragment in str(cookie_header or "").split(";"):
        name, separator, value = fragment.strip().partition("=")
        clean_name = " ".join(name.strip().split())
        clean_value = " ".join(value.strip().split())
        if separator and clean_name:
            items.append((clean_name, clean_value))
    return items


def _set_cookie(cookies: list[tuple[str, str]], name: str, value: str) -> None:
    for index, (existing_name, _) in enumerate(cookies):
        if existing_name == name:
            cookies[index] = (name, value)
            return
    cookies.append((name, value))


def _app_chat_cookie(access_token: str, cf_clearance: str = "", cf_cookies: str = "") -> str:
    token = str(access_token or "").strip()
    cookies: list[tuple[str, str]] = []
    sso_value = ""
    for name, value in _cookie_items(token):
        if name == "sso":
            sso_value = value
            break
    if not sso_value and token and "=" not in token:
        sso_value = " ".join(token.split())
    if sso_value:
        cookies.extend((name, value) for name, value in _cookie_items(token) if name not in {"sso", "sso-rw"})
        cookies.insert(0, ("sso-rw", sso_value))
        cookies.insert(0, ("sso", sso_value))
    else:
        cookies.extend(_cookie_items(token))
    for name, value in _cookie_items(cf_cookies):
        if name not in {"sso", "sso-rw"}:
            _set_cookie(cookies, name, value)
    clearance = " ".join(str(cf_clearance or "").strip().split())
    if clearance:
        _set_cookie(cookies, "cf_clearance", clearance)
    return "; ".join(f"{name}={value}" for name, value in cookies)


def _extract_raw_sso(access_token: str) -> str:
    token = str(access_token or "").strip()
    for name, value in _cookie_items(token):
        if name == "sso":
            return value
    if token and "=" not in token:
        return " ".join(token.split())
    return token


def _detect_bridge_url() -> str:
    configured = config.browser_bridge_url
    if configured:
        return configured
    global _bridge_detected_url, _bridge_probed
    if _bridge_probed:
        return _bridge_detected_url or ""
    _bridge_probed = True
    import urllib.request
    try:
        with urllib.request.urlopen(f"{_BRIDGE_DEFAULT_URL}/health", timeout=2) as r:
            if r.status == 200:
                _bridge_detected_url = _BRIDGE_DEFAULT_URL
                logger.info({"event": "browser_bridge_detected", "url": _BRIDGE_DEFAULT_URL})
                return _BRIDGE_DEFAULT_URL
    except Exception:
        pass
    _bridge_detected_url = ""
    return ""


def app_chat_headers(access_token: str, account: dict[str, Any] | None = None) -> dict[str, str]:
    profile = _grok_app_chat_profile()
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
        "Content-Type": "application/json",
        "Origin": APP_CHAT_BASE_URL,
        "Referer": f"{APP_CHAT_BASE_URL}/",
        "Priority": "u=1, i",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": _app_chat_profile_value(profile, account, "user_agent", "user_agent", "user-agent"),
        "x-statsig-id": _app_chat_profile_value(profile, account, "statsig_id", "statsig_id", "x-statsig-id"),
        "x-xai-request-id": str(uuid.uuid4()),
    }
    sec_ch_ua = _app_chat_profile_value(profile, account, "sec_ch_ua", "sec_ch_ua", "sec-ch-ua")
    sec_ch_ua_mobile = _app_chat_profile_value(profile, account, "sec_ch_ua_mobile", "sec_ch_ua_mobile", "sec-ch-ua-mobile")
    sec_ch_ua_platform = _app_chat_profile_value(profile, account, "sec_ch_ua_platform", "sec_ch_ua_platform", "sec-ch-ua-platform")
    if sec_ch_ua:
        headers["Sec-Ch-Ua"] = sec_ch_ua
    if sec_ch_ua_mobile:
        headers["Sec-Ch-Ua-Mobile"] = sec_ch_ua_mobile
    if sec_ch_ua_platform:
        headers["Sec-Ch-Ua-Platform"] = sec_ch_ua_platform
    cf_clearance = _app_chat_profile_value(profile, account, "cf_clearance", "cf_clearance")
    cf_cookies = _app_chat_profile_value(profile, account, "cf_cookies", "cf_cookies")
    cookie = _app_chat_cookie(access_token, cf_clearance, cf_cookies)
    if cookie:
        headers["Cookie"] = cookie
    return headers


def latest_user_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if str(message.get("role") or "user").strip().lower() != "user":
            continue
        text = _message_text(message.get("content"), "user").strip()
        if text:
            return text
    return ""


def build_app_chat_payload(
    spec: ModelSpec,
    body: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    image_generation: bool = False,
) -> dict[str, Any]:
    message = str(body.get("prompt") or "").strip() or latest_user_message(messages)
    if not message:
        raise HTTPException(status_code=400, detail={"error": "Grok app-chat requires a user text message"})
    payload: dict[str, Any] = {
        "collectionIds": [],
        "connectors": [],
        "deviceEnvInfo": {
            "darkModeEnabled": False,
            "devicePixelRatio": 2,
            "screenHeight": 1329,
            "screenWidth": 2056,
            "viewportHeight": 1083,
            "viewportWidth": 2056,
        },
        "disableMemory": True,
        "disableSearch": False,
        "disableSelfHarmShortCircuit": False,
        "disableTextFollowUps": False,
        "enableImageGeneration": image_generation,
        "enableImageStreaming": image_generation,
        "enableSideBySide": True,
        "fileAttachments": [],
        "forceConcise": False,
        "forceSideBySide": False,
        "imageAttachments": [],
        "imageGenerationCount": int(body.get("n") or 1) if image_generation else 0,
        "isAsyncChat": False,
        "message": message,
        "modeId": spec.mode_id or spec.upstream_model or spec.id,
        "responseMetadata": {},
        "returnImageBytes": False,
        "returnRawGrokInXaiRequest": False,
        "searchAllConnectors": False,
        "sendFinalMetadata": True,
        "temporary": True,
        "toolOverrides": {
            "imageGen": False,
            "webSearch": False,
            "xSearch": False,
            "xMediaSearch": False,
            "trendsSearch": False,
            "xPostAnalyze": False,
        },
    }
    if spec.model_tier:
        payload["modelTier"] = spec.model_tier
    if spec.prefer_best:
        payload["preferBest"] = True
    return payload


def parse_app_chat_payload_line(line: str | bytes) -> dict[str, Any] | None:
    if isinstance(line, bytes):
        payload = line.decode("utf-8", errors="replace").strip()
    else:
        payload = str(line or "").strip()
    if not payload or payload == "[DONE]":
        return None
    if payload.startswith("data:"):
        payload = payload[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def app_chat_line_events(lines: Iterable[str | bytes]) -> Iterator[dict[str, Any]]:
    for line in lines:
        event = parse_app_chat_payload_line(line)
        if event is not None:
            yield event


def extract_app_chat_token(event: dict[str, Any]) -> tuple[str, bool]:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    token = response.get("token")
    if not token:
        return "", False
    return str(token), response.get("isThinking") is True


def is_app_chat_final_event(event: dict[str, Any]) -> bool:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    if response.get("isSoftStop") is True:
        return True
    if "finalMetadata" in response or "finalMetadata" in result:
        return True
    return False


def _app_chat_json_data(attachment: dict[str, Any]) -> dict[str, Any]:
    raw_json_data = attachment.get("jsonData")
    if isinstance(raw_json_data, dict):
        return raw_json_data
    if isinstance(raw_json_data, str):
        try:
            decoded = json.loads(raw_json_data)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def extract_app_chat_image_url(event: dict[str, Any]) -> str:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    attachment = response.get("cardAttachment") if isinstance(response.get("cardAttachment"), dict) else {}
    json_data = _app_chat_json_data(attachment)
    chunk = json_data.get("image_chunk") if isinstance(json_data.get("image_chunk"), dict) else {}
    progress = chunk.get("progress")
    moderated = chunk.get("moderated") is True or chunk.get("isModerated") is True
    image_url = str(chunk.get("imageUrl") or "").strip()
    if progress != 100 or moderated or not image_url:
        return ""
    if image_url.startswith(("http://", "https://")):
        return image_url
    return GROK_ASSET_BASE_URL + image_url.lstrip("/")


def collect_app_chat_response(events: Iterable[dict[str, Any]]) -> dict[str, str]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    for event in events:
        token, thinking = extract_app_chat_token(event)
        if token:
            if thinking:
                reasoning_parts.append(token)
            else:
                content_parts.append(token)
        if is_app_chat_final_event(event):
            break
    return {"content": "".join(content_parts), "reasoning_content": "".join(reasoning_parts)}


def classify_app_chat_upstream_error(upstream_status: int, access_token: str | None = None) -> GrokConsoleError:
    status = int(upstream_status)
    feedback_status = _feedback_status(status)
    if access_token and feedback_status:
        from services.account_service import account_service

        account_service.update_account(access_token, {"status": feedback_status})
    if status == 401:
        return GrokConsoleError("Grok app-chat authentication failed (HTTP 401)", 401, status)
    if status == 403:
        return GrokConsoleError(
            "Grok app-chat forbidden (HTTP 403)",
            403,
            status,
        )
    if status == 429:
        return GrokConsoleError("Grok app-chat rate limited (HTTP 429)", 429, status)
    if status in {408, 504}:
        return GrokConsoleError(f"Grok app-chat upstream timeout (HTTP {status})", _openai_status(status), status)
    return GrokConsoleError(f"Grok app-chat upstream error (HTTP {status})", _openai_status(status), status)


class GrokAppChatClient:
    def __init__(self, access_token: str, account: dict[str, Any] | None = None) -> None:
        self.access_token = access_token
        self.account = account if isinstance(account, dict) else None
        self.network_profile = _grok_app_chat_profile()
        impersonate = _app_chat_impersonate(self.network_profile, self.account)
        self.session = create_session(impersonate=impersonate, verify=self.network_profile.verify)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "GrokAppChatClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _call_with_retry(self, fn, policy=None, context=""):
        from services.network.retry import retry_call, RetryPolicy

        api_policy = policy or RetryPolicy(
            max_attempts=3,
            retry_statuses=frozenset({408, 429, 500, 502, 503, 504}),
        )
        deadline = time.monotonic() + 60.0

        def on_retry(attempt, status_code, exc):
            logger.warning({
                "event": "grok_app_chat_retry",
                "context": context,
                "attempt": attempt,
                "status_code": status_code,
                "error": str(exc) if exc else None,
            })

        return retry_call(fn, policy=api_policy, deadline=deadline, on_retry=on_retry)

    def _refresh_clearance(self) -> bool:
        if not config.flaresolverr_url:
            return False
        with _APP_CHAT_CLEARANCE_LOCK:
            clearance = FlareSolverrClearanceProvider().solve()
            if clearance is None:
                return False
            current_profiles = config.network_profiles
            app_profile = dict(current_profiles.get("grok_app_chat") or {})
            solved_impersonate = infer_chromium_impersonate(clearance.user_agent)
            app_profile.update({
                "browser": solved_impersonate or app_profile.get("browser") or app_profile.get("impersonate"),
                "cf_cookies": clearance.cf_cookies,
                "cf_clearance": clearance.cf_clearance,
                "impersonate": solved_impersonate or app_profile.get("impersonate") or app_profile.get("browser"),
                "user-agent": clearance.user_agent,
            })
            next_profiles = dict(current_profiles)
            next_profiles["grok_app_chat"] = app_profile
            config.update({"network_profiles": next_profiles})
            self.network_profile = _grok_app_chat_profile()
            headers = app_chat_headers(self.access_token, self.account)
            session_headers = getattr(self.session, "headers", None)
            if hasattr(session_headers, "update"):
                session_headers.update(headers)
            return True

    def _try_browser_bridge(self, payload: dict[str, Any]) -> list[str] | None:
        bridge_url = _detect_bridge_url()
        if not bridge_url:
            return None
        sso = _extract_raw_sso(self.access_token)
        if not sso:
            return None
        import urllib.request
        import urllib.error
        bridge_body = json.dumps({"sso": sso, "payload": payload}).encode()
        req = urllib.request.Request(
            f"{bridge_url}/api/chat",
            data=bridge_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                if resp.status != 200:
                    logger.warning({"event": "browser_bridge_error", "status": resp.status})
                    return None
                return resp.read().decode("utf-8", errors="replace").splitlines()
        except urllib.error.HTTPError as exc:
            msg = f"Grok app-chat forbidden (HTTP {exc.code}): account may lack required tier" if exc.code == 403 else f"Grok app-chat via bridge failed (HTTP {exc.code})"
            raise GrokConsoleError(msg, _openai_status(exc.code), exc.code)
        except (urllib.error.URLError, OSError, TimeoutError):
            global _bridge_probed
            _bridge_probed = False
            logger.warning({"event": "browser_bridge_unavailable"})
            return None

    def stream_events(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        bridge_lines = self._try_browser_bridge(payload)
        if bridge_lines is not None:
            yield from app_chat_line_events(bridge_lines)
            return
        response = None
        try:
            response = self._call_with_retry(
                lambda: self.session.post(
                    APP_CHAT_NEW_CONVERSATION_URL,
                    headers=app_chat_headers(self.access_token, self.account),
                    json=payload,
                    timeout=self.network_profile.timeout,
                    stream=True,
                ),
                context="app_chat",
            )
        except requests.exceptions.RequestException as exc:
            raise GrokConsoleError(f"Grok app-chat upstream request failed: {exc}", 502) from exc
        if response.status_code == 403 and self._refresh_clearance():
            try:
                response = self._call_with_retry(
                    lambda: self.session.post(
                        APP_CHAT_NEW_CONVERSATION_URL,
                        headers=app_chat_headers(self.access_token, self.account),
                        json=payload,
                        timeout=self.network_profile.timeout,
                        stream=True,
                    ),
                    context="app_chat_flaresolverr",
                )
            except requests.exceptions.RequestException as exc:
                raise GrokConsoleError(f"Grok app-chat upstream request failed: {exc}", 502) from exc
        if response.status_code >= 400:
            raise classify_app_chat_upstream_error(int(response.status_code), self.access_token)
        yield from app_chat_line_events(response.iter_lines())


def app_chat_completion_events(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    from services.account_service import account_service

    access_token = account_service.get_grok_app_chat_access_token(spec)
    if not access_token:
        raise HTTPException(status_code=503, detail={"error": "no available Grok account"})
    account = account_service.get_account(access_token)
    payload = build_app_chat_payload(spec, body, messages)
    try:
        with GrokAppChatClient(access_token, account) as client:
            yield from client.stream_events(payload)
    except GrokConsoleError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error": str(exc)}) from exc
    account_service.mark_text_used(access_token)


def app_chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> dict[str, str]:
    return collect_app_chat_response(app_chat_completion_events(body, spec, messages))


def app_chat_image_outputs(body: dict[str, Any], spec: ModelSpec, prompt: str, n: int = 1) -> Iterator[ImageOutput]:
    if not is_supported_grok_app_chat_image_model(spec.id):
        if spec.capability == "video":
            detail = f"Grok video generation is not yet supported: {spec.id}"
        elif spec.capability == "image_edit":
            detail = f"Grok image editing is not yet supported: {spec.id}"
        else:
            detail = f"unsupported Grok image model: {spec.id}"
        raise ImageGenerationError(
            detail,
            status_code=400,
            error_type="invalid_request_error",
            code="unsupported_model",
            param="model",
        )
    request_body = dict(body)
    request_body["prompt"] = prompt
    request_body["n"] = n
    payload = build_app_chat_payload(spec, request_body, [{"role": "user", "content": prompt}], image_generation=True)

    from services.account_service import account_service

    access_token = account_service.get_grok_app_chat_access_token(spec)
    if not access_token:
        raise ImageGenerationError("no available Grok account", status_code=503, code="no_available_account")
    account = account_service.get_account(access_token)
    image_items: list[dict[str, Any]] = []
    try:
        with GrokAppChatClient(access_token, account) as client:
            for event in client.stream_events(payload):
                token, thinking = extract_app_chat_token(event)
                if token and not thinking:
                    yield ImageOutput(kind="progress", model=spec.id, index=1, total=n, text=token, upstream_event_type="app_chat.token")
                image_url = extract_app_chat_image_url(event)
                if image_url:
                    image_items.append({"url": image_url, "revised_prompt": prompt})
                if is_app_chat_final_event(event):
                    break
    except GrokConsoleError as exc:
        raise ImageGenerationError(str(exc), status_code=exc.status_code) from exc
    account_service.mark_text_used(access_token)
    if image_items:
        yield ImageOutput(kind="result", model=spec.id, index=1, total=n, data=format_image_result(image_items, prompt, "url")["data"])
        return
    raise ImageGenerationError("Grok image generation did not return an image", status_code=502, code="image_generation_failed")


def console_chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> GrokConsoleCompletion:
    from services.account_service import account_service

    access_token = account_service.get_text_access_token(provider=GROK_PROVIDER)
    if not access_token:
        raise HTTPException(status_code=503, detail={"error": "no available Grok account"})
    payload = build_console_payload(spec, body, messages)
    try:
        with GrokConsoleClient(access_token) as client:
            response_json = client.create_response(payload)
    except GrokConsoleError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error": str(exc)}) from exc
    account_service.mark_text_used(access_token)
    completion = extract_console_completion(response_json)
    if not completion.content and not completion.reasoning_content:
        raise HTTPException(status_code=502, detail={"error": "Grok upstream response did not contain text"})
    return completion


def chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> str:
    return console_chat_completion(body, spec, messages).content
