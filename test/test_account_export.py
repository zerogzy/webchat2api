import base64
import importlib.util
import json
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
    sys.modules.setdefault("fastapi", fastapi)
else:
    fastapi = sys.modules["fastapi"]
fastapi.APIRouter = getattr(fastapi, "APIRouter", lambda *args, **kwargs: None)
fastapi.FastAPI = getattr(fastapi, "FastAPI", lambda *args, **kwargs: None)
fastapi.Header = getattr(fastapi, "Header", lambda default=None, **kwargs: default)
fastapi.HTTPException = getattr(fastapi, "HTTPException", type("HTTPException", (Exception,), {"__init__": lambda self, status_code=500, detail=None, **kwargs: (Exception.__init__(self, detail), setattr(self, "status_code", status_code), setattr(self, "detail", detail), setattr(self, "headers", kwargs.get("headers")))[0]}))
fastapi_concurrency = sys.modules.get("fastapi.concurrency") or types.ModuleType("fastapi.concurrency")
fastapi_concurrency.run_in_threadpool = getattr(fastapi_concurrency, "run_in_threadpool", lambda func, *args, **kwargs: func(*args, **kwargs))
fastapi_responses = sys.modules.get("fastapi.responses") or types.ModuleType("fastapi.responses")
fastapi_responses.JSONResponse = getattr(fastapi_responses, "JSONResponse", object)
fastapi_responses.StreamingResponse = getattr(fastapi_responses, "StreamingResponse", object)
fastapi_responses.Response = getattr(fastapi_responses, "Response", lambda *args, **kwargs: {"args": args, "kwargs": kwargs})
sys.modules.setdefault("fastapi.concurrency", fastapi_concurrency)
sys.modules.setdefault("fastapi.responses", fastapi_responses)

if "pydantic" not in sys.modules:
    pydantic = types.ModuleType("pydantic")

    class BaseModelStub:
        pass

    pydantic.BaseModel = BaseModelStub
    pydantic.ConfigDict = lambda **kwargs: dict(kwargs)
    pydantic.Field = lambda default=..., default_factory=None, **kwargs: default_factory() if default_factory else default
    sys.modules["pydantic"] = pydantic

from services.account_service import AccountService
import services.account_service as account_service_module
from services.models import GROK_PROVIDER, GPT_PROVIDER

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


