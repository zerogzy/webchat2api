from __future__ import annotations

import sys
import types
import unittest
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
from services.providers.joycode.client import JoyCodeClient, parse_oauth_pt_key


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
