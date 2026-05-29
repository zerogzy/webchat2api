from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from threading import Lock, Thread
from typing import Any, Callable, Iterator

from fastapi import HTTPException

from services.protocol import gemini_native


@dataclass
class DeepResearchResult:
    id: str
    status: str
    query: str
    summary: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    model: str = "gemini-2.5-pro"
    error: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    completed_at: int | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "query": self.query,
            "summary": self.summary,
            "sources": list(self.sources),
            "steps": list(self.steps),
            "model": self.model,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
        }
CompletionTextFunc = Callable[[str, str], str]


def run_deep_research(body: dict[str, Any], completion_func: CompletionTextFunc | None = None) -> dict[str, Any]:
    query = _query(body)
    model = str(body.get("model") or "gemini-2.5-pro")
    result = DeepResearchResult(id=f"dr_{uuid.uuid4().hex}", status="in_progress", query=query, model=model)
    started = time.time()
    complete = completion_func or gemini_native.complete_text
    try:
        plan_text = complete(model, _plan_prompt(query))
        plan = _json_value(plan_text)
        questions = _questions(plan, query)
        result.steps.append({"type": "plan", "content": plan})
        findings: list[dict[str, Any]] = []
        for index, question in enumerate(questions, start=1):
            research_text = complete(model, _research_prompt(query, question))
            research = _json_value(research_text)
            sources = _sources(research)
            result.sources.extend(sources)
            step = {"type": "research", "index": index, "question": question, "content": research}
            result.steps.append(step)
            findings.append({"question": question, "research": research})
        synthesis = complete(model, _synthesis_prompt(query, findings))
        synthesis_data = _json_value(synthesis)
        result.summary = str(synthesis_data.get("summary") or synthesis).strip()
        result.status = "completed"
        result.completed_at = int(time.time())
        result.duration_ms = int((time.time() - started) * 1000)
        return result.to_dict()
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
        result.completed_at = int(time.time())
        result.duration_ms = int((time.time() - started) * 1000)
        if isinstance(exc, HTTPException):
            raise
        return result.to_dict()


def stream_deep_research(body: dict[str, Any], completion_func: CompletionTextFunc | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
    query = _query(body)
    model = str(body.get("model") or "gemini-2.5-pro")
    result_id = f"dr_{uuid.uuid4().hex}"
    yield "progress", {"id": result_id, "status": "in_progress", "query": query, "model": model}
    complete = completion_func or gemini_native.complete_text
    started = time.time()
    try:
        plan = _json_value(complete(model, _plan_prompt(query)))
        questions = _questions(plan, query)
        yield "step", {"id": result_id, "type": "plan", "content": plan}
        findings: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        steps = [{"type": "plan", "content": plan}]
        for index, question in enumerate(questions, start=1):
            research = _json_value(complete(model, _research_prompt(query, question)))
            step = {"type": "research", "index": index, "question": question, "content": research}
            steps.append(step)
            findings.append({"question": question, "research": research})
            yield "step", {"id": result_id, **step}
            for source in _sources(research):
                sources.append(source)
                yield "source", {"id": result_id, "source": source}
        synthesis = complete(model, _synthesis_prompt(query, findings))
        synthesis_data = _json_value(synthesis)
        result = DeepResearchResult(
            id=result_id,
            status="completed",
            query=query,
            summary=str(synthesis_data.get("summary") or synthesis).strip(),
            sources=sources,
            steps=steps,
            model=model,
            created_at=int(started),
            completed_at=int(time.time()),
            duration_ms=int((time.time() - started) * 1000),
        ).to_dict()
        yield "result", result
        yield "done", {"id": result_id, "status": "completed"}
    except Exception as exc:
        yield "error", {"id": result_id, "status": "failed", "error": str(exc)}
        yield "done", {"id": result_id, "status": "failed"}


class InteractionStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = Lock()
        self._items: dict[str, dict[str, Any]] = {}

    def create(self, body: dict[str, Any], completion_func: CompletionTextFunc | None = None, owner_id: str = "") -> dict[str, Any]:
        self.purge()
        query = _query(body)
        model = str(body.get("model") or "gemini-2.5-pro")
        task_id = f"int_{uuid.uuid4().hex}"
        item = {
            "id": task_id,
            "status": "in_progress",
            "query": query,
            "model": model,
            "owner_id": owner_id,
            "created_at": int(time.time()),
            "result": None,
        }
        with self._lock:
            self._items[task_id] = item
        Thread(target=self._run, args=(task_id, dict(body), completion_func), daemon=True).start()
        return self._public_item(item)

    def get(self, task_id: str, owner_id: str = "") -> dict[str, Any] | None:
        self.purge()
        with self._lock:
            item = self._items.get(task_id)
            if item is None:
                return None
            if owner_id and item.get("owner_id") not in {"", owner_id}:
                return None
            return self._public_item(item)

    @staticmethod
    def _public_item(item: dict[str, Any]) -> dict[str, Any]:
        public = dict(item)
        public.pop("owner_id", None)
        return public

    def purge(self) -> None:
        now = int(time.time())
        with self._lock:
            expired = [key for key, item in self._items.items() if now - int(item.get("created_at") or now) > self.ttl_seconds]
            for key in expired:
                self._items.pop(key, None)

    def _run(self, task_id: str, body: dict[str, Any], completion_func: CompletionTextFunc | None) -> None:
        result = run_deep_research(body, completion_func=completion_func)
        with self._lock:
            item = self._items.get(task_id)
            if item is None:
                return
            item["status"] = "completed" if result.get("status") == "completed" else "failed"
            item["result"] = result
            item["error"] = str(result.get("error") or "")
            item["completed_at"] = int(time.time())


interaction_store = InteractionStore()


def _query(body: dict[str, Any]) -> str:
    query = str(body.get("query") or body.get("prompt") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail={"error": "query is required"})
    return query


def _plan_prompt(query: str) -> str:
    return "Return JSON only with a questions array for researching this query: " + query


def _research_prompt(query: str, question: str) -> str:
    return "Return JSON only with summary and sources array for query " + json.dumps(query) + " subquestion " + json.dumps(question)


def _synthesis_prompt(query: str, findings: list[dict[str, Any]]) -> str:
    return "Return JSON only with summary for query " + json.dumps(query) + " using findings " + json.dumps(findings, ensure_ascii=False)


def _json_value(text: str) -> dict[str, Any]:
    stripped = _strip_fences(text).strip()
    decoder = json.JSONDecoder()
    start = stripped.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(stripped, start)
        except json.JSONDecodeError:
            start = stripped.find("{", start + 1)
            continue
        return value if isinstance(value, dict) else {"value": value}
    return {"summary": stripped}


def _questions(plan: dict[str, Any], query: str) -> list[str]:
    raw = plan.get("questions") or plan.get("subquestions") or plan.get("steps")
    if isinstance(raw, list):
        questions = [str(item.get("question") if isinstance(item, dict) else item).strip() for item in raw]
        questions = [item for item in questions if item]
        if questions:
            return questions[:5]
    return [query]


def _sources(research: dict[str, Any]) -> list[dict[str, Any]]:
    raw = research.get("sources")
    if not isinstance(raw, list):
        return []
    sources: list[dict[str, Any]] = []
    for source in raw:
        if isinstance(source, dict):
            sources.append(dict(source))
        elif isinstance(source, str):
            sources.append({"url": source})
    return sources


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        return "\n".join(lines[1:-1])
    return text
