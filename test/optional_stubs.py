from __future__ import annotations

import base64
import io
import json
import os
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Coroutine, get_type_hints, cast
from urllib.parse import parse_qs, urlparse


_OPTIONAL_TEST_STUBS: list[str] = []


def _install_module(name: str, module: types.ModuleType) -> None:
    sys.modules[name] = module
    _OPTIONAL_TEST_STUBS.append(name)


class StubHTTPException(Exception):
    def __init__(self, status_code: int, detail: object = None, **kwargs: object) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = kwargs.get("headers")


class StubUploadFile:
    def __init__(self, filename: str | None = None, content_type: str | None = None, file: Any = None) -> None:
        self.filename = filename
        self.content_type = content_type
        self.file = file

    async def read(self) -> bytes:
        if self.file is None:
            return b""
        data = self.file.read()
        return data if isinstance(data, bytes) else bytes(data)


    async def close(self) -> None:
        if self.file is not None and hasattr(self.file, "close"):
            self.file.close()


class StubHeader:
    def __init__(self, default: object = None, **kwargs: object) -> None:
        self.default = default
        self.alias = kwargs.get("alias")


class StubQuery:
    def __init__(self, default: object = None, **_: object) -> None:
        self.default = default


class StubFile:
    def __init__(self, default: object = None, **_: object) -> None:
        self.default = default


class StubBaseModel:
    def __init__(self, **data: object) -> None:
        annotations = getattr(type(self), "__annotations__", {})
        for cls in reversed(type(self).__mro__):
            annotations.update(getattr(cls, "__annotations__", {}))
        for name in annotations:
            default = getattr(type(self), name, None)
            if name in data:
                value = data[name]
            elif isinstance(default, StubFieldInfo):
                value = default.default_factory() if default.default_factory is not None else default.default
            else:
                value = default
            setattr(self, name, value)
        for name, value in data.items():
            if not hasattr(self, name):
                setattr(self, name, value)

    def model_dump(self, **_: object) -> dict[str, object]:
        return dict(self.__dict__)


class StubFieldInfo:
    def __init__(self, default: object = None, default_factory: Callable[[], object] | None = None) -> None:
        self.default = default
        self.default_factory = default_factory


def StubField(default: object = None, **kwargs: object) -> StubFieldInfo:
    default_factory = kwargs.get("default_factory")
    return StubFieldInfo(default, cast(Callable[[], object] | None, default_factory))


def StubConfigDict(**kwargs: object) -> dict[str, object]:
    return dict(kwargs)


class StubRequest:
    def __init__(self, json_data: object | None = None, form_data: object | None = None, headers: dict[str, str] | None = None) -> None:
        self._json_data = json_data
        self._form_data = form_data
        self.headers = headers or {}
        self.url = types.SimpleNamespace(scheme="http", netloc="testserver")

    async def json(self) -> object:
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data

    async def form(self) -> object:
        return self._form_data or {}


