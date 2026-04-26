"""Idempotently load .env into os.environ.

Modules that read env vars (`METACULUS_API_TOKEN`, `ANTHROPIC_API_KEY`)
import this and call `load()` once before reading. Safe to call from
multiple places — python-dotenv's `load_dotenv` is itself idempotent.
"""
from __future__ import annotations

from pathlib import Path

_loaded = False


def load() -> None:
    global _loaded
    if _loaded:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _loaded = True  # don't retry on every call
        return
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env", override=False)
    _loaded = True
