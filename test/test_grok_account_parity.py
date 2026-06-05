from __future__ import annotations

import unittest
from typing import Any

from test.test_account_provider import MemoryStorage

from services.account_service import AccountService
from services.models import GROK_PROVIDER, resolve_model
from services.providers.grok import accounts as grok_accounts


def _storage() -> Any:
    return MemoryStorage()


class GrokAccountParityTests(unittest.TestCase):
    def test_bare_sso_token_normalizes_copied_unicode_artifacts(self) -> None:
        token = grok_accounts.normalize_access_token({"sso": " \ufeffabc\u2011def\u00a0ghi\u200b\u202fjkl\u2212mno\u2603 "})

        self.assertEqual(token, "abc-defghijkl-mno")

    def test_simple_sso_prefix_is_stripped_and_sanitized(self) -> None:
        token = grok_accounts.normalize_access_token({"sso_token": " sso = tok\u2014en\u00a0value\u200d "})

        self.assertEqual(token, "tok-envalue")

    def test_full_cookie_header_preserves_cookie_shape(self) -> None:
        header = " sso=abc def ; sso-rw=old value ; cf_clearance=keep value "
        token = grok_accounts.normalize_access_token({"cookie": header})

        self.assertEqual(token, header.strip())

    def test_existing_normalized_account_detection_still_preserves_token(self) -> None:
        token = grok_accounts.normalize_access_token({"access_token": " copied token value ", "quota_console": {"remaining": 1}})

        self.assertEqual(token, "copied token value")

    def test_tier_and_capability_aliases(self) -> None:
        self.assertEqual(grok_accounts.normalize_tier("FREE"), "basic")
        self.assertEqual(grok_accounts.normalize_tier("premium"), "super")
        self.assertEqual(grok_accounts.normalize_tier("super_grok"), "super")
        self.assertEqual(grok_accounts.normalize_tier("max"), "heavy")
        self.assertEqual(grok_accounts.normalize_capability("app-chat"), "chat")
        self.assertEqual(grok_accounts.normalize_capability("image-generation"), "image")
        self.assertEqual(grok_accounts.normalize_capability("image-edit"), "image_edit")

    def test_account_imports_upstream_tier_and_status_aliases(self) -> None:
        service = AccountService(_storage())
        result = service.add_account_items([
            {"sso": "basic-token", "provider": "grok", "category": "ssoBasic", "status": "active"},
            {"sso": "super-token", "provider": "grok", "pool": "ssoSuper", "status": "cooling"},
            {"sso": "max-token", "provider": "grok", "tier": "max", "status": "rate-limited"},
            {"sso": "expired-token", "provider": "grok", "tier": "basic", "status": "expired"},
        ])

        self.assertEqual(result["added"], 4)
        accounts = {account["access_token"]: account for account in service.list_accounts(provider=GROK_PROVIDER)}
        self.assertEqual(accounts["basic-token"]["tier"], "basic")
        self.assertEqual(accounts["basic-token"]["status"], "正常")
        self.assertEqual(accounts["super-token"]["tier"], "super")
        self.assertEqual(accounts["super-token"]["status"], "限流")
        self.assertEqual(accounts["max-token"]["tier"], "heavy")
        self.assertEqual(accounts["max-token"]["status"], "限流")
        self.assertEqual(accounts["expired-token"]["status"], "异常")

    def test_app_chat_selection_skips_upstream_status_aliases(self) -> None:
        service = AccountService(_storage())
        service.add_account_items([
            {"access_token": "sso=cooling-heavy", "provider": "grok", "tier": "heavy", "status": "cooling"},
            {"access_token": "sso=expired-heavy", "provider": "grok", "tier": "heavy", "status": "expired"},
            {"access_token": "sso=active-heavy", "provider": "grok", "tier": "heavy", "status": "active"},
        ])

        self.assertEqual(service.get_grok_app_chat_access_token(resolve_model("grok-4.20-heavy")), "active-heavy")

    def test_rate_limit_payload_normalizes_token_and_query_shapes(self) -> None:
        token_quota = grok_accounts.normalize_app_chat_rate_limit_payload({
            "limits": [
                {"remainingTokens": "120", "totalTokens": "140", "windowSizeSeconds": "7200", "category": "ssoSuper"},
            ]
        })
        query_quota = grok_accounts.normalize_app_chat_rate_limit_payload({
            "rateLimits": [
                {"remainingQueries": 72, "totalQueries": 80, "windowSizeSeconds": 86400, "pool": "ssoBasic"},
            ]
        })

        self.assertEqual(token_quota, {"remaining": 120, "total": 140, "window_seconds": 7200, "tier": "super"})
        self.assertEqual(query_quota, {"remaining": 72, "total": 80, "window_seconds": 86400, "tier": "basic"})

    def test_rate_limit_payload_infers_tier_from_window_size(self) -> None:
        basic_quota = grok_accounts.normalize_app_chat_rate_limit_payload({
            "remainingQueries": 79,
            "totalQueries": 80,
            "windowSizeSeconds": 86400,
        })
        super_quota = grok_accounts.normalize_app_chat_rate_limit_payload({
            "limits": {"remainingTokens": 139, "totalTokens": 140, "windowSizeSeconds": 7200}
        })

        self.assertEqual(basic_quota["tier"], "basic")
        self.assertEqual(super_quota["tier"], "super")

    def test_account_normalization_applies_rate_limit_payload(self) -> None:
        service = AccountService(_storage())
        service.add_account_items([
            {
                "sso": "rate-limit-token",
                "provider": "grok",
                "rateLimits": [{"remainingQueries": 70, "totalQueries": 80, "windowSizeSeconds": 86400}],
            },
        ])

        [account] = service.list_accounts(provider=GROK_PROVIDER)
        self.assertEqual(account["tier"], "basic")
        self.assertEqual(account["quota_console"], {
            "remaining": 70,
            "total": 80,
            "window_seconds": 86400,
            "reset_at": None,
        })
    def test_generic_auth_markers_are_not_confirmed_invalid(self) -> None:
        self.assertFalse(grok_accounts.is_auth_failure_payload({"code": "authentication_failed"}))
        self.assertFalse(grok_accounts.is_auth_failure_payload({"message": "unauthenticated"}))
        self.assertTrue(grok_accounts.is_auth_failure_payload({"code": "invalid-credentials"}))


if __name__ == "__main__":
    unittest.main()
