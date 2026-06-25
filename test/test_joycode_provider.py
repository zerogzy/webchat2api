from __future__ import annotations

import sys
import types
import unittest
import importlib.util
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from unittest import mock

if "curl_cffi" not in sys.modules:
    curl_cffi = types.ModuleType("curl_cffi")
    requests_module = types.ModuleType("curl_cffi.requests")
    setattr(requests_module, "Session", object)
    setattr(curl_cffi, "requests", requests_module)
    sys.modules["curl_cffi"] = curl_cffi
    sys.modules["curl_cffi.requests"] = requests_module

from test.optional_stubs import install_fastapi_stubs

install_fastapi_stubs()

from services.providers.registry import resolve_model, normalize_account_provider
from services.providers.base import JOYCODE_PROVIDER
from services.providers.joycode.accounts import normalize_account, sanitize_account
from services.providers.joycode.client import JoyCodeClient, parse_oauth_credentials, parse_oauth_pt_key

_joycode_flow_spec = importlib.util.spec_from_file_location("joycode_flow", Path(__file__).parents[1] / "api/account_flows/joycode.py")
joycode_flow = importlib.util.module_from_spec(_joycode_flow_spec)
assert _joycode_flow_spec and _joycode_flow_spec.loader
_joycode_flow_spec.loader.exec_module(joycode_flow)


class JoyCodeProviderTests(unittest.TestCase):
    def test_model_routes_to_joycode(self) -> None:
        spec = resolve_model("GLM-5.1")

        self.assertEqual(spec.provider, JOYCODE_PROVIDER)
        self.assertEqual(normalize_account_provider("joy-code"), JOYCODE_PROVIDER)

    def test_account_normalization_hides_pt_key(self) -> None:
        account = normalize_account({"provider": "joycode", "pt_key": "secret", "user_id": "u1"})

        self.assertEqual(account["access_token"], "u1")
        self.assertEqual(account["pt_key"], "secret")
        sanitized = sanitize_account(account)
        self.assertNotIn("pt_key", sanitized)
        self.assertTrue(sanitized["has_pt_key"])

    def test_oauth_url_parser_accepts_url_or_plain_key(self) -> None:
        self.assertEqual(parse_oauth_pt_key("http://127.0.0.1:83/?pt_key=abc&x=1"), "abc")
        self.assertEqual(parse_oauth_pt_key("abc"), "abc")

    def test_oauth_url_parser_preserves_joycode_callback_metadata(self) -> None:
        creds = parse_oauth_credentials(
            "http://127.0.0.1:83/?pt_key=abc&loginType=PIN_JD_CLOUD&tenant=tenant_jd_x&base_url=https%3A%2F%2Fapi-ai.jd.com"
        )

        self.assertEqual(creds.pt_key, "abc")
        self.assertEqual(creds.login_type, "PIN_JD_CLOUD")
        self.assertEqual(creds.tenant, "tenant_jd_x")
        self.assertEqual(creds.color_base_url, "https://api-ai.jd.com")

    def test_jd_qr_validation_reads_pt_key_from_set_cookie(self) -> None:
        session = types.SimpleNamespace(cookies={})
        response = types.SimpleNamespace(cookies={}, headers={"set-cookie": "pt_key=qr-key; Path=/; HttpOnly"})

        self.assertEqual(joycode_flow._validation_pt_key(session, response), "qr-key")

    def test_jd_qr_validation_reads_domain_cookie_jar(self) -> None:
        jar = CookieJar()
        jar.set_cookie(Cookie(0, "pt_key", "domain-key", None, False, ".jd.com", True, True, "/", True, False, None, False, None, None, {}, False))
        session = types.SimpleNamespace(cookies=types.SimpleNamespace(jar=jar))
        response = types.SimpleNamespace(cookies={}, headers={})

        self.assertEqual(joycode_flow._validation_pt_key(session, response), "domain-key")

    def test_jd_qr_validation_follows_login_relay_risk_1100(self) -> None:
        response = types.SimpleNamespace(
            cookies={},
            headers={},
            json=lambda: {"returnCode": 0, "riskCode": 1100, "url": "http://passport.jd.com/relay/loginRelay?x=1"},
        )
        session = types.SimpleNamespace(
            cookies={},
            get=mock.Mock(return_value=types.SimpleNamespace(cookies={"pt_key": "relay-key"}, headers={})),
        )

        self.assertEqual(joycode_flow._validation_pt_key(session, response), "relay-key")
        self.assertEqual(session.get.call_args.args[0], "https://passport.jd.com/relay/loginRelay?x=1")

    def test_jd_qr_validation_quotes_ticket(self) -> None:
        get = mock.Mock(side_effect=[
            types.SimpleNamespace(text='jsonpCallback({"code":200,"ticket":"a+b/c="})'),
            types.SimpleNamespace(cookies={"pt_key": "quoted-key"}, headers={}),
        ])
        session = types.SimpleNamespace(get=get, cookies={})
        job_id = "job"
        joycode_flow._QR_JOBS[job_id] = {"session": session, "token": "token", "created_at": 0}
        with mock.patch("time.time", return_value=1), mock.patch.object(joycode_flow, "_user_payload", return_value={"pt_key": "quoted-key"}):
            result = joycode_flow.poll_qr_login(job_id, account_service=types.SimpleNamespace(add_account_items=lambda items: {"accounts": items}), sanitize_account_result=lambda value: value)

        self.assertIn("a%2Bb%2Fc%3D", get.call_args_list[1].args[0])
        self.assertEqual(result["status"], "success")

    def test_prepare_body_uses_joycode_defaults(self) -> None:
        fake_session = types.SimpleNamespace(close=lambda: None)
        with mock.patch("services.providers.joycode.client.create_session", return_value=fake_session):
            client = JoyCodeClient({"pt_key": "k", "user_id": "u"})
            try:
                body = client.prepare_body({"model": "JoyAI-Code"})
            finally:
                client.close()

        self.assertEqual(body["tenant"], "JOYCODE")
        self.assertEqual(body["userId"], "u")
        self.assertEqual(body["client"], "JoyCode")
        self.assertEqual(body["model"], "JoyAI-Code")


if __name__ == "__main__":
    unittest.main()
