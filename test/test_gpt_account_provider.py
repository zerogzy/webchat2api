from __future__ import annotations

import unittest

from services.providers.base import GEMINI_PROVIDER, GPT_PROVIDER, ModelSpec
from services.providers.gpt import accounts as gpt_accounts


class GPTAccountProviderTests(unittest.TestCase):
    def test_normalizes_access_token_tier_status_capabilities_and_quota(self) -> None:
        account = gpt_accounts.normalize_account({
            "accessToken": "  token-value  ",
            "type": "PlusLite",
            "status": "rate_limited",
            "capabilities": "text-chat; image-generation; image-edit",
            "quota_console": {"remaining": "5", "total": "10", "window_seconds": "60", "reset_at": "123"},
        })

        self.assertEqual(gpt_accounts.normalize_access_token(account), "token-value")
        self.assertEqual(account["tier"], "plus")
        self.assertEqual(account["status"], "限流")
        self.assertEqual(account["capabilities"], ["chat", "image", "image_edit"])
        self.assertEqual(account["quota_console"], {"remaining": 5, "total": 10, "window_seconds": 60, "reset_at": 123})

    def test_sanitize_hides_refresh_credentials_but_keeps_public_token_identifier(self) -> None:
        account = gpt_accounts.sanitize_account({
            "provider": GPT_PROVIDER,
            "access_token": "admin-visible-token",
            "id_token": "secret-id-token",
            "refresh_token": "secret-refresh-token",
            "sso": "secret-sso",
            "status": "正常",
        })

        self.assertEqual(account["access_token"], "admin-visible-token")
        self.assertNotIn("id_token", account)
        self.assertNotIn("refresh_token", account)
        self.assertNotIn("sso", account)
        self.assertTrue(account["has_id_token"])
        self.assertTrue(account["has_refresh_token"])
        self.assertTrue(account["has_sso"])

    def test_export_item_preserves_existing_gpt_export_fields(self) -> None:
        item = gpt_accounts.build_export_item({
            "access_token": " access-token ",
            "id_token": " id-token ",
            "refresh_token": " refresh-token ",
            "email": " user@example.com ",
            "account_id": " account-id ",
            "sso": " sso-token ",
            "last_refresh": " refreshed ",
            "expired": " expired ",
        })

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["access_token"], "access-token")
        self.assertEqual(item["id_token"], "id-token")
        self.assertEqual(item["refresh_token"], "refresh-token")
        self.assertEqual(item["email"], "user@example.com")
        self.assertEqual(item["account_id"], "account-id")
        self.assertEqual(item["sso"], "sso-token")

    def test_image_availability_accepts_unknown_quota_and_rejects_unavailable_status(self) -> None:
        self.assertTrue(gpt_accounts.is_image_account_available({
            "provider": GPT_PROVIDER,
            "status": "正常",
            "quota": 0,
            "image_quota_unknown": True,
        }))
        self.assertFalse(gpt_accounts.is_image_account_available({
            "provider": GPT_PROVIDER,
            "status": "限流",
            "quota": 10,
            "image_quota_unknown": True,
        }))
        self.assertFalse(gpt_accounts.is_image_account_available({
            "provider": GEMINI_PROVIDER,
            "status": "正常",
            "quota": 10,
        }))

    def test_console_quota_resets_and_availability_tracks_known_remaining_quota(self) -> None:
        quota = gpt_accounts.normalize_console_quota({"remaining": "99", "total": "3", "reset_at": "10"})
        self.assertEqual(quota, {"remaining": 3, "total": 3, "window_seconds": 0, "reset_at": 10})

        reset = gpt_accounts.reset_console_quota_if_ready({
            "provider": GPT_PROVIDER,
            "status": "限流",
            "quota_console": {"remaining": 0, "total": 3, "reset_at": 10},
        }, 11)
        self.assertEqual(reset["quota_console"], {"remaining": 3, "total": 3, "window_seconds": 0, "reset_at": None})
        self.assertEqual(reset["status"], "正常")
        self.assertTrue(gpt_accounts.is_console_account_available(reset, 11))
        self.assertFalse(gpt_accounts.is_console_account_available({"status": "正常", "quota_console": {"remaining": 0, "total": 3}}, 11))

    def test_tier_and_capability_decisions_follow_model_spec(self) -> None:
        plus_image = ModelSpec("plus-codex-gpt-image-2", GPT_PROVIDER, "chatgpt", model_tier="plus", capability="image")
        pro_image = ModelSpec("pro-codex-gpt-image-2", GPT_PROVIDER, "chatgpt", model_tier="pro", capability="image")
        video = ModelSpec("future-video", GPT_PROVIDER, "chatgpt", capability="video")

        self.assertEqual(gpt_accounts.requested_tiers(plus_image), ["plus"])
        self.assertTrue(gpt_accounts.tier_matches("Team", "plus"))
        self.assertTrue(gpt_accounts.tier_matches("pro", "team"))
        self.assertFalse(gpt_accounts.tier_matches("plus", "pro"))
        self.assertTrue(gpt_accounts.account_has_capability({"capabilities": "chat,image"}, plus_image))
        self.assertFalse(gpt_accounts.account_has_capability({"capabilities": "chat"}, pro_image))
        self.assertFalse(gpt_accounts.account_has_capability({"capabilities": "video"}, video))

    def test_auth_failure_payload_detects_known_invalid_token_shapes(self) -> None:
        self.assertTrue(gpt_accounts.is_auth_failure_payload({"status_code": 401, "detail": "Unauthorized"}))
        self.assertTrue(gpt_accounts.is_auth_failure_payload({"error": {"message": "Invalid token supplied"}}))
        self.assertTrue(gpt_accounts.is_auth_failure_payload([{"code": "session_expired"}]))
        self.assertFalse(gpt_accounts.is_auth_failure_payload({"status_code": 429, "message": "rate limited"}))


if __name__ == "__main__":
    unittest.main()
