"""Protocol converters for OpenAI-compatible endpoints."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    openai_search: Any
    openai_v1_complete: Any

_LAZY_MODULES = {"openai_search", "openai_v1_complete"}


def __getattr__(name: str) -> Any:
    if name in _LAZY_MODULES:
        module = import_module(f"services.protocol.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ("openai_search", "openai_v1_complete")
