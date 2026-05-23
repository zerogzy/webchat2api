"""Remote account source configuration and import/injection service."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from curl_cffi.requests import Session

from services.account_service import account_service as default_account_service
from services.config import DATA_DIR
from services.models import GPT_PROVIDER, normalize_provider


REMOTE_ACCOUNT_CONFIG_FILE = DATA_DIR / "remote_account_sources.json"
REMOTE_SOURCE_SECRET_FIELDS = {"auth_token", "bearer_token"}
REMOTE_ACCOUNT_SYNC_FAILED = "remote account sync failed"
REMOTE_ACCOUNT_IMPORT_FAILED = "remote account import failed"


REMOTE_ACCOUNT_MUTABLE_FIELDS = {
    "status",
    "quota",
    "image_quota_unknown",
    "limits_progress",
    "restore_at",
    "success",
    "fail",
    "last_used_at",
}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: object) -> str:
    return str(value or "").strip()


def _normalize_method(value: object) -> str:
    method = _clean(value).upper() or "GET"
    if method not in {"GET", "POST"}:
        raise ValueError("method must be GET or POST")
    return method


def _normalize_sync_strategy(value: object) -> str:
    strategy = _clean(value).lower() or "merge"
    if strategy not in {"merge", "replace"}:
        raise ValueError("sync_strategy must be merge or replace")
    return strategy


def _normalize_provider_default(value: object) -> str:
    provider = _clean(value)
    return normalize_provider(provider) if provider else ""


def _normalize_interval(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        interval = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("interval_seconds must be an integer") from exc
    if interval <= 0:
        raise ValueError("interval_seconds must be greater than 0")
    return interval


def _normalize_import_job(raw: object, *, fail_unfinished: bool) -> dict | None:
    if not isinstance(raw, dict):
        return None
    status = _clean(raw.get("status")) or "failed"
    if fail_unfinished and status in {"pending", "running"}:
        status = "failed"
    errors = raw.get("errors") if isinstance(raw.get("errors"), list) else []
    return {
        "job_id": _clean(raw.get("job_id")) or uuid.uuid4().hex,
        "status": status,
        "created_at": _clean(raw.get("created_at")) or _now_iso(),
        "updated_at": _clean(raw.get("updated_at")) or _clean(raw.get("created_at")) or _now_iso(),
        "source_id": _clean(raw.get("source_id")),
        "source_name": _clean(raw.get("source_name")),
        "strategy": _normalize_sync_strategy(raw.get("strategy")),
        "total": int(raw.get("total") or 0),
        "added": int(raw.get("added") or 0),
        "skipped": int(raw.get("skipped") or 0),
        "removed": int(raw.get("removed") or 0),
        "failed": int(raw.get("failed") or 0),
        "errors": [_clean(error) for error in errors if _clean(error)],
    }


def _normalize_source(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean(raw.get("id")) or _new_id(),
        "name": _clean(raw.get("name")),
        "enabled": bool(raw.get("enabled", True)),
        "url": _clean(raw.get("url")),
        "method": _normalize_method(raw.get("method")),
        "auth_header": _clean(raw.get("auth_header")),
        "auth_token": _clean(raw.get("auth_token")),
        "bearer_token": _clean(raw.get("bearer_token")),
        "provider": _normalize_provider_default(raw.get("provider")),
        "sync_strategy": _normalize_sync_strategy(raw.get("sync_strategy")),
        "interval_seconds": _normalize_interval(raw.get("interval_seconds")),
        "last_sync_at": _clean(raw.get("last_sync_at")),
        "import_job": _normalize_import_job(raw.get("import_job"), fail_unfinished=True),
    }


def _payload_items(payload: object) -> list[object]:
    if isinstance(payload, dict):
        if "accounts" in payload:
            accounts = payload.get("accounts")
            if not isinstance(accounts, list):
                raise ValueError("accounts must be a list")
            return accounts
        if "tokens" in payload:
            tokens = payload.get("tokens")
            if not isinstance(tokens, list):
                raise ValueError("tokens must be a list")
            return tokens
        raise ValueError("payload must contain accounts or tokens")
    if isinstance(payload, list):
        return payload
    raise ValueError("payload must be a list or wrapper object")


def _item_token(item: dict[str, Any]) -> str:
    return _clean(item.get("access_token") or item.get("token"))


def normalize_remote_account_payload(
    payload: object,
    *,
    provider_default: str = GPT_PROVIDER,
    source_id: str = "",
    source_name: str = "",
    injected_at: str | None = None,
) -> list[dict[str, Any]]:
    provider_default = normalize_provider(provider_default or GPT_PROVIDER)
    injected_at = injected_at or _now_iso()
    normalized: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()

    for item in _payload_items(payload):
        if isinstance(item, str):
            access_token = _clean(item)
            if not access_token or access_token in seen_tokens:
                continue
            account: dict[str, Any] = {"access_token": access_token, "provider": provider_default}
        elif isinstance(item, dict):
            access_token = _item_token(item)
            if not access_token or access_token in seen_tokens:
                continue
            account = {key: value for key, value in item.items() if key not in {"token", "accessToken"}}
            account["access_token"] = access_token
            account["provider"] = normalize_provider(item.get("provider") or provider_default)
        else:
            continue

        if source_id:
            account["remote_source_id"] = source_id
        if source_name:
            account["remote_source_name"] = source_name
        account["remote_injected_at"] = injected_at
        normalized.append(account)
        seen_tokens.add(access_token)

    return normalized


class RemoteAccountConfig:
    def __init__(self, store_file: Path):
        self._store_file = store_file
        self._lock = Lock()
        self._sources = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self._store_file.exists():
            return []
        try:
            raw = json.loads(self._store_file.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [_normalize_source(item) for item in raw if isinstance(item, dict)]
        except Exception:
            pass
        return []

    def _save(self) -> None:
        self._store_file.parent.mkdir(parents=True, exist_ok=True)
        self._store_file.write_text(json.dumps(self._sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def list_sources(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(source) for source in self._sources]

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        with self._lock:
            for source in self._sources:
                if source["id"] == source_id:
                    return dict(source)
        return None

    def add_source(self, **values: Any) -> dict[str, Any]:
        source = _normalize_source({"id": _new_id(), **values})
        with self._lock:
            self._sources.append(source)
            self._save()
        return dict(source)

    def update_source(self, source_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            for index, source in enumerate(self._sources):
                if source["id"] != source_id:
                    continue
                merged = {**source, **{key: value for key, value in updates.items() if value is not None}, "id": source_id}
                self._sources[index] = _normalize_source(merged)
                self._save()
                return dict(self._sources[index])
        return None

    def delete_source(self, source_id: str) -> bool:
        with self._lock:
            before = len(self._sources)
            self._sources = [source for source in self._sources if source["id"] != source_id]
            removed = len(self._sources) < before
            if removed:
                self._save()
            return removed

    def set_import_job(self, source_id: str, import_job: dict[str, Any] | None) -> dict[str, Any] | None:
        with self._lock:
            for index, source in enumerate(self._sources):
                if source["id"] != source_id:
                    continue
                next_source = dict(source)
                next_source["import_job"] = _normalize_import_job(import_job, fail_unfinished=False)
                if next_source["import_job"] and next_source["import_job"].get("status") == "success":
                    next_source["last_sync_at"] = next_source["import_job"].get("updated_at") or _now_iso()
                self._sources[index] = next_source
                self._save()
                return dict(next_source)
        return None


class RemoteAccountService:
    def __init__(self, account_pool: Any = None):
        self.account_pool = account_pool or default_account_service

    @staticmethod
    def _headers_for_source(source: dict[str, Any]) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        bearer_token = _clean(source.get("bearer_token"))
        auth_header = _clean(source.get("auth_header"))
        auth_token = _clean(source.get("auth_token"))
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        elif auth_header and auth_token:
            headers[auth_header] = auth_token
        return headers

    def fetch_source_payload(self, source: dict[str, Any]) -> object:
        url = _clean(source.get("url"))
        if not url:
            raise ValueError("url is required")
        session = Session()
        method = _normalize_method(source.get("method"))
        if method == "POST":
            response = session.post(url, headers=self._headers_for_source(source), json={})
        else:
            response = session.get(url, headers=self._headers_for_source(source))
        response.raise_for_status()
        return response.json()

    def inject_payload(self, payload: object, *, strategy: str = "merge", source_id: str = "", source_name: str = "", provider_default: str = GPT_PROVIDER, reject_empty_replace: bool = True) -> dict[str, Any]:
        strategy = _normalize_sync_strategy(strategy)
        accounts = normalize_remote_account_payload(payload, provider_default=provider_default, source_id=source_id, source_name=source_name)
        if strategy == "replace" and not source_id:
            raise ValueError("replace requires source_id")
        if strategy == "replace" and reject_empty_replace and not accounts:
            raise ValueError("replace requires a non-empty account payload")

        if strategy == "replace":
            result = self.account_pool.replace_account_items_for_remote_source(source_id, accounts)
            removed = int(result.get("removed") or 0)
        else:
            result = self.account_pool.add_account_items(accounts)
            removed = 0
        return {
            "strategy": strategy,
            "source_id": source_id,
            "source_name": source_name,
            "total": len(accounts),
            "added": int(result.get("added") or 0),
            "skipped": int(result.get("skipped") or 0),
            "removed": removed,
        }

    def sync_source(self, source: dict[str, Any], config: RemoteAccountConfig | None = None) -> dict[str, Any]:
        source_id = _clean(source.get("id"))
        source_name = _clean(source.get("name"))
        if not bool(source.get("enabled", True)):
            raise ValueError("source is disabled")
        if not _clean(source.get("url")):
            raise ValueError("url is required")

        now = _now_iso()
        job = {
            "job_id": uuid.uuid4().hex,
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "source_id": source_id,
            "source_name": source_name,
            "strategy": source.get("sync_strategy") or "merge",
            "total": 0,
            "added": 0,
            "skipped": 0,
            "removed": 0,
            "failed": 0,
            "errors": [],
        }
        if config is not None and source_id:
            config.set_import_job(source_id, job)

        try:
            payload = self.fetch_source_payload(source)
            result = self.inject_payload(
                payload,
                strategy=source.get("sync_strategy") or "merge",
                source_id=source_id,
                source_name=source_name,
                provider_default=source.get("provider") or GPT_PROVIDER,
            )
            job.update({
                "status": "success",
                "updated_at": _now_iso(),
                "total": result["total"],
                "added": result["added"],
                "skipped": result["skipped"],
                "removed": result["removed"],
            })
        except Exception as exc:
            job.update({"status": "failed", "updated_at": _now_iso(), "failed": 1, "errors": [REMOTE_ACCOUNT_SYNC_FAILED]})
            if config is not None and source_id:
                config.set_import_job(source_id, job)
            raise RuntimeError(REMOTE_ACCOUNT_SYNC_FAILED) from exc

        if config is not None and source_id:
            config.set_import_job(source_id, job)
        return job


remote_account_config = RemoteAccountConfig(REMOTE_ACCOUNT_CONFIG_FILE)
remote_account_import_service = RemoteAccountService()
