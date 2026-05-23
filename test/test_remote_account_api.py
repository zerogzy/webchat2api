from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

if "sqlalchemy" not in sys.modules:
    sqlalchemy = types.ModuleType("sqlalchemy")
    sqlalchemy.Column = lambda *args, **kwargs: None
    sqlalchemy.String = lambda *args, **kwargs: str
    sqlalchemy.Text = lambda *args, **kwargs: str
    sqlalchemy.Integer = lambda *args, **kwargs: int
    sqlalchemy.create_engine = lambda *args, **kwargs: None
    sqlalchemy.text = lambda value: value
    sqlalchemy_ext = types.ModuleType("sqlalchemy.ext")
    sqlalchemy_declarative = types.ModuleType("sqlalchemy.ext.declarative")

    class BaseStub:
        metadata = types.SimpleNamespace(create_all=lambda *args, **kwargs: None)

    sqlalchemy_declarative.declarative_base = lambda: BaseStub
    sqlalchemy_orm = types.ModuleType("sqlalchemy.orm")
    sqlalchemy_orm.sessionmaker = lambda *args, **kwargs: None
    sys.modules["sqlalchemy"] = sqlalchemy
    sys.modules["sqlalchemy.ext"] = sqlalchemy_ext
    sys.modules["sqlalchemy.ext.declarative"] = sqlalchemy_declarative
    sys.modules["sqlalchemy.orm"] = sqlalchemy_orm

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

if "git" not in sys.modules:
    git = types.ModuleType("git")
    git.Repo = object
    git_exc = types.ModuleType("git.exc")
    git_exc.GitCommandError = Exception
    sys.modules["git"] = git
    sys.modules["git.exc"] = git_exc

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api.accounts as accounts_module
except ImportError:
    FastAPI = None
    TestClient = None
    accounts_module = None


AUTH_HEADERS = {"Authorization": "Bearer webchat2api"}


class FakeRemoteAccountConfig:
    def __init__(self) -> None:
        self.sources = []
        self.deleted_ids = []

    def list_sources(self):
        return list(self.sources)

    def add_source(self, **values):
        source = {"id": "source-1", "import_job": None, **values}
        self.sources.append(source)
        return source

    def get_source(self, source_id):
        for source in self.sources:
            if source["id"] == source_id:
                return source
        return None

    def update_source(self, source_id, updates):
        source = self.get_source(source_id)
        if source is None:
            return None
        source.update(updates)
        return source

    def delete_source(self, source_id):
        before = len(self.sources)
        self.sources = [source for source in self.sources if source["id"] != source_id]
        return len(self.sources) < before


class FakeRemoteAccountImportService:
    def __init__(self) -> None:
        self.inject_calls = []
        self.sync_calls = []

    def inject_payload(self, payload, **kwargs):
        self.inject_calls.append((payload, kwargs))
        return {
            "strategy": kwargs.get("strategy", "merge"),
            "source_id": kwargs.get("source_id", ""),
            "source_name": kwargs.get("source_name", ""),
            "total": 1,
            "added": 1,
            "skipped": 0,
            "removed": 0,
        }

    def sync_source(self, source, config):
        self.sync_calls.append((source, config))
        if source.get("fail"):
            raise RuntimeError("secret-token bearer-secret account-secret-token raw response body")
        return {"status": "success", "total": 1, "added": 1, "skipped": 0, "removed": 0, "failed": 0, "errors": []}


@unittest.skipIf(accounts_module is None, "fastapi is not installed")
class RemoteAccountApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = FakeRemoteAccountConfig()
        self.import_service = FakeRemoteAccountImportService()
        self.patchers = [
            mock.patch.object(accounts_module, "remote_account_config", self.config),
            mock.patch.object(accounts_module, "remote_account_import_service", self.import_service),
            mock.patch.object(accounts_module, "require_admin", lambda authorization: {"role": "admin"}),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(accounts_module.create_router())
        self.client = TestClient(app)

    def test_create_source_sanitizes_secrets(self) -> None:
        response = self.client.post(
            "/api/remote-account/sources",
            headers=AUTH_HEADERS,
            json={
                "name": "Remote",
                "url": "https://example.test/accounts",
                "auth_token": "secret-token",
                "bearer_token": "bearer-secret",
                "provider": "grok",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("auth_token", body["source"])
        self.assertNotIn("bearer_token", body["source"])
        self.assertTrue(body["source"]["has_auth_token"])
        self.assertTrue(body["source"]["has_bearer_token"])
        self.assertEqual(body["source"]["provider"], "grok")

    def test_direct_inject_passes_payload_and_strategy(self) -> None:
        response = self.client.post(
            "/api/remote-account/inject",
            headers=AUTH_HEADERS,
            json={
                "tokens": ["token-1"],
                "strategy": "merge",
                "source_id": "manual",
                "source_name": "Manual",
                "provider": "gpt",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload, kwargs = self.import_service.inject_calls[0]
        self.assertEqual(payload, {"tokens": ["token-1"]})
        body = response.json()
        body_text = response.text
        self.assertNotIn("items", body)
        self.assertNotIn("access_token", body_text)
        self.assertNotIn("token-1", body_text)
        self.assertNotIn("auth_token", body_text)
        self.assertNotIn("bearer_token", body_text)
        self.assertEqual(kwargs["strategy"], "merge")
        self.assertEqual(kwargs["source_id"], "manual")
        self.assertEqual(kwargs["source_name"], "Manual")
        self.assertEqual(kwargs["provider_default"], "gpt")


    def test_sync_source_generic_exception_response_is_sanitized(self) -> None:
        self.config.sources.append({
            "id": "source-1",
            "name": "Remote",
            "url": "https://example.test/accounts",
            "auth_token": "secret-token",
            "bearer_token": "bearer-secret",
            "fail": True,
        })

        response = self.client.post("/api/remote-account/sources/source-1/sync", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 502)
        body_text = response.text
        self.assertIn("remote account sync failed", body_text)
        self.assertNotIn("secret-token", body_text)
        self.assertNotIn("bearer-secret", body_text)
        self.assertNotIn("account-secret-token", body_text)
        self.assertNotIn("raw response body", body_text)

    def test_sync_source_uses_configured_source(self) -> None:
        self.config.sources.append({"id": "source-1", "name": "Remote", "url": "https://example.test/accounts"})

        response = self.client.post("/api/remote-account/sources/source-1/sync", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200)
        body_text = response.text
        self.assertNotIn("access_token", body_text)
        self.assertNotIn("submitted-token", body_text)
        self.assertNotIn("auth_token", body_text)
        self.assertNotIn("bearer_token", body_text)
        source, config = self.import_service.sync_calls[0]
        self.assertEqual(source["id"], "source-1")
        self.assertIs(config, self.config)


if __name__ == "__main__":
    unittest.main()
