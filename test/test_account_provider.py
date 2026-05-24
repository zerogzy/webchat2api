from __future__ import annotations

import sys
import types
import unittest
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

if "git" not in sys.modules:
    git = types.ModuleType("git")
    git.Repo = object
    git_exc = types.ModuleType("git.exc")
    git_exc.GitCommandError = Exception
    sys.modules["git"] = git
    sys.modules["git.exc"] = git_exc

if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: object = None) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi.HTTPException = HTTPException
    sys.modules["fastapi"] = fastapi

if "fastapi.concurrency" not in sys.modules:
    fastapi_concurrency = types.ModuleType("fastapi.concurrency")
    fastapi_concurrency.run_in_threadpool = lambda func, *args, **kwargs: func(*args, **kwargs)
    sys.modules["fastapi.concurrency"] = fastapi_concurrency

if "fastapi.responses" not in sys.modules:
    fastapi_responses = types.ModuleType("fastapi.responses")
    fastapi_responses.JSONResponse = object
    fastapi_responses.StreamingResponse = object
    sys.modules["fastapi.responses"] = fastapi_responses

from services.account_service import AccountService
import services.account_service as account_service_module
from services.models import GROK_PROVIDER, GPT_PROVIDER, resolve_model

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

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def get_backend_info(self) -> dict[str, Any]:
        return {"type": "memory"}


