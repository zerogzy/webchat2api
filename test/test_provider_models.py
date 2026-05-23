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

from services.protocol import openai_v1_models


class FakeBackend:
    calls: list[str]
    fail_authenticated = False
    fail_anonymous = False

    def __init__(self, access_token: str = "") -> None:
        self.access_token = access_token
        self.__class__.calls.append(access_token)

    def __enter__(self) -> "FakeBackend":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def list_models(self) -> dict:
        if self.access_token:
            if self.fail_authenticated:
                raise RuntimeError("authenticated failure")
            return {
                "object": "list",
                "data": [{"id": "dynamic-gpt", "object": "model", "owned_by": "chatgpt"}],
            }
        if self.fail_anonymous:
            raise RuntimeError("anonymous failure")
        return {
            "object": "list",
            "data": [{"id": "anon-gpt", "object": "model", "owned_by": "chatgpt"}],
        }


class FakeAccountService:
    def __init__(self, token: str = "") -> None:
        self.token = token
        self.calls: list[str] = []

    def get_text_access_token(self, provider: str = "gpt") -> str:
        self.calls.append(provider)
        return self.token


class ProviderModelListTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeBackend.calls = []
        FakeBackend.fail_authenticated = False
        FakeBackend.fail_anonymous = False

    def test_list_models_includes_gpt_and_grok_metadata_when_chatgpt_unavailable(self) -> None:
        with mock.patch.dict(sys.modules, {"services.openai_backend_api": None}):
            result = openai_v1_models.list_models()

        models = {item["id"]: item for item in result["data"]}
        self.assertEqual(models["gpt-4o"]["provider"], "gpt")
        self.assertEqual(models["grok-4.3"]["provider"], "grok")
        self.assertEqual(models["grok-4.20-multi-agent"]["owned_by"], "xai")

    def test_list_models_tries_gpt_account_token_before_anonymous(self) -> None:
        account_service = FakeAccountService("stored-token")

        with mock.patch.object(openai_v1_models, "_get_gpt_access_token", account_service.get_text_access_token), \
             mock.patch.dict(sys.modules, {"services.openai_backend_api": types.SimpleNamespace(
                 OpenAIBackendAPI=FakeBackend,
             )}):
            result = openai_v1_models.list_models()

        models = {item["id"]: item for item in result["data"]}
        self.assertEqual(FakeBackend.calls, ["stored-token"])
        self.assertEqual(account_service.calls, ["gpt"])
        self.assertEqual(models["dynamic-gpt"]["provider"], "gpt")
        self.assertNotIn("anon-gpt", models)
        self.assertIn("gpt-image-2", models)
        self.assertIn("grok-4.3", models)

    def test_list_models_falls_back_to_anonymous_when_account_fetch_fails(self) -> None:
        account_service = FakeAccountService("stored-token")
        FakeBackend.fail_authenticated = True

        with mock.patch.object(openai_v1_models, "_get_gpt_access_token", account_service.get_text_access_token), \
             mock.patch.dict(sys.modules, {"services.openai_backend_api": types.SimpleNamespace(
                 OpenAIBackendAPI=FakeBackend,
             )}):
            result = openai_v1_models.list_models()

        models = {item["id"]: item for item in result["data"]}
        self.assertEqual(FakeBackend.calls, ["stored-token", ""])
        self.assertEqual(models["anon-gpt"]["provider"], "gpt")
        self.assertNotIn("dynamic-gpt", models)
        self.assertIn("gpt-4o", models)
        self.assertIn("gpt-image-2", models)
        self.assertIn("grok-4.3", models)

    def test_list_models_uses_fallbacks_when_authenticated_and_anonymous_fetch_fail(self) -> None:
        account_service = FakeAccountService("stored-token")
        FakeBackend.fail_authenticated = True
        FakeBackend.fail_anonymous = True

        with mock.patch.object(openai_v1_models, "_get_gpt_access_token", account_service.get_text_access_token), \
             mock.patch.dict(sys.modules, {"services.openai_backend_api": types.SimpleNamespace(
                 OpenAIBackendAPI=FakeBackend,
             )}):
            result = openai_v1_models.list_models()

        models = {item["id"]: item for item in result["data"]}
        self.assertEqual(FakeBackend.calls, ["stored-token", ""])
        self.assertEqual(models["gpt-4o"]["provider"], "gpt")
        self.assertEqual(models["gpt-image-2"]["provider"], "gpt")
        self.assertEqual(models["grok-4.3"]["provider"], "grok")

    def test_list_models_includes_new_grok_aliases_and_image_models(self) -> None:
        with mock.patch.dict(sys.modules, {"services.openai_backend_api": None}):
            result = openai_v1_models.list_models()

        models = {item["id"]: item for item in result["data"]}
        for model_id in [
            "grok-4.20-0309-non-reasoning",
            "grok-4.20-0309-heavy",
            "grok-4.20-heavy",
            "grok-4.3-beta",
            "grok-imagine-image-lite",
            "grok-imagine-image",
            "grok-imagine-image-pro",
            "grok-imagine-image-edit",
            "grok-imagine-video",
        ]:
            self.assertIn(model_id, models)
            self.assertEqual(models[model_id]["provider"], "grok")
        self.assertEqual(models["grok-imagine-image-lite"]["capability"], "image")
        self.assertEqual(models["grok-imagine-image-edit"]["capability"], "image_edit")
        self.assertEqual(models["grok-imagine-video"]["capability"], "video")


if __name__ == "__main__":
    unittest.main()
