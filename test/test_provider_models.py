from __future__ import annotations

import sys
import types
import unittest
from typing import cast
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

from test.optional_stubs import install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_tiktoken_stub

install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_tiktoken_stub()

from services.models import resolve_model
from services.protocol.conversation import ConversationRequest, ImageOutput, conversation_events
from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import openai_v1_models
from services.providers.gpt import images as gpt_images


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
        self.assertEqual(models["gemini-2.5-pro"]["provider"], "gemini")
        self.assertEqual(models["gemini-pro"]["owned_by"], "google")
        self.assertEqual(models["grok-4.20-multi-agent"]["owned_by"], "xai")
        for model_id in ["gpt-5-1", "gpt-5-2", "gpt-5-3", "gpt-5-3-mini", "gpt-5-mini"]:
            self.assertEqual(models[model_id]["provider"], "gpt")
            self.assertEqual(models[model_id]["owned_by"], "chatgpt")

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
        self.assertIn("gemini-2.5-flash", models)
        self.assertIn("gemini-2.5-pro", models)

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
        self.assertEqual(models["gpt-image-2"]["capability"], "image")
        self.assertEqual(models["codex-gpt-image-2"]["provider"], "gpt")
        self.assertEqual(models["codex-gpt-image-2"]["capability"], "image")
        for model_id, tier in [
            ("plus-codex-gpt-image-2", "plus"),
            ("team-codex-gpt-image-2", "team"),
            ("pro-codex-gpt-image-2", "pro"),
        ]:
            self.assertEqual(models[model_id]["provider"], "gpt")
            self.assertEqual(models[model_id]["capability"], "image")
            spec = resolve_model(model_id)
            self.assertEqual(spec.capability, "image")
            self.assertEqual(spec.upstream_model, "codex-gpt-image-2")
            self.assertEqual(spec.model_tier, tier)
        self.assertEqual(resolve_model("gpt-image-2").capability, "image")
        self.assertEqual(resolve_model("codex-gpt-image-2").capability, "image")
        self.assertEqual(models["grok-4.3"]["provider"], "grok")
        self.assertEqual(models["gemini-pro"]["provider"], "gemini")

    def test_gpt_image_alias_routes_to_upstream_model_and_preserves_response_model(self) -> None:
        seen_models: list[str] = []

        def fake_stream(request: ConversationRequest):
            seen_models.append(request.model)
            yield ImageOutput(kind="result", model=request.model, index=1, total=1, data=[])

        request = ConversationRequest(prompt="draw", model="team-codex-gpt-image-2")
        spec = resolve_model("team-codex-gpt-image-2")
        with mock.patch.object(gpt_images, "stream_image_outputs_with_pool", fake_stream):
            outputs = list(gpt_images.generation_outputs(request, spec))

        self.assertEqual(seen_models, ["codex-gpt-image-2"])
        self.assertEqual(outputs[0].model, "team-codex-gpt-image-2")

    def test_codex_upstream_model_remains_image_mode_for_conversation_events(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeBackend:
            def stream_conversation(self, **kwargs):
                calls.append(kwargs)
                return iter(())

        list(conversation_events(cast(OpenAIBackendAPI, FakeBackend()), prompt="draw", model="codex-gpt-image-2", images=["image-data"]))

        self.assertEqual(calls[0]["model"], "codex-gpt-image-2")
        self.assertEqual(calls[0]["images"], ["image-data"])
        self.assertEqual(calls[0]["system_hints"], ["picture_v2"])

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

    def test_list_models_uses_gemini_static_metadata_without_dynamic_hook(self) -> None:
        with mock.patch.object(openai_v1_models, "_fetch_chatgpt_models", side_effect=RuntimeError("unavailable")):
            result = openai_v1_models.list_models()

        models = {item["id"]: item for item in result["data"]}
        self.assertEqual(models["gemini-2.5-pro"]["provider"], "gemini")
        self.assertEqual(models["gemini-2.5-flash"]["owned_by"], "google")
        self.assertEqual(models["gemini-pro"]["provider"], "gemini")
        self.assertFalse(hasattr(openai_v1_models, "_dynamic_gemini_model_metadata"))


if __name__ == "__main__":
    unittest.main()
