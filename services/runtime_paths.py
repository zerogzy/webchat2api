from __future__ import annotations

from pathlib import Path
import sys


def _source_base_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def resource_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass).resolve()
    return _source_base_dir()


def writable_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _source_base_dir()
