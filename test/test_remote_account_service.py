from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any

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


if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: object = None) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi.HTTPException = HTTPException
    fastapi.Request = object
    fastapi.FastAPI = object
    fastapi.APIRouter = lambda *args, **kwargs: None
    fastapi.Header = lambda default=None, **kwargs: default
    fastapi_concurrency = types.ModuleType("fastapi.concurrency")
    fastapi_concurrency.run_in_threadpool = lambda func, *args, **kwargs: func(*args, **kwargs)
    fastapi_responses = types.ModuleType("fastapi.responses")
    fastapi_responses.JSONResponse = object
    fastapi_responses.StreamingResponse = object
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.concurrency"] = fastapi_concurrency
    sys.modules["fastapi.responses"] = fastapi_responses

if "git" not in sys.modules:
    git = types.ModuleType("git")
    git.Repo = object
    git_exc = types.ModuleType("git.exc")
    git_exc.GitCommandError = Exception
    sys.modules["git"] = git
    sys.modules["git.exc"] = git_exc

def sanitize_remote_account_source(source: dict | None) -> dict | None:
    if not isinstance(source, dict):
        return None
    sanitized = {key: value for key, value in source.items() if key not in {"auth_token", "bearer_token"}}
    sanitized["has_auth_token"] = bool(str(source.get("auth_token") or "").strip())
    sanitized["has_bearer_token"] = bool(str(source.get("bearer_token") or "").strip())
    return sanitized

from services.account_service import AccountService
import services.account_service as account_service_module
from services.models import GROK_PROVIDER, GPT_PROVIDER
from services.remote_account_service import (
    RemoteAccountConfig,
    RemoteAccountService,
    normalize_remote_account_payload,
)

account_service_module.log_service.add = lambda *args, **kwargs: None


class MemoryStorage:
    def __init__(self, accounts: list[dict[str, Any]] | None = None) -> None:
        self.accounts = list(accounts or [])

    def load_accounts(self) -> list[dict[str, Any]]:
        return list(self.accounts)

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        self.accounts = list(accounts)

    def load_auth_keys(self) -> list[dict[str, Any]]:
        return []

    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        pass

    def load_settings(self) -> dict[str, Any]:
        return {}

    def save_settings(self, settings: dict[str, Any]) -> None:
        pass

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def get_backend_info(self) -> dict[str, Any]:
        return {"type": "memory"}


class FailingReplaceStorage(MemoryStorage):
    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        raise RuntimeError("account-secret-token secret-token bearer-secret")


