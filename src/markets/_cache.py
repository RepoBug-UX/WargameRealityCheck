from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "markets"
DEFAULT_TTL_SECONDS = 60 * 60  # 1h — fresh enough for an audit, cheap on the API


def _safe_key(key: str) -> str:
    # Filenames stay readable for the common case (slug-like keys) but
    # arbitrary strings still get a stable filesystem-safe form.
    if all(c.isalnum() or c in "-_." for c in key) and len(key) <= 100:
        return key
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def cache_path(namespace: str, key: str, base_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    return base_dir / namespace / f"{_safe_key(key)}.json"


def load(
    namespace: str,
    key: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    base_dir: Path = DEFAULT_CACHE_DIR,
) -> Any | None:
    p = cache_path(namespace, key, base_dir=base_dir)
    if not p.exists():
        return None
    age = time.time() - p.stat().st_mtime
    if age > ttl_seconds:
        return None
    with p.open() as f:
        return json.load(f)


def store(
    namespace: str,
    key: str,
    value: Any,
    *,
    base_dir: Path = DEFAULT_CACHE_DIR,
) -> None:
    p = cache_path(namespace, key, base_dir=base_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        json.dump(value, f, default=str)
