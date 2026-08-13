from __future__ import annotations

import unittest

from services.providers.gpt.model_catalog import GPTModelCatalog


class GPTModelCatalogTests(unittest.TestCase):
    def test_catalog_tries_next_account_of_same_type_and_routes_alias(self) -> None:
        catalog = GPTModelCatalog()
        catalog._accounts = lambda: [
            {"type": "Plus", "access_token": "bad"},
            {"type": "Plus", "access_token": "good"},
        ]
        catalog._fetch = lambda token="": (
            (_ for _ in ()).throw(RuntimeError("expired")) if token == "bad"
            else {"gpt-5-6-thinking"} if token == "good"
            else {"gpt-5-5"}
        )

        account_types, allow_anonymous = catalog.account_types_for_model("gpt-5-6-thinking-extended")

        self.assertEqual(account_types, {"Plus"})
        self.assertFalse(allow_anonymous)
        self.assertEqual({item["id"] for item in catalog.list_models()["data"]}, {"gpt-5-5", "gpt-5-6-thinking"})


if __name__ == "__main__":
    unittest.main()