def make_jwt(payload: dict[str, Any]) -> str:
    def encode(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f'{encode({"alg": "none", "typ": "JWT"})}.{encode(payload)}.sig'


class AccountExportTests(unittest.TestCase):
    def test_build_export_items_uses_codex_shape_and_jwt_claims(self) -> None:
        access_token = make_jwt(
            {
                "exp": 0,
                "iat": 3600,
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"},
                "https://api.openai.com/profile": {"email": "test@example.com"},
            }
        )
        id_token = make_jwt({"email": "fallback@example.com"})
        service = AccountService(
            MemoryStorage(
                [
                    {
                        "access_token": access_token,
                        "id_token": id_token,
                        "refresh_token": "rt_test",
                    }
                ]
            )
        )

        [item] = service.build_export_items([access_token])

        self.assertEqual(item["type"], "codex")
        self.assertEqual(item["email"], "test@example.com")
        self.assertEqual(item["expired"], "1970-01-01T08:00:00+08:00")
        self.assertEqual(item["account_id"], "acct_123")
        self.assertEqual(item["access_token"], access_token)
        self.assertEqual(item["last_refresh"], "1970-01-01T09:00:00+08:00")
        self.assertEqual(item["id_token"], id_token)
        self.assertEqual(item["refresh_token"], "rt_test")

    def test_build_export_items_includes_access_token_only_accounts(self) -> None:
        complete_access_token = make_jwt({"exp": 0})
        complete_id_token = make_jwt({"email": "complete@example.com"})
        service = AccountService(
            MemoryStorage(
                [
                    {"access_token": "only_access"},
                    {"access_token": "missing_id", "refresh_token": "rt_missing_id"},
                    {"access_token": complete_access_token, "id_token": complete_id_token, "refresh_token": "rt_complete"},
                ]
            )
        )

        items = service.build_export_items()

        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["access_token"], "only_access")
        self.assertEqual(items[0]["id_token"], "")
        self.assertEqual(items[0]["refresh_token"], "")
        self.assertEqual(items[1]["access_token"], "missing_id")
        self.assertEqual(items[1]["refresh_token"], "rt_missing_id")
        self.assertEqual(items[2]["access_token"], complete_access_token)
        self.assertEqual(items[2]["id_token"], complete_id_token)
        self.assertEqual(items[2]["refresh_token"], "rt_complete")

    def test_build_export_items_filters_by_provider_and_tokens(self) -> None:
        service = AccountService(
            MemoryStorage(
                [
                    {"access_token": "gpt-token", "provider": GPT_PROVIDER, "id_token": "gpt-id", "refresh_token": "gpt-rt"},
                    {"access_token": "grok-token", "provider": GROK_PROVIDER, "id_token": "grok-id", "refresh_token": "grok-rt"},
                ]
            )
        )

        gpt_items = service.build_export_items(provider=GPT_PROVIDER)
        grok_items = service.build_export_items(provider=GROK_PROVIDER)
        intersected_items = service.build_export_items(["gpt-token", "grok-token"], provider=GROK_PROVIDER)

        self.assertEqual([item["access_token"] for item in gpt_items], ["gpt-token"])
        self.assertEqual([item["access_token"] for item in grok_items], ["grok-token"])
        self.assertEqual([item["access_token"] for item in intersected_items], ["grok-token"])
        self.assertEqual(intersected_items[0]["id_token"], "grok-id")
        self.assertEqual(intersected_items[0]["refresh_token"], "grok-rt")

    def test_account_txt_content_is_line_oriented_and_keeps_tokens(self) -> None:
        content = AccountService.build_export_text(
            [
                {"access_token": " access-token ", "sso": "ignored-sso"},
                {"access_token": "", "sso": " sso-token "},
                {"access_token": "   ", "sso": ""},
            ]
        )

        self.assertEqual(content, "access-token\nsso-token\n")

    def test_export_filename_for_provider(self) -> None:
        api_stub = types.ModuleType("api")
        api_stub.__path__ = []
        support_stub = types.ModuleType("api.support")
        for name in (
            "require_admin",
            "sanitize_cpa_pool",
            "sanitize_cpa_pools",
            "sanitize_remote_account_source",
            "sanitize_remote_account_sources",
            "sanitize_sub2api_server",
            "sanitize_sub2api_servers",
        ):
            setattr(support_stub, name, lambda value=None, *args, **kwargs: value)
        auth_stub = types.ModuleType("services.auth_service")
        auth_stub.auth_service = object()
        cpa_stub = types.ModuleType("services.cpa_service")
        cpa_stub.cpa_config = object()
        cpa_stub.cpa_import_service = object()
        cpa_stub.list_remote_files = lambda *args, **kwargs: []
        remote_account_stub = types.ModuleType("services.remote_account_service")
        remote_account_stub.remote_account_config = object()
        remote_account_stub.remote_account_import_service = object()
        sub2api_stub = types.ModuleType("services.sub2api_service")
        sub2api_stub.list_remote_accounts = lambda *args, **kwargs: []
        sub2api_stub.list_remote_groups = lambda *args, **kwargs: []
        sub2api_stub.sub2api_config = object()
        sub2api_stub.sub2api_import_service = object()
        sys.modules.setdefault("api", api_stub)
        sys.modules.setdefault("api.support", support_stub)
        sys.modules.setdefault("services.auth_service", auth_stub)
        sys.modules.setdefault("services.cpa_service", cpa_stub)
        sys.modules.setdefault("services.remote_account_service", remote_account_stub)
        sys.modules.setdefault("services.sub2api_service", sub2api_stub)

        spec = importlib.util.spec_from_file_location("accounts_api_module", "api/accounts.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        accounts_api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(accounts_api)

        self.assertEqual(accounts_api._export_filename("gpt"), "webchat2api-gpt.txt")
        self.assertEqual(accounts_api._export_filename("grok"), "webchat2api_grok.txt")

    def test_delete_limited_accounts_removes_exact_limited_status(self) -> None:
        service = AccountService(
            MemoryStorage(
                [
                    {"access_token": "limited_gpt", "provider": "gpt", "status": "限流"},
                    {"access_token": "limited_grok", "provider": "grok", "status": "限流"},
                    {"access_token": "normal", "provider": "gpt", "status": "正常"},
                    {"access_token": "disabled", "provider": "grok", "status": "禁用"},
                ]
            )
        )

        result = service.delete_limited_accounts()

        self.assertEqual(result["removed"], 2)
        self.assertEqual([item["access_token"] for item in result["items"]], ["normal", "disabled"])

    def test_add_account_items_preserves_export_fields_without_overwriting_plan_type(self) -> None:
        service = AccountService(MemoryStorage())

        result = service.add_account_items(
            [
                {
                    "type": "codex",
                    "access_token": "access_token_test",
                    "refresh_token": "rt_test",
                    "account_id": "acct_123",
                }
            ]
        )

        account = service.get_account("access_token_test")
        self.assertEqual(result["added"], 1)
        self.assertIsNotNone(account)
        self.assertEqual(account["type"], "free")
        self.assertEqual(account["export_type"], "codex")
        self.assertEqual(account["refresh_token"], "rt_test")
        self.assertEqual(account["account_id"], "acct_123")


if __name__ == "__main__":
    unittest.main()
