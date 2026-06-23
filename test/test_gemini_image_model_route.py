from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_tiktoken_stub()

from fastapi import HTTPException

from services.protocol import openai_v1_chat_complete, openai_v1_image_edit, openai_v1_image_generations
from services.providers.gemini import api_client


class GeminiImageModelRouteTests(unittest.TestCase):
    def test_gemini_image_model_rejects_chat_completion(self):
        with self.assertRaises(HTTPException) as ctx:
            openai_v1_chat_complete.handle({
                "model": "gemini-image",
                "messages": [{"role": "user", "content": "画一只猫"}],
            })
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("/v1/images/*", str(ctx.exception.detail))

    def test_gemini_image_pro_model_rejects_chat_completion(self):
        with self.assertRaises(HTTPException) as ctx:
            openai_v1_chat_complete.handle({
                "model": "gemini-image-pro",
                "messages": [{"role": "user", "content": "画一只狗"}],
            })
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("/v1/images/*", str(ctx.exception.detail))

    def test_gemini_image_model_resolves_in_image_generation_endpoint(self):
        original = openai_v1_image_generations.image_generation_outputs
        calls = []

        def fake_image_generation_outputs(spec, request, *, body, prompt, n):
            calls.append((spec.id, request.model, prompt, n))
            return iter(())

        try:
            openai_v1_image_generations.image_generation_outputs = fake_image_generation_outputs
            response = openai_v1_image_generations.handle({
                "model": "gemini-image",
                "prompt": "画一张海报",
                "n": 1,
            })
        finally:
            openai_v1_image_generations.image_generation_outputs = original

        self.assertEqual(response["data"], [])
        self.assertEqual(calls, [("gemini-image", "gemini-image", "画一张海报", 1)])

    def test_gemini_image_model_resolves_in_image_edit_endpoint(self):
        original = openai_v1_image_edit.image_edit_outputs
        calls = []

        def fake_image_edit_outputs(spec, request, *, body, prompt, images, n, size):
            calls.append((spec.id, request.model, prompt, len(request.images or []), n, size))
            return iter(())

        try:
            openai_v1_image_edit.image_edit_outputs = fake_image_edit_outputs
            response = openai_v1_image_edit.handle({
                "model": "gemini-image-pro",
                "prompt": "把图片改成水彩风格",
                "images": [(b"fake", "input.png", "image/png")],
                "n": 1,
                "size": "1024x1024",
            })
        finally:
            openai_v1_image_edit.image_edit_outputs = original

        self.assertEqual(response["data"], [])
        self.assertEqual(calls, [("gemini-image-pro", "gemini-image-pro", "把图片改成水彩风格", 1, 1, "1024x1024")])

    def test_gemini_materializes_base64_image_edit_input(self):
        encoded = base64.b64encode(b"fake-image").decode("ascii")
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = api_client._materialize_files(Path(temp_dir), [encoded])
            self.assertIsNotNone(paths)
            self.assertEqual(len(paths or []), 1)
            path = Path(str((paths or [])[0]))
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"fake-image")

    def test_gemini_materializes_long_base64_before_path_stat(self):
        encoded = base64.b64encode(b"fake-image" * 256).decode("ascii")
        self.assertGreater(len(encoded), 255)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = api_client._materialize_files(Path(temp_dir), [encoded])
            self.assertIsNotNone(paths)
            path = Path(str((paths or [])[0]))
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"fake-image" * 256)

    def test_gemini_materializes_data_url_image_edit_input(self):
        encoded = base64.b64encode(b"fake-image").decode("ascii")
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = api_client._materialize_files(Path(temp_dir), [f"data:image/jpeg;base64,{encoded}"])
            self.assertIsNotNone(paths)
            path = Path(str((paths or [])[0]))
            self.assertEqual(path.suffix, ".jpg")
            self.assertEqual(path.read_bytes(), b"fake-image")


if __name__ == "__main__":
    unittest.main()
