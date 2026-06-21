from __future__ import annotations

import unittest
from unittest import mock

from fastapi import HTTPException

from api.account_flows import catpaw
from services.providers.base import CATPAW_PROVIDER


class CatpawQrLoginTests(unittest.TestCase):
    def test_build_account_payload_uses_mis_from_token_data_before_user_info(self) -> None:
        token_data = {
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "misId": "user-mis",
            "loginName": "fallback-login",
        }

        with mock.patch(
            "services.providers.catpaw.client.get_user_info",
            return_value={"loginName": "catpaw-login", "email": "catpaw@example.test"},
        ) as get_user_info:
            payload = catpaw.build_account_payload(token_data, "")

        get_user_info.assert_called_once_with("access-token", "user-mis")
        self.assertEqual(payload["provider"], CATPAW_PROVIDER)
        self.assertEqual(payload["catpaw_id"], "user-mis")
        self.assertEqual(payload["mis_id"], "user-mis")
        self.assertEqual(payload["login_name"], "catpaw-login")
        self.assertEqual(payload["email"], "catpaw@example.test")

    def test_build_account_payload_uses_mis_from_user_info_when_token_data_has_none(self) -> None:
        token_data = {
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
        }

        with mock.patch(
            "services.providers.catpaw.client.get_user_info",
            return_value={"misId": "info-mis", "loginName": "catpaw-login"},
        ) as get_user_info:
            payload = catpaw.build_account_payload(token_data, "")

        get_user_info.assert_called_once_with("access-token", None)
        self.assertEqual(payload["catpaw_id"], "info-mis")
        self.assertEqual(payload["mis_id"], "info-mis")
        self.assertEqual(payload["login_name"], "catpaw-login")

    def test_build_account_payload_rejects_login_without_mis_identity(self) -> None:
        token_data = {
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
        }

        with mock.patch("services.providers.catpaw.client.get_user_info", return_value={}) as get_user_info:
            with self.assertRaises(HTTPException) as ctx:
                catpaw.build_account_payload(token_data, "")

        get_user_info.assert_called_once_with("access-token", None)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail["error"], "catpaw_mis_id_required")


if __name__ == "__main__":
    unittest.main()
