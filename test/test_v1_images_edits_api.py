from __future__ import annotations

import base64
import unittest
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_pydantic_stub, install_starlette_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_pydantic_stub()
install_starlette_stub()
install_tiktoken_stub()

import sys
from typing import Any, cast

FastAPI = cast(Any, getattr(sys.modules["fastapi"], "FastAPI"))
TestClient = cast(Any, getattr(sys.modules["fastapi.testclient"], "TestClient"))

import api.ai as ai_module
import api.image_inputs as image_inputs_module
import api.image_tasks as image_tasks_module
from api.image_inputs import read_image_sources


AUTH_HEADERS = {"Authorization": "Bearer webchat2api"}
PNG_BYTES = b"\x89PNG\r\n\x1a\n"
GIF_BYTES = b"GIF89a"
DATA_IMAGE_URL = f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"
DATA_GIF_URL = f"data:image/gif;base64,{base64.b64encode(GIF_BYTES).decode('ascii')}"


class ImagesEditsApiTests(unittest.TestCase):
    def setUp(self):
        self.handle_calls = []

        def fake_handle(payload):
            self.handle_calls.append(payload)
            return {"created": 1, "data": [{"b64_json": base64.b64encode(b"out").decode("ascii")}]}

        self.handler_patcher = mock.patch.object(ai_module.openai_v1_image_edit, "handle", fake_handle)
        self.handler_patcher.start()
        self.addCleanup(self.handler_patcher.stop)
        self.log_patcher = mock.patch("services.log_service.log_service.add")
        self.log_patcher.start()
        self.addCleanup(self.log_patcher.stop)
        app = FastAPI()
        app.include_router(ai_module.create_router())
        self.client = TestClient(app)

    def test_edit_accepts_json_image_url(self):
        """测试图片编辑接口支持官方 JSON image_url 引用。"""
        response = self.client.post(
            "/v1/images/edits",
            headers=AUTH_HEADERS,
            json={
                "model": "gpt-image-2",
                "prompt": "edit",
                "images": [{"image_url": DATA_IMAGE_URL}],
                "n": 1,
                "response_format": "b64_json",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.handle_calls), 1)
        payload = self.handle_calls[0]
        self.assertEqual(payload["prompt"], "edit")
        self.assertEqual(payload["n"], 1)
        self.assertEqual(payload["images"], [(PNG_BYTES, "image_url.png", "image/png")])

    def test_edit_accepts_top_level_json_image_url_string(self):
        response = self.client.post(
            "/v1/images/edits",
            headers=AUTH_HEADERS,
            json={"model": "gpt-image-2", "prompt": "edit", "image_url": DATA_IMAGE_URL},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.handle_calls[0]["images"], [(PNG_BYTES, "image_url.png", "image/png")])

    def test_edit_accepts_json_images_strings_and_url_objects(self):
        response = self.client.post(
            "/v1/images/edits",
            headers=AUTH_HEADERS,
            json={
                "model": "gpt-image-2",
                "prompt": "edit",
                "images": [DATA_IMAGE_URL, {"url": DATA_GIF_URL}],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.handle_calls[0]["images"],
            [(PNG_BYTES, "image_url.png", "image/png"), (GIF_BYTES, "image_url.gif", "image/gif")],
        )

    def test_edit_accepts_multipart_image_field_variants(self):
        response = self.client.post(
            "/v1/images/edits",
            headers=AUTH_HEADERS,
            data={
                "model": "gpt-image-2",
                "prompt": "edit",
                "n": "2",
                "stream": "false",
                "image_url": DATA_IMAGE_URL,
                "images": DATA_GIF_URL,
            },
            files=[
                ("image", ("upload.png", PNG_BYTES, "image/png")),
                ("image[]", ("upload.gif", GIF_BYTES, "image/gif")),
            ],
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = self.handle_calls[0]
        self.assertEqual(payload["n"], 2)
        self.assertFalse(payload["stream"])
        self.assertEqual(
            payload["images"],
            [
                (PNG_BYTES, "image_url.png", "image/png"),
                (GIF_BYTES, "image_url.gif", "image/gif"),
                (PNG_BYTES, "upload.png", "image/png"),
                (GIF_BYTES, "upload.gif", "image/gif"),
            ],
        )

    def test_edit_accepts_multipart_image_url_array_and_images_array_fields(self):
        response = self.client.post(
            "/v1/images/edits",
            headers=AUTH_HEADERS,
            data={"model": "gpt-image-2", "prompt": "edit", "image_url[]": DATA_IMAGE_URL, "images[]": DATA_GIF_URL},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.handle_calls[0]["images"],
            [(PNG_BYTES, "image_url.png", "image/png"), (GIF_BYTES, "image_url.gif", "image/gif")],
        )

    def test_edit_rejects_file_id_reference(self):
        """测试图片编辑接口对暂不支持的 file_id 返回明确错误。"""
        response = self.client.post(
            "/v1/images/edits",
            headers=AUTH_HEADERS,
            json={
                "model": "gpt-image-2",
                "prompt": "edit",
                "images": [{"file_id": "file-abc123"}],
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("file_id image references are not supported", response.text)
        self.assertEqual(self.handle_calls, [])

    def test_edit_rejects_empty_file_id_reference(self):
        response = self.client.post(
            "/v1/images/edits",
            headers=AUTH_HEADERS,
            json={"model": "gpt-image-2", "prompt": "edit", "images": [{"file_id": ""}]},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("file_id image references are not supported", response.text)
        self.assertEqual(self.handle_calls, [])

    def test_edit_grok_image_edit_model_reaches_same_handler(self):
        response = self.client.post(
            "/v1/images/edits",
            headers=AUTH_HEADERS,
            json={
                "model": "grok-imagine-image-edit",
                "prompt": "edit",
                "images": [{"image_url": DATA_IMAGE_URL}],
                "n": 1,
                "size": "1024x1024",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.handle_calls), 1)
        payload = self.handle_calls[0]
        self.assertEqual(payload["model"], "grok-imagine-image-edit")
        self.assertEqual(payload["images"], [(PNG_BYTES, "image_url.png", "image/png")])


class ImageTasksEditsApiTests(unittest.TestCase):
    def setUp(self):
        self.submit_calls = []

        def fake_submit_edit(identity, **kwargs):
            self.submit_calls.append((identity, kwargs))
            return {"id": kwargs["client_task_id"], "status": "queued"}

        self.submit_patcher = mock.patch.object(image_tasks_module.image_task_service, "submit_edit", fake_submit_edit)
        self.submit_patcher.start()
        self.addCleanup(self.submit_patcher.stop)
        self.log_patcher = mock.patch("services.log_service.log_service.add")
        self.log_patcher.start()
        self.addCleanup(self.log_patcher.stop)
        app = FastAPI()
        app.include_router(image_tasks_module.create_router())
        self.client = TestClient(app)

    def test_image_task_edit_receives_normalized_images(self):
        response = self.client.post(
            "/api/image-tasks/edits",
            headers=AUTH_HEADERS,
            json={
                "client_task_id": "task-1",
                "model": "gpt-image-2",
                "prompt": "edit",
                "image_url": DATA_IMAGE_URL,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.submit_calls), 1)
        identity, kwargs = self.submit_calls[0]
        self.assertEqual(identity["name"], "管理员")
        self.assertEqual(kwargs["images"], [(PNG_BYTES, "image_url.png", "image/png")])


class ImageInputParserTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_image_sources_preserves_tuple_and_list_internal_formats(self):
        tuple_image = (PNG_BYTES, "tuple.png", "image/png")
        list_image = [GIF_BYTES, "list.gif", "image/gif"]

        images = await read_image_sources([tuple_image, list_image])

        self.assertEqual(images, [tuple_image, (GIF_BYTES, "list.gif", "image/gif")])

    async def test_read_image_sources_rejects_non_image_remote_response(self):
        response = type(
            "Response",
            (),
            {"status_code": 200, "headers": {"content-type": "text/plain"}, "content": b"not image"},
        )()

        with mock.patch.object(image_inputs_module.requests, "get", return_value=response, create=True):
            with self.assertRaises(Exception) as context:
                await read_image_sources(["https://example.test/file.txt"])

        self.assertIn("image_url must point to an image", str(context.exception))

    async def test_read_image_sources_rejects_unsupported_remote_scheme(self):
        with self.assertRaises(Exception) as context:
            await read_image_sources(["ftp://example.test/image.png"])

        self.assertIn("image_url must be an http or https URL", str(context.exception))


if __name__ == "__main__":
    unittest.main()