class StubAPIRouter:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Callable[..., object]] = {}

    def get(self, path: str, **_: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
        return self._register("GET", path)

    def post(self, path: str, **_: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
        return self._register("POST", path)

    def delete(self, path: str, **_: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
        return self._register("DELETE", path)

    def put(self, path: str, **_: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
        return self._register("PUT", path)

    def patch(self, path: str, **_: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
        return self._register("PATCH", path)

    def _register(self, method: str, path: str) -> Callable[[Callable[..., object]], Callable[..., object]]:
        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.routes[(method, path)] = func
            return func

        return decorator


class StubFormData:
    def __init__(self, items: list[tuple[str, object]]) -> None:
        self._items = items

    def multi_items(self) -> list[tuple[str, object]]:
        return list(self._items)

    def get(self, key: str, default: object = None) -> object:
        for item_key, value in self._items:
            if item_key == key:
                return value
        return default


class StubFastAPI:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Callable[..., object]] = {}

    def exception_handler(self, *_: object, **__: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            return func

        return decorator

    def include_router(self, router: StubAPIRouter) -> None:
        self.routes.update(router.routes)

    def add_middleware(self, *_: object, **__: object) -> None:
        pass


class StubCORSMiddleware:
    pass


class StubStaticFiles:
    def __init__(self, *_: object, **__: object) -> None:
        pass


class StubFileResponse:
    def __init__(self, path: str | Path, status_code: int = 200, **_: object) -> None:
        self.path = path
        self.status_code = status_code


class StubJSONResponse:
    def __init__(self, content: object = None, status_code: int = 200, **_: object) -> None:
        self.content = content
        self.status_code = status_code


class StubResponse:
    def __init__(self, content: object = None, status_code: int = 200, media_type: str | None = None, **_: object) -> None:
        self.content = content
        self.status_code = status_code
        self.media_type = media_type


class StubStreamingResponse:
    def __init__(self, content: object = None, status_code: int = 200, **_: object) -> None:
        self.content = content
        self.status_code = status_code


def _current_auth_key() -> str:
    try:
        from services.config import config

        return str(config.auth_key or "")
    except Exception:
        return ""


def _sync_auth_key(value: str) -> None:
    if not value:
        return
    if os.environ.get("WEBCHAT2API_AUTH_KEY") != value:
        os.environ["WEBCHAT2API_AUTH_KEY"] = value


class StubTestResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload

    def json(self) -> object:
        return self._payload


class StubTestClient:
    def __init__(self, app: StubFastAPI) -> None:
        self.app = app
        self.auth_key = "webchat2api"

    def get(self, path: str, headers: dict[str, str] | None = None) -> StubTestResponse:
        route_path, query = _split_path(path)
        request_headers = dict(headers or {})
        _sync_auth_key(self.auth_key)
        return self._request("GET", route_path, headers=request_headers, query=query)

    def post(self, path: str, headers: dict[str, str] | None = None, json: object | None = None, files: object | None = None, data: object | None = None) -> StubTestResponse:
        request_headers = dict(headers or {})
        if json is not None:
            request_headers.setdefault("content-type", "application/json")
        _sync_auth_key(self.auth_key)
        return self._request("POST", path, headers=request_headers, json_data=json, files=files, data=data)

    def _request(self, method: str, path: str, *, headers: dict[str, str], query: dict[str, list[str]] | None = None, json_data: object | None = None, files: object | None = None, data: object | None = None) -> StubTestResponse:
        route_path = _match_route_path(self.app.routes, method, path)
        func = self.app.routes[(method, route_path)]
        path_values = _extract_path_values(route_path, path)
        try:
            result = _call_route(func, headers=headers, query=query or {}, json_data=json_data, files=files, data=data, path_values=path_values)
        except StubHTTPException as exc:
            return StubTestResponse(exc.status_code, exc.detail)
        status = result.status_code if isinstance(result, StubJSONResponse) else 200
        payload = result.content if isinstance(result, StubJSONResponse) else result
        return StubTestResponse(status, payload)


def _split_path(path: str) -> tuple[str, dict[str, list[str]]]:
    parsed = urlparse(path)
    return parsed.path, parse_qs(parsed.query)


def _route_parts(path: str) -> list[str]:
    return [part for part in path.strip("/").split("/") if part]


def _match_route_path(routes: dict[tuple[str, str], Callable[..., object]], method: str, path: str) -> str:
    if (method, path) in routes:
        return path
    request_parts = _route_parts(path)
    for route_method, route_path in routes:
        if route_method != method:
            continue
        route_parts = _route_parts(route_path)
        if len(route_parts) != len(request_parts):
            continue
        if all(route_part.startswith("{") and route_part.endswith("}") or route_part == request_part for route_part, request_part in zip(route_parts, request_parts)):
            return route_path
    raise KeyError((method, path))


def _extract_path_values(route_path: str, path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for route_part, request_part in zip(_route_parts(route_path), _route_parts(path)):
        if route_part.startswith("{") and route_part.endswith("}"):
            values[route_part.strip("{}").split(":", 1)[0]] = request_part
    return values


def _call_route(func: Callable[..., object], *, headers: dict[str, str], query: dict[str, list[str]], json_data: object | None, files: object | None, data: object | None, path_values: dict[str, str] | None = None) -> object:
    import asyncio
    import inspect

    kwargs: dict[str, object] = {}
    annotations = get_type_hints(func)
    path_values = path_values or {}
    for name, parameter in inspect.signature(func).parameters.items():
        annotation = annotations.get(name, parameter.annotation)
        if name == "request" or annotation is StubRequest:
            kwargs[name] = StubRequest(json_data=json_data, form_data=_make_form(files, data), headers=headers)
        elif isinstance(annotation, type) and issubclass(annotation, StubBaseModel):
            kwargs[name] = annotation(**(json_data if isinstance(json_data, dict) else {}))
        elif name == "authorization":
            kwargs[name] = headers.get("Authorization")
        elif name in path_values:
            kwargs[name] = path_values[name]
        elif name in query:
            kwargs[name] = query[name][0]
        elif parameter.default is not inspect._empty:
            alias = getattr(parameter.default, "alias", None)
            if alias:
                kwargs[name] = headers.get(str(alias))
            elif hasattr(parameter.default, "default"):
                kwargs[name] = parameter.default.default
    result = func(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(cast(Coroutine[Any, Any, object], result))
    return result


def _make_form(files: object | None, data: object | None) -> StubFormData:
    form_items: list[tuple[str, object]] = []
    if isinstance(data, dict):
        form_items.extend(data.items())
    if isinstance(files, dict):
        for key, value in files.items():
            if isinstance(value, tuple):
                filename = value[0] if len(value) > 0 else key
                payload = value[1] if len(value) > 1 else b""
                content_type = value[2] if len(value) > 2 else None
                form_items.append((key, StubUploadFile(str(filename), content_type, io.BytesIO(payload))))
    elif isinstance(files, list):
        for entry in files:
            if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[1], tuple):
                key = str(entry[0])
                value = entry[1]
                filename = value[0] if len(value) > 0 else key
                payload = value[1] if len(value) > 1 else b""
                content_type = value[2] if len(value) > 2 else None
                form_items.append((key, StubUploadFile(str(filename), content_type, io.BytesIO(payload))))
    return StubFormData(form_items)


async def run_in_threadpool(func: Callable[..., object], *args: object, **kwargs: object) -> object:
    return func(*args, **kwargs)


def install_fastapi_stubs() -> None:
    existing = sys.modules.get("fastapi")
    if existing is not None:
        if getattr(existing, "__webchat2api_test_stub__", False):
            return
        if getattr(existing, "__file__", None):
            return
    else:
        try:
            __import__("fastapi")
            return
        except ImportError:
            pass

    fastapi = types.ModuleType("fastapi")
    setattr(fastapi, "__webchat2api_test_stub__", True)
    setattr(fastapi, "APIRouter", StubAPIRouter)
    setattr(fastapi, "FastAPI", StubFastAPI)
    setattr(fastapi, "File", StubFile)
    setattr(fastapi, "Header", StubHeader)
    setattr(fastapi, "HTTPException", StubHTTPException)
    setattr(fastapi, "Query", StubQuery)
    setattr(fastapi, "Request", StubRequest)
    setattr(fastapi, "UploadFile", StubUploadFile)

    concurrency = types.ModuleType("fastapi.concurrency")
    setattr(concurrency, "run_in_threadpool", run_in_threadpool)
    encoders = types.ModuleType("fastapi.encoders")
    setattr(encoders, "jsonable_encoder", lambda value: value)
    exceptions = types.ModuleType("fastapi.exceptions")
    setattr(exceptions, "RequestValidationError", type("RequestValidationError", (Exception,), {"errors": lambda self: []}))
    responses = types.ModuleType("fastapi.responses")
    setattr(responses, "FileResponse", StubFileResponse)
    setattr(responses, "JSONResponse", StubJSONResponse)
    setattr(responses, "Response", StubResponse)
    setattr(responses, "StreamingResponse", StubStreamingResponse)
    testclient = types.ModuleType("fastapi.testclient")
    setattr(testclient, "TestClient", StubTestClient)
    middleware = types.ModuleType("fastapi.middleware")
    cors = types.ModuleType("fastapi.middleware.cors")
    setattr(cors, "CORSMiddleware", StubCORSMiddleware)
    staticfiles = types.ModuleType("fastapi.staticfiles")
    setattr(staticfiles, "StaticFiles", StubStaticFiles)

    setattr(fastapi, "concurrency", concurrency)
    setattr(fastapi, "encoders", encoders)
    setattr(fastapi, "exceptions", exceptions)
    setattr(fastapi, "responses", responses)
    setattr(fastapi, "testclient", testclient)
    setattr(fastapi, "middleware", middleware)
    setattr(fastapi, "staticfiles", staticfiles)
    setattr(middleware, "cors", cors)

    for name, module in {
        "fastapi": fastapi,
        "fastapi.concurrency": concurrency,
        "fastapi.encoders": encoders,
        "fastapi.exceptions": exceptions,
        "fastapi.responses": responses,
        "fastapi.testclient": testclient,
        "fastapi.middleware": middleware,
        "fastapi.middleware.cors": cors,
        "fastapi.staticfiles": staticfiles,
    }.items():
        _install_module(name, module)

    loaded_log_service = sys.modules.get("services.log_service")
    if loaded_log_service is not None:
        setattr(loaded_log_service, "run_in_threadpool", run_in_threadpool)
    loaded_error_response = sys.modules.get("services.protocol.error_response")
    if loaded_error_response is not None:
        setattr(loaded_error_response, "JSONResponse", StubJSONResponse)


def install_curl_cffi_stub() -> None:
    if "curl_cffi" in sys.modules:
        return
    curl_cffi = types.ModuleType("curl_cffi")
    requests_module = types.ModuleType("curl_cffi.requests")
    setattr(requests_module, "Session", object)
    setattr(requests_module, "Response", object)
    setattr(requests_module, "exceptions", types.SimpleNamespace(RequestException=Exception))
    setattr(curl_cffi, "requests", requests_module)
    _install_module("curl_cffi", curl_cffi)
    _install_module("curl_cffi.requests", requests_module)


class StubImage:
    size = (2, 2)

    def __enter__(self) -> "StubImage":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def save(self, path: str | Path, format: str | None = None) -> None:
        Path(path).write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"))


class StubImageOpsModule:
    @staticmethod
    def exif_transpose(image: StubImage) -> StubImage:
        return image


class StubImageModule:
    @staticmethod
    def new(*_: object, **__: object) -> StubImage:
        return StubImage()

    @staticmethod
    def open(*_: object, **__: object) -> StubImage:
        return StubImage()


def install_pil_stub() -> None:
    if "PIL" in sys.modules:
        return
    pil = types.ModuleType("PIL")
    image_module = types.ModuleType("PIL.Image")
    setattr(image_module, "new", StubImageModule.new)
    setattr(image_module, "open", StubImageModule.open)
    image_ops_module = types.ModuleType("PIL.ImageOps")
    setattr(image_ops_module, "exif_transpose", StubImageOpsModule.exif_transpose)
    setattr(pil, "Image", image_module)
    setattr(pil, "ImageOps", image_ops_module)
    _install_module("PIL", pil)
    _install_module("PIL.Image", image_module)
    _install_module("PIL.ImageOps", image_ops_module)


def install_pydantic_stub() -> None:
    existing = sys.modules.get("pydantic")
    if existing is not None:
        if getattr(existing, "__webchat2api_test_stub__", False):
            return
        if getattr(existing, "__file__", None):
            return
    pydantic = types.ModuleType("pydantic")
    setattr(pydantic, "__webchat2api_test_stub__", True)
    setattr(pydantic, "BaseModel", StubBaseModel)
    setattr(pydantic, "ConfigDict", StubConfigDict)
    setattr(pydantic, "Field", StubField)
    _install_module("pydantic", pydantic)


def install_pybase64_stub() -> None:
    if "pybase64" in sys.modules:
        return
    pybase64 = types.ModuleType("pybase64")
    setattr(pybase64, "b64encode", base64.b64encode)
    setattr(pybase64, "b64decode", base64.b64decode)
    _install_module("pybase64", pybase64)


def install_starlette_stub() -> None:
    if "starlette" in sys.modules:
        return
    starlette = types.ModuleType("starlette")
    datastructures = types.ModuleType("starlette.datastructures")
    setattr(datastructures, "UploadFile", StubUploadFile)
    exceptions = types.ModuleType("starlette.exceptions")
    setattr(exceptions, "HTTPException", StubHTTPException)
    setattr(starlette, "datastructures", datastructures)
    setattr(starlette, "exceptions", exceptions)
    _install_module("starlette", starlette)
    _install_module("starlette.datastructures", datastructures)
    _install_module("starlette.exceptions", exceptions)


def install_tiktoken_stub() -> None:
    if "tiktoken" in sys.modules:
        return
    tiktoken = types.ModuleType("tiktoken")

    class FakeEncoding:
        def encode(self, text: str) -> list[str]:
            return list(text)

    setattr(tiktoken, "get_encoding", lambda name: FakeEncoding())
    setattr(tiktoken, "encoding_for_model", lambda model: FakeEncoding())
    _install_module("tiktoken", tiktoken)
