from __future__ import annotations

import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch
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

if "tiktoken" not in sys.modules:
    tiktoken = types.ModuleType("tiktoken")
    tiktoken.encoding_for_model = lambda *args, **kwargs: types.SimpleNamespace(encode=lambda text: [])
    tiktoken.get_encoding = lambda *args, **kwargs: types.SimpleNamespace(encode=lambda text: [])
    sys.modules["tiktoken"] = tiktoken

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


class TestGrokConsoleError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_status = upstream_status


_MISSING = object()


@contextmanager
def patched_grok_validation(return_value: object = None, side_effect: Exception | None = None):
    module_name = "services.providers.grok"
    grok_module = types.ModuleType(module_name)
    validate = Mock(return_value=return_value, side_effect=side_effect)
    setattr(grok_module, "GrokConsoleError", TestGrokConsoleError)
    setattr(grok_module, "validate_grok_access_token", validate)
    previous_module = sys.modules.get(module_name, _MISSING)
    services_providers = sys.modules.get("services.providers")
    previous_attr = getattr(services_providers, "grok", _MISSING) if services_providers is not None else _MISSING
    sys.modules[module_name] = grok_module
    if services_providers is not None:
        setattr(services_providers, "grok", grok_module)
    try:
        yield validate
    finally:
        if previous_module is _MISSING:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
        if services_providers is not None:
            if previous_attr is _MISSING:
                try:
                    delattr(services_providers, "grok")
                except AttributeError:
                    pass
            else:
                setattr(services_providers, "grok", previous_attr)


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

    def test_grok_console_quota_normalizes_defaults_and_persists(self) -> None:
        storage = MemoryStorage([{"access_token": "grok-token", "provider": "grok"}])
        service = AccountService(storage)

        [account] = service.list_accounts()
        self.assertEqual(account["quota_console"], {
            "remaining": 30,
            "total": 30,
            "window_seconds": 900,
            "reset_at": None,
        })
        service.get_grok_console_access_token()
        [stored] = storage.accounts
        self.assertEqual(stored["quota_console"]["remaining"], 29)
        self.assertEqual(stored["quota"], 0)

    def test_grok_console_selection_skips_exhausted_until_reset(self) -> None:
        now = [1000.0]
        service = AccountService(MemoryStorage([
            {
                "access_token": "exhausted-token",
                "provider": "grok",
                "quota_console": {"remaining": 0, "total": 30, "window_seconds": 900, "reset_at": 1900},
            },
            {"access_token": "ready-token", "provider": "grok"},
        ]), now=lambda: now[0])

        self.assertEqual(service.get_grok_console_access_token(), "ready-token")
        now[0] = 1900.0
        self.assertEqual(service.get_grok_console_access_token(excluded_tokens={"ready-token"}), "exhausted-token")
        [reset_account] = [item for item in service.list_accounts() if item["access_token"] == "exhausted-token"]
        self.assertEqual(reset_account["quota_console"]["remaining"], 29)
        self.assertIsNone(reset_account["quota_console"]["reset_at"])

    def test_grok_console_selection_reserves_quota_and_sets_reset_window(self) -> None:
        now = [5000.0]
        service = AccountService(MemoryStorage([
            {
                "access_token": "grok-token",
                "provider": "grok",
                "quota_console": {"remaining": 1, "total": 30, "window_seconds": 900, "reset_at": None},
            }
        ]), now=lambda: now[0])

        self.assertEqual(service.get_grok_console_access_token(), "grok-token")

        [account] = service.list_accounts()
        self.assertEqual(account["quota_console"]["remaining"], 0)
        self.assertEqual(account["quota_console"]["reset_at"], 5900)
        self.assertEqual(service.get_grok_console_access_token(), "")

    def test_grok_console_selection_reserves_without_provider_mark(self) -> None:
        service = AccountService(MemoryStorage([
            {
                "access_token": "grok-token",
                "provider": "grok",
                "quota_console": {"remaining": 1, "total": 1, "window_seconds": 900, "reset_at": None},
            }
        ]))

        self.assertEqual(service.get_grok_console_access_token(), "grok-token")
        self.assertEqual(service.get_grok_console_access_token(), "")

        [account] = service.list_accounts()
        self.assertEqual(account["quota_console"]["remaining"], 0)

    def test_text_and_image_usage_do_not_consume_console_quota(self) -> None:
        storage = MemoryStorage([
            {"access_token": "grok-token", "provider": "grok"},
            {"access_token": "gpt-token", "provider": "gpt", "quota": 1},
        ])
        service = AccountService(storage)

        service.mark_text_used("grok-token")
        service.mark_image_result("gpt-token", success=True)

        grok_account = service.get_account("grok-token")
        gpt_account = service.get_account("gpt-token")
        self.assertEqual(grok_account["quota_console"]["remaining"], 30)
        self.assertNotIn("quota_console", gpt_account)
        self.assertEqual(gpt_account["quota"], 0)

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

    def test_refresh_accounts_attempts_grok_validation(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": "grok-token", "provider": "grok", "status": "正常"},
        ])

        with patched_grok_validation(return_value={"rateLimits": []}) as validate:
            refresh_result = service.refresh_accounts(["grok-token"])

        validate.assert_called_once()
        self.assertEqual(validate.call_args.args[0], "grok-token")
        self.assertEqual(refresh_result["refreshed"], 1)
        self.assertEqual(refresh_result["errors"], [])
        [account] = service.list_accounts()
        self.assertEqual(account["status"], "正常")
        self.assertTrue(account["app_chat"])

    def test_refresh_accounts_marks_invalid_grok_validation_abnormal(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": "grok-token", "provider": "grok", "status": "正常"},
        ])

        previous_auto_remove = account_service_module.config.data.get("auto_remove_invalid_accounts")
        account_service_module.config.data["auto_remove_invalid_accounts"] = False
        try:
            with patched_grok_validation(side_effect=TestGrokConsoleError("auth failed with sso=secret-cookie", 401, 401)):
                refresh_result = service.refresh_accounts(["grok-token"])
        finally:
            if previous_auto_remove is None:
                account_service_module.config.data.pop("auto_remove_invalid_accounts", None)
            else:
                account_service_module.config.data["auto_remove_invalid_accounts"] = previous_auto_remove

        self.assertEqual(refresh_result["refreshed"], 0)
        self.assertEqual(len(refresh_result["errors"]), 1)
        self.assertEqual(refresh_result["errors"][0]["error"], "Grok app-chat rate-limit validation failed")
        self.assertNotIn("secret-cookie", refresh_result["errors"][0]["error"])
        [account] = service.list_accounts()
        self.assertEqual(account["status"], "异常")
        self.assertEqual(account["quota"], 0)

    def test_refresh_accounts_keeps_valid_grok_validation_usable(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": "grok-token", "provider": "grok", "status": "异常"},
        ])

        with patched_grok_validation(return_value={"rateLimits": [{"remaining": 1}]}) as validate:
            refresh_result = service.refresh_accounts(["grok-token"])

        validate.assert_called_once()
        self.assertEqual(refresh_result["refreshed"], 1)
        self.assertEqual(refresh_result["errors"], [])
        [account] = service.list_accounts()
        self.assertEqual(account["status"], "正常")
        self.assertEqual(service.get_text_access_token(provider=GROK_PROVIDER), "grok-token")

    def test_image_and_refresh_candidates_do_not_use_grok_for_image(self) -> None:
        service = AccountService(MemoryStorage())
        service.add_account_items([
            {"access_token": "grok-token", "provider": "grok", "quota": 5, "status": "限流"},
            {"access_token": "gpt-token", "provider": "gpt", "quota": 5, "status": "正常"},
        ])

        self.assertEqual(service._list_ready_candidate_tokens(), ["gpt-token"])
        self.assertEqual(service.list_limited_tokens(), [])


if __name__ == "__main__":
    unittest.main()
