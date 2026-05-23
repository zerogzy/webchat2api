from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

if "curl_cffi" not in sys.modules:
    curl_cffi = types.ModuleType("curl_cffi")
    requests_module = types.SimpleNamespace(
        Session=object,
        Response=object,
        exceptions=types.SimpleNamespace(RequestException=Exception),
    )
    curl_cffi.requests = requests_module
    sys.modules["curl_cffi"] = curl_cffi
    sys.modules["curl_cffi.requests"] = requests_module

if "pybase64" not in sys.modules:
    pybase64 = types.ModuleType("pybase64")
    pybase64.b64encode = lambda value: value
    pybase64.b64decode = lambda value: value
    sys.modules["pybase64"] = pybase64

if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: object = None) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi.HTTPException = HTTPException
    concurrency = types.ModuleType("fastapi.concurrency")
    concurrency.run_in_threadpool = lambda func, *args, **kwargs: func(*args, **kwargs)
    responses = types.ModuleType("fastapi.responses")

    class JSONResponse:
        pass

    class StreamingResponse:
        pass

    responses.JSONResponse = JSONResponse
    responses.StreamingResponse = StreamingResponse
    fastapi.concurrency = concurrency
    fastapi.responses = responses
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.concurrency"] = concurrency
    sys.modules["fastapi.responses"] = responses

if "PIL" not in sys.modules:
    pil = types.ModuleType("PIL")
    pil.Image = object
    sys.modules["PIL"] = pil

from services.network.client import build_session_kwargs
from services.network.headers import build_chatgpt_web_headers, build_grok_console_headers
from services.network.profiles import build_chatgpt_web_profile, build_grok_console_profile


class NetworkProfileTests(unittest.TestCase):
    def test_chatgpt_profile_reuses_existing_fingerprint_keys(self) -> None:
        profile = build_chatgpt_web_profile({
            "fp": {"user-agent": "Nested UA", "impersonate": "nested-browser"},
            "user-agent": "Top UA",
            "impersonate": "top-browser",
            "oai-device-id": "device-id",
            "oai-session-id": "session-id",
            "sec-ch-ua": "sec ua",
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": "platform",
        })

        self.assertEqual(profile.as_fingerprint(), {
            "user-agent": "Top UA",
            "impersonate": "top-browser",
            "oai-device-id": "device-id",
            "oai-session-id": "session-id",
            "sec-ch-ua": "sec ua",
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": "platform",
        })
        headers = build_chatgpt_web_headers(
            profile,
            base_url="https://chatgpt.com",
            client_version="client-version",
            client_build_number="build-number",
        )
        self.assertEqual(headers["User-Agent"], "Top UA")
        self.assertEqual(headers["OAI-Device-Id"], "device-id")
        self.assertEqual(headers["OAI-Session-Id"], "session-id")


    def test_chatgpt_profile_uses_global_fingerprint_without_account_override(self) -> None:
        profile = build_chatgpt_web_profile({}, {
            "user-agent": "Global UA",
            "impersonate": "global-browser",
            "oai-device-id": "global-device",
            "oai-session-id": "global-session",
            "sec-ch-ua": "global sec ua",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "global platform",
        })

        self.assertEqual(profile.as_fingerprint(), {
            "user-agent": "Global UA",
            "impersonate": "global-browser",
            "oai-device-id": "global-device",
            "oai-session-id": "global-session",
            "sec-ch-ua": "global sec ua",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "global platform",
        })

    def test_chatgpt_profile_account_values_override_global_fingerprint(self) -> None:
        profile = build_chatgpt_web_profile(
            {
                "fp": {
                    "user-agent": "Account Nested UA",
                    "impersonate": "account-nested-browser",
                    "oai-device-id": "account-nested-device",
                },
                "user-agent": "Account Top UA",
                "sec-ch-ua-platform": "account top platform",
            },
            {
                "user-agent": "Global UA",
                "impersonate": "global-browser",
                "oai-device-id": "global-device",
                "oai-session-id": "global-session",
                "sec-ch-ua": "global sec ua",
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": "global platform",
            },
        )

        self.assertEqual(profile.user_agent, "Account Top UA")
        self.assertEqual(profile.impersonate, "account-nested-browser")
        self.assertEqual(profile.oai_device_id, "account-nested-device")
        self.assertEqual(profile.oai_session_id, "global-session")
        self.assertEqual(profile.sec_ch_ua_platform, "account top platform")

    def test_turnstile_required_fails_closed(self) -> None:
        from services.openai_backend_api import OpenAIBackendAPI

        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        client.user_agent = "Test UA"
        client.pow_script_sources = []
        client.pow_data_build = ""

        with self.assertRaisesRegex(RuntimeError, "turnstile token"):
            client._build_requirements({"token": "requirements-token", "turnstile": {"required": True, "dx": "challenge"}})

    def test_grok_profile_prefers_network_profiles_over_legacy_key(self) -> None:
        profile = build_grok_console_profile({
            "grok_console_fingerprint": {"impersonate": "legacy", "user-agent": "Legacy UA"},
            "network_profiles": {"grok_console": {"impersonate": "profile", "user_agent": "Profile UA"}},
        })

        self.assertEqual(profile.impersonate, "profile")
        self.assertEqual(profile.user_agent, "Profile UA")
        self.assertTrue(profile.verify)
        self.assertEqual(profile.timeout, 60)

    def test_grok_profile_reads_cf_clearance_from_network_profile(self) -> None:
        profile = build_grok_console_profile({
            "grok_console_fingerprint": {"cf_clearance": "legacy-clearance"},
            "network_profiles": {"grok_console": {"cf_clearance": "profile-clearance"}},
        })

        self.assertEqual(profile.cf_clearance, "profile-clearance")

    def test_grok_console_headers_include_sso_and_cf_clearance_cookies(self) -> None:
        profile = build_grok_console_profile({
            "network_profiles": {"grok_console": {"cf_clearance": "profile-clearance"}},
        })

        headers = build_grok_console_headers(
            profile,
            access_token="access-token",
            base_url="https://grok.com",
        )

        self.assertEqual(headers["Cookie"], "sso=access-token; cf_clearance=profile-clearance")

    def test_session_kwargs_preserve_proxy_and_extra_kwargs(self) -> None:
        with mock.patch("services.proxy_service.config.get_proxy_settings", return_value="http://proxy.local:8080"):
            kwargs = build_session_kwargs(impersonate="edge101", verify=True, timeout=30)

        self.assertEqual(kwargs, {
            "impersonate": "edge101",
            "verify": True,
            "timeout": 30,
            "proxy": "http://proxy.local:8080",
        })


if __name__ == "__main__":
    unittest.main()
