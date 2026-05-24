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
from services.network.profiles import build_chatgpt_web_profile, build_grok_app_chat_profile, build_grok_console_profile


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

    def test_authenticated_chat_requirements_passes_generated_p_to_turnstile_builder(self) -> None:
        from services.openai_backend_api import ChatRequirements, OpenAIBackendAPI

        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        client.access_token = "access-token"
        client.base_url = "https://chatgpt.com"
        client.user_agent = "Test UA"
        client.pow_script_sources = []
        client.pow_data_build = ""
        response = mock.Mock(status_code=200, headers={})
        response.json.return_value = {"token": "requirements-token", "turnstile": {"required": True, "dx": "challenge"}}
        client.session = mock.Mock(headers={})
        client.session.post.return_value = response
        client._call_with_retry = lambda callback, context: callback()
        client._build_requirements = mock.Mock(return_value=ChatRequirements(token="requirements-token"))

        with mock.patch("services.openai_backend_api.build_legacy_requirements_token", return_value="generated-p"):
            requirements = client._get_chat_requirements()

        self.assertEqual(requirements.token, "requirements-token")
        client.session.post.assert_called_once()
        self.assertEqual(client.session.post.call_args.kwargs["json"], {"p": "generated-p"})
        client._build_requirements.assert_called_once_with(response.json.return_value, "generated-p")

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

    def test_grok_app_chat_profile_prefers_app_profile_with_console_fallback(self) -> None:
        profile = build_grok_app_chat_profile({
            "network_profiles": {
                "grok_console": {
                    "impersonate": "console-browser",
                    "user-agent": "Console UA",
                    "verify": False,
                    "timeout": 12,
                    "cf_clearance": "console-clearance",
                },
                "grok_app_chat": {
                    "browser": "app-browser",
                    "user-agent": "App UA",
                    "cf_cookies": "cf_bm=profile-bm",
                    "cf_clearance": "app-clearance",
                    "sec-ch-ua": "app sec ua",
                    "sec-ch-ua-mobile": "?1",
                    "sec-ch-ua-platform": '"Linux"',
                    "statsig_id": "statsig-profile",
                },
            },
        })

        self.assertEqual(profile.impersonate, "app-browser")
        self.assertEqual(profile.user_agent, "App UA")
        self.assertFalse(profile.verify)
        self.assertEqual(profile.timeout, 12)
        self.assertEqual(profile.cf_cookies, "cf_bm=profile-bm")
        self.assertEqual(profile.cf_clearance, "app-clearance")
        self.assertEqual(profile.sec_ch_ua, "app sec ua")
        self.assertEqual(profile.sec_ch_ua_mobile, "?1")
        self.assertEqual(profile.sec_ch_ua_platform, '"Linux"')
        self.assertEqual(profile.statsig_id, "statsig-profile")

    def test_grok_app_chat_profile_derives_client_hints_from_user_agent(self) -> None:
        profile = build_grok_app_chat_profile({
            "network_profiles": {
                "grok_app_chat": {
                    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
                    "cf_cookies": "cf_bm=profile-bm",
                },
            },
        })

        self.assertEqual(profile.impersonate, "chrome141")
        self.assertEqual(profile.sec_ch_ua, '"Chromium";v="141", "Google Chrome";v="141", "Not.A/Brand";v="99"')
        self.assertEqual(profile.sec_ch_ua_mobile, "?0")
        self.assertEqual(profile.sec_ch_ua_platform, '"Linux"')

    def test_grok_app_chat_profile_keeps_explicit_client_hints(self) -> None:
        profile = build_grok_app_chat_profile({
            "network_profiles": {
                "grok_app_chat": {
                    "browser": "chrome141",
                    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
                    "sec-ch-ua": "explicit sec ua",
                    "sec-ch-ua-mobile": "?1",
                    "sec-ch-ua-platform": '"ExplicitOS"',
                },
            },
        })

        self.assertEqual(profile.sec_ch_ua, "explicit sec ua")
        self.assertEqual(profile.sec_ch_ua_mobile, "?1")
        self.assertEqual(profile.sec_ch_ua_platform, '"ExplicitOS"')

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
