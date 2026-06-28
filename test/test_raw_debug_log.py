from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

if "curl_cffi" not in sys.modules:
    curl_cffi = types.ModuleType("curl_cffi")
    requests_module = types.ModuleType("curl_cffi.requests")
    setattr(requests_module, "Session", object)
    setattr(requests_module, "Response", object)
    setattr(requests_module, "exceptions", types.SimpleNamespace(RequestException=Exception))
    setattr(curl_cffi, "requests", requests_module)
    sys.modules["curl_cffi"] = curl_cffi
    sys.modules["curl_cffi.requests"] = requests_module

from test.optional_stubs import install_fastapi_stubs

install_fastapi_stubs()

from services.config import config
from services.raw_debug_log import MAX_STRING_CHARS, raw_debug_log


class RawDebugLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = config.data.get("raw_debug_logging")

    def tearDown(self) -> None:
        if self.previous is None:
            config.data.pop("raw_debug_logging", None)
        else:
            config.data["raw_debug_logging"] = self.previous

    def test_raw_debug_log_is_disabled_by_default(self) -> None:
        config.data["raw_debug_logging"] = False
        with mock.patch("services.raw_debug_log.log_service.add") as add:
            raw_debug_log("raw", {"response": {"text": "ok"}})
        add.assert_not_called()

    def test_raw_debug_log_redacts_secrets_and_clips_large_strings(self) -> None:
        config.data["raw_debug_logging"] = True
        long_text = "x" * (MAX_STRING_CHARS + 1)

        with mock.patch("services.raw_debug_log.log_service.add") as add:
            raw_debug_log(
                "raw",
                {
                    "headers": {"Authorization": "Bearer secret"},
                    "body": {"api_key": "secret-key", "content": long_text},
                },
            )

        add.assert_called_once()
        self.assertEqual(add.call_args.args[0], "raw_debug")
        detail = add.call_args.args[2]
        self.assertEqual(detail["headers"]["Authorization"], "[REDACTED]")
        self.assertEqual(detail["body"]["api_key"], "[REDACTED]")
        self.assertTrue(detail["body"]["content"]["truncated"])
        self.assertEqual(detail["body"]["content"]["chars"], MAX_STRING_CHARS + 1)


if __name__ == "__main__":
    unittest.main()
