from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch

from services.providers.base import CATPAW_PROVIDER, GEMINI_PROVIDER, GROK_PROVIDER


class ProviderAccountOpsTests(unittest.TestCase):
    def test_identifier_matching_is_provider_owned(self) -> None:
        account_ops = importlib.import_module("services.providers.account_ops")

        gemini = {"account_id": "gemini-a", "__Secure-1PSID": "psid", "__Secure-1PSIDTS": "ts"}
        grok = {"access_token": "sso-token", "app_chat": True}
        catpaw = {"catpaw_id": "catpaw-a", "catpaw_access_token": "secret-token"}

        self.assertEqual(
            account_ops.matched_account_tokens_by_identifiers(
                [{"account_id": "gemini-a"}],
                {"gemini-token": gemini},
                GEMINI_PROVIDER,
            ),
            {"gemini-token"},
        )
        self.assertEqual(
            account_ops.matched_account_tokens_by_identifiers(
                [{"row_id": account_ops.account_row_id_for_provider(grok, GROK_PROVIDER)}],
                {"grok-token": grok},
                GROK_PROVIDER,
            ),
            {"grok-token"},
        )
        self.assertEqual(
            account_ops.matched_account_tokens_by_identifiers(
                [{"row_id": account_ops.account_row_id_for_provider(catpaw, CATPAW_PROVIDER)}],
                {"catpaw-a": catpaw},
                CATPAW_PROVIDER,
            ),
            {"catpaw-a"},
        )

    def test_catpaw_headers_require_explicit_identity_configuration(self) -> None:
        with patch.dict(os.environ, {"CATPAW_MIS_ID": "", "CATPAW_TENANT": ""}, clear=False):
            import services.providers.catpaw.client as catpaw_client

            catpaw_client = importlib.reload(catpaw_client)

            self.assertNotIn("tenant", catpaw_client._common_headers())
            with self.assertRaises(catpaw_client.CatpawError):
                catpaw_client._auth_headers("access-token")


if __name__ == "__main__":
    unittest.main()