class AccountProviderTests(unittest.TestCase):
    def test_normalizes_provider_without_replacing_plan_type(self) -> None:
        service = AccountService(MemoryStorage([{"access_token": "token-1", "type": "plus"}]))

        [account] = service.list_accounts()

        self.assertEqual(account["provider"], GPT_PROVIDER)
        self.assertEqual(account["type"], "plus")

    def test_selects_text_token_by_provider(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": "gpt-token", "type": "plus", "provider": "gpt"},
            {"access_token": "grok-token", "type": "basic", "provider": "grok"},
        ])

        self.assertEqual(service.get_text_access_token(provider=GPT_PROVIDER), "gpt-token")
        self.assertEqual(service.get_text_access_token(provider=GROK_PROVIDER), "grok-token")

    def test_grok_account_normalizes_simple_sso_cookie_token(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": " sso=grok-token ", "provider": "grok"},
        ])

        [account] = service.list_accounts()
        self.assertEqual(account["access_token"], "grok-token")
        self.assertEqual(service.get_text_access_token(provider=GROK_PROVIDER), "grok-token")

    def test_grok_account_normalizes_case_insensitive_spaced_sso_token(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": " SSO  =  grok-token ", "provider": "grok"},
        ])

        [account] = service.list_accounts()
        self.assertEqual(account["access_token"], "grok-token")
        self.assertEqual(service.get_text_access_token(provider=GROK_PROVIDER), "grok-token")

    def test_grok_account_preserves_spaced_sso_cookie_header_token(self) -> None:
        cookie_token = " SSO  =  grok-token ; other=value "
        service = AccountService(MemoryStorage([{"access_token": cookie_token, "provider": "grok"}]))

        [account] = service.list_accounts()
        self.assertEqual(account["access_token"], "SSO  =  grok-token ; other=value")

    def test_grok_account_preserves_full_cookie_header_token(self) -> None:
        cookie_token = " sso=grok-token ; other=value "
        service = AccountService(MemoryStorage([{"access_token": cookie_token, "provider": "grok"}]))

        [account] = service.list_accounts()
        self.assertEqual(account["access_token"], "sso=grok-token ; other=value")

    def test_grok_account_cloudflare_fields_are_optional_metadata(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": "grok-token", "provider": "grok"},
        ])

        [account] = service.list_accounts()
        self.assertEqual(account["access_token"], "grok-token")
        self.assertEqual(account["cf_cookies"], "")
        self.assertIsNone(account["user_agent"])
        self.assertEqual(service.get_text_access_token(provider=GROK_PROVIDER), "grok-token")

    def test_selects_grok_app_chat_token_by_tier_semantics(self) -> None:
        basic_service = AccountService(MemoryStorage())
        basic_service.add_account_items([
            {"access_token": "basic-token", "provider": "grok", "tier": "free"},
            {"access_token": "super-token", "provider": "grok", "tier": "premium"},
            {"access_token": "heavy-token", "provider": "grok", "tier": "heavy"},
        ])
        super_service = AccountService(MemoryStorage())
        super_service.add_account_items([
            {"access_token": "basic-token", "provider": "grok", "tier": "free"},
            {"access_token": "super-token", "provider": "grok", "tier": "premium"},
            {"access_token": "heavy-token", "provider": "grok", "tier": "heavy"},
        ])
        heavy_service = AccountService(MemoryStorage())
        heavy_service.add_account_items([
            {"access_token": "basic-token", "provider": "grok", "tier": "free"},
            {"access_token": "super-token", "provider": "grok", "tier": "premium"},
            {"access_token": "heavy-token", "provider": "grok", "tier": "heavy"},
        ])

        self.assertEqual(basic_service.get_grok_app_chat_access_token(resolve_model("grok-4.20-0309-non-reasoning")), "basic-token")
        self.assertEqual(super_service.get_grok_app_chat_access_token(resolve_model("grok-4.20-0309")), "super-token")
        self.assertEqual(heavy_service.get_grok_app_chat_access_token(resolve_model("grok-4.20-0309-heavy")), "heavy-token")

    def test_prefer_best_grok_app_chat_selection_tries_heavy_super_basic(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": "basic-token", "provider": "grok", "tier": "basic", "cf_cookies": "cf_bm=value", "user_agent": "Test UA"},
            {"access_token": "super-token", "provider": "grok", "tier": "super"},
        ])

        [account] = [item for item in service.list_accounts() if item["access_token"] == "basic-token"]
        self.assertEqual(account["cf_cookies"], "cf_bm=value")
        self.assertEqual(account["user_agent"], "Test UA")
        self.assertEqual(service.get_grok_app_chat_access_token(resolve_model("grok-4.20-fast")), "super-token")

    def test_grok_app_chat_selection_uses_capabilities_and_status(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": "disabled-heavy", "provider": "grok", "tier": "heavy", "status": "禁用"},
            {"access_token": "image-heavy", "provider": "grok", "tier": "heavy", "capabilities": ["image"]},
            {"access_token": "chat-heavy", "provider": "grok", "tier": "heavy", "capabilities": "chat,heavy"},
        ])

        self.assertEqual(service.get_grok_app_chat_access_token(resolve_model("grok-4.20-heavy")), "chat-heavy")

    def test_grok_app_chat_selection_falls_back_to_provider_round_robin(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": "gpt-token", "provider": "gpt"},
            {"access_token": "unknown-tier", "provider": "grok", "tier": "enterprise"},
            {"access_token": "plain-grok", "provider": "grok"},
        ])

        self.assertEqual(service.get_grok_app_chat_access_token(resolve_model("grok-4.20-0309-heavy")), "unknown-tier")
        self.assertEqual(service.get_grok_app_chat_access_token(resolve_model("grok-4.20-0309-heavy")), "plain-grok")

    def test_image_and_refresh_candidates_ignore_grok_accounts(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": "grok-token", "provider": "grok", "quota": 5, "status": "限流"},
            {"access_token": "gpt-token", "provider": "gpt", "quota": 5, "status": "正常"},
        ])

        self.assertEqual(service._list_ready_candidate_tokens(), ["gpt-token"])
        self.assertEqual(service.list_limited_tokens(), [])
        refresh_result = service.refresh_accounts(["grok-token"])
        self.assertEqual(refresh_result["refreshed"], 0)
        self.assertEqual(refresh_result["errors"], [])


if __name__ == "__main__":
    unittest.main()