class RemoteAccountServiceTests(unittest.TestCase):
    def test_normalizes_gpt_token_strings_and_grok_account_dicts(self) -> None:
        gpt_accounts = normalize_remote_account_payload({"tokens": [" gpt-token ", ""]})
        grok_accounts = normalize_remote_account_payload(
            {"accounts": [{"token": "grok-token", "provider": "grok", "type": "basic", "metadata": {"team": "x"}}]},
            provider_default=GPT_PROVIDER,
            source_id="source-1",
            source_name="Remote",
            injected_at="2026-01-01T00:00:00+00:00",
        )

        self.assertEqual(gpt_accounts, [{"access_token": "gpt-token", "provider": GPT_PROVIDER, "remote_injected_at": gpt_accounts[0]["remote_injected_at"]}])
        self.assertEqual(grok_accounts[0]["access_token"], "grok-token")
        self.assertEqual(grok_accounts[0]["provider"], GROK_PROVIDER)
        self.assertEqual(grok_accounts[0]["type"], "basic")
        self.assertEqual(grok_accounts[0]["metadata"], {"team": "x"})
        self.assertEqual(grok_accounts[0]["remote_source_id"], "source-1")

    def test_merge_preserves_existing_mutable_fields_when_omitted(self) -> None:
        account_pool = AccountService(MemoryStorage([
            {
                "access_token": "token-1",
                "provider": "gpt",
                "status": "限流",
                "quota": 7,
                "success": 3,
                "fail": 2,
                "last_used_at": "2026-01-01 00:00:00",
                "restore_at": "2026-01-02 00:00:00",
            }
        ]))
        service = RemoteAccountService(account_pool)

        result = service.inject_payload({"accounts": [{"access_token": "token-1", "provider": "gpt", "type": "plus"}]}, source_id="remote-1")
        account = account_pool.get_account("token-1")

        self.assertEqual(result["skipped"], 1)
        self.assertNotIn("items", result)
        self.assertNotIn("access_token", result)
        self.assertNotIn("token-1", repr(result))
        self.assertEqual(account["status"], "限流")
        self.assertEqual(account["quota"], 7)
        self.assertEqual(account["success"], 3)
        self.assertEqual(account["fail"], 2)
        self.assertEqual(account["last_used_at"], "2026-01-01 00:00:00")
        self.assertEqual(account["restore_at"], "2026-01-02 00:00:00")
        self.assertEqual(account["type"], "plus")
        self.assertEqual(account["remote_source_id"], "remote-1")

    def test_replace_only_removes_same_source_and_rejects_empty_payload(self) -> None:
        account_pool = AccountService(MemoryStorage([
            {"access_token": "old-same", "provider": "gpt", "remote_source_id": "source-1"},
            {"access_token": "old-other", "provider": "gpt", "remote_source_id": "source-2"},
            {"access_token": "manual", "provider": "gpt"},
        ]))
        service = RemoteAccountService(account_pool)

        with self.assertRaises(ValueError):
            service.inject_payload({"tokens": []}, strategy="replace", source_id="source-1")

        result = service.inject_payload({"tokens": ["new-same"]}, strategy="replace", source_id="source-1")
        tokens = {account["access_token"] for account in account_pool.list_accounts()}

        self.assertEqual(result["removed"], 1)
        self.assertNotIn("items", result)
        self.assertNotIn("new-same", repr(result))
        self.assertNotIn("old-same", tokens)
        self.assertIn("old-other", tokens)
        self.assertIn("manual", tokens)
        self.assertIn("new-same", tokens)



    def test_replace_add_failure_keeps_existing_source_accounts(self) -> None:
        account_pool = AccountService(FailingReplaceStorage([
            {"access_token": "old-same", "provider": "gpt", "remote_source_id": "source-1"},
            {"access_token": "other", "provider": "gpt", "remote_source_id": "source-2"},
        ]))
        service = RemoteAccountService(account_pool)

        with self.assertRaises(RuntimeError):
            service.inject_payload({"tokens": ["account-secret-token"]}, strategy="replace", source_id="source-1")

        tokens = {account["access_token"] for account in account_pool.list_accounts()}
        self.assertIn("old-same", tokens)
        self.assertIn("other", tokens)
        self.assertNotIn("account-secret-token", tokens)

    def test_sync_source_persists_sanitized_error_and_raises_generic_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = RemoteAccountConfig(Path(temp_dir) / "remote_sources.json")
            source = config.add_source(
                name="Remote",
                url="https://example.test/accounts?token=secret-token",
                auth_header="X-Auth",
                auth_token="secret-token",
                bearer_token="bearer-secret",
            )
            service = RemoteAccountService(AccountService(MemoryStorage()))

            def fail_fetch(_source):
                raise RuntimeError("secret-token bearer-secret account-secret-token response body")

            service.fetch_source_payload = fail_fetch
            with self.assertRaisesRegex(RuntimeError, "remote account sync failed"):
                service.sync_source(source, config)

            job = config.get_source(source["id"])["import_job"]
            job_text = repr(job)
            self.assertEqual(job["errors"], ["remote account sync failed"])
            self.assertNotIn("secret-token", job_text)
            self.assertNotIn("bearer-secret", job_text)
            self.assertNotIn("account-secret-token", job_text)

    def test_inject_payload_result_is_count_only_without_raw_tokens(self) -> None:
        account_pool = AccountService(MemoryStorage())
        service = RemoteAccountService(account_pool)

        result = service.inject_payload(
            {"accounts": [{"access_token": "raw-secret-token", "provider": "gpt"}]},
            strategy="merge",
            source_id="source-1",
            source_name="Remote",
        )

        self.assertEqual(result, {
            "strategy": "merge",
            "source_id": "source-1",
            "source_name": "Remote",
            "total": 1,
            "added": 1,
            "skipped": 0,
            "removed": 0,
        })
        self.assertNotIn("access_token", repr(result))
        self.assertNotIn("raw-secret-token", repr(result))

    def test_source_config_persistence_and_sanitization_hide_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_file = Path(temp_dir) / "remote_sources.json"
            config = RemoteAccountConfig(store_file)
            source = config.add_source(
                name="Remote",
                enabled=True,
                url="https://example.test/accounts",
                method="POST",
                auth_header="X-Token",
                auth_token="secret-token",
                bearer_token="bearer-secret",
                provider="grok",
                sync_strategy="replace",
                interval_seconds=60,
            )
            reloaded = RemoteAccountConfig(store_file)
            [stored] = reloaded.list_sources()
            sanitized = sanitize_remote_account_source(source)

        self.assertEqual(stored["name"], "Remote")
        self.assertEqual(stored["method"], "POST")
        self.assertEqual(stored["provider"], GROK_PROVIDER)
        self.assertEqual(stored["sync_strategy"], "replace")
        self.assertEqual(stored["interval_seconds"], 60)
        self.assertNotIn("auth_token", sanitized)
        self.assertNotIn("bearer_token", sanitized)
        self.assertTrue(sanitized["has_auth_token"])
        self.assertTrue(sanitized["has_bearer_token"])


if __name__ == "__main__":
    unittest.main()
