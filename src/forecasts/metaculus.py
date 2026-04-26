"""Metaculus client.

Pulls a binary forecasting question from Metaculus and normalizes it to a
`Forecast` record. Disk-cached to `data/forecasts/`.

Endpoints used (current as of early 2026):
  Question detail   GET https://www.metaculus.com/api/posts/{id}/
  Search            GET https://www.metaculus.com/api/posts/?search=...&forecast_type=binary&statuses=open

**Authentication required.** Metaculus moved to required-auth for their API
in late 2025. We gate all network calls behind `METACULUS_API_TOKEN` (env
var) and raise `MetaculusAuthRequired` if absent. The structure of the
sidecar pipeline still works without a token — it just emits "skipped, no
token" results, so the wider audit pipeline never breaks because Metaculus
isn't configured.

Get a token by signing up at https://www.metaculus.com and visiting
https://www.metaculus.com/aib/ for API access details.

Distinct from `src/markets/` deliberately. A `Forecast` is never
substitutable for a `Market` — the override CLI cannot pick a Forecast
candidate, the matcher does not return Forecast candidates, and Phase 6's
disagreement score never reads from `data/forecasts/`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import _cache, _env
from .types import Forecast, ForecastPoint

_env.load()

API_BASE = "https://www.metaculus.com/api"
HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MetaculusAuthRequired(RuntimeError):
    """Raised when METACULUS_API_TOKEN is not set."""


class MetaculusError(RuntimeError):
    pass


def _token() -> str:
    t = os.environ.get("METACULUS_API_TOKEN")
    if not t:
        raise MetaculusAuthRequired(
            "METACULUS_API_TOKEN is not set. Metaculus's API requires "
            "authentication; the sidecar will be skipped. Get a token at "
            "https://www.metaculus.com/aib/ and export METACULUS_API_TOKEN."
        )
    return t


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"Authorization": f"Token {_token()}"},
    )


_QUESTION_ID_FROM_URL = re.compile(r"/questions?/(\d+)")


def _coerce_question_id(question_id_or_url: str) -> str:
    """Accept a numeric id, a /questions/N/ URL, or a /api/posts/N/ URL."""
    s = str(question_id_or_url).strip()
    if s.isdigit():
        return s
    m = _QUESTION_ID_FROM_URL.search(s)
    if m:
        return m.group(1)
    raise ValueError(f"unrecognized Metaculus question id/URL: {question_id_or_url!r}")


def _extract_binary_question(post: dict[str, Any]) -> dict[str, Any] | None:
    """Metaculus posts can be single questions or grouped. Return the
    binary question payload, or None if this post isn't a binary question.
    """
    q = post.get("question")
    if isinstance(q, dict) and q.get("type") == "binary":
        return q
    # Group of questions — pick the first binary one we find.
    group = post.get("group_of_questions")
    if isinstance(group, dict):
        for child in group.get("questions", []) or []:
            if isinstance(child, dict) and child.get("type") == "binary":
                return child
    return None


def _community_prediction(question: dict[str, Any]) -> tuple[float | None, list[ForecastPoint]]:
    """Return (latest community prediction, history). Metaculus calls this
    `aggregations.recency_weighted` on the new schema."""
    aggregations = question.get("aggregations") or {}
    rw = aggregations.get("recency_weighted") or {}
    history_raw = rw.get("history") or []
    latest = rw.get("latest")

    points: list[ForecastPoint] = []
    for h in history_raw:
        ts_raw = h.get("start_time") or h.get("end_time")
        means = h.get("means") or h.get("centers")
        if ts_raw is None or not means:
            continue
        try:
            points.append(
                ForecastPoint(
                    ts=datetime.fromtimestamp(float(ts_raw), tz=timezone.utc),
                    community_prediction=float(means[0]),
                )
            )
        except (TypeError, ValueError):
            continue

    current: float | None = None
    if latest:
        means = latest.get("means") or latest.get("centers")
        if means:
            try:
                current = float(means[0])
            except (TypeError, ValueError):
                current = None
    if current is None and points:
        current = points[-1].community_prediction
    return current, points


def fetch_forecast(
    question_id_or_url: str,
    *,
    use_cache: bool = True,
    cache_ttl: int = 3600,
) -> Forecast:
    qid = _coerce_question_id(question_id_or_url)
    cached = (
        _cache.load("metaculus", qid, ttl_seconds=cache_ttl, base_dir=_cache.FORECASTS_CACHE_DIR)
        if use_cache
        else None
    )
    if cached:
        return Forecast.model_validate(cached)

    with _client() as client:
        r = client.get(f"{API_BASE}/posts/{qid}/")
        if r.status_code == 401 or r.status_code == 403:
            raise MetaculusAuthRequired(f"Metaculus auth rejected: {r.status_code} {r.text[:200]}")
        if r.status_code != 200:
            raise MetaculusError(f"Metaculus {r.status_code}: {r.text[:200]}")
        post = r.json()

    question = _extract_binary_question(post)
    if not question:
        raise MetaculusError(
            f"post {qid} is not a binary question; sidecar only handles binary forecasts"
        )

    current, history = _community_prediction(question)
    # Note: `current` may be None when the access tier doesn't expose
    # aggregations (default tier as of early 2026). We propagate None through
    # so downstream rendering can surface the "click through" caveat honestly
    # rather than silently fabricating a 0.5 placeholder.

    end_date_raw = (
        post.get("scheduled_close_time")
        or post.get("scheduled_resolve_time")
        or question.get("scheduled_close_time")
        or question.get("scheduled_resolve_time")
    )
    end_date = (
        datetime.fromisoformat(str(end_date_raw).replace("Z", "+00:00"))
        if end_date_raw else None
    )

    # nr_forecasters lives at the post level, not the question level (verified
    # against live API response — question-level value is always None).
    n_forecasters = post.get("nr_forecasters") or question.get("nr_forecasters")

    forecast = Forecast(
        question_text=str(post.get("title") or question.get("title") or "").strip(),
        source="metaculus",
        question_id=str(qid),
        resolution_criteria=question.get("resolution_criteria"),
        end_date=end_date,
        url=f"https://www.metaculus.com/questions/{qid}/",
        community_prediction=float(current) if current is not None else None,
        n_forecasters=n_forecasters,
        prediction_history=history,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    _cache.store(
        "metaculus", qid, forecast.model_dump(mode="json"), base_dir=_cache.FORECASTS_CACHE_DIR
    )
    return forecast


def search_forecasts(
    query_text: str, *, n_results: int = 5, use_cache: bool = True, cache_ttl: int = 3600
) -> list[dict[str, Any]]:
    """Search Metaculus for related binary forecasts. Returns lightweight
    summaries (id, title, url, current prediction) — call `fetch_forecast`
    on a chosen id to get the full record."""
    cache_key = f"search::{query_text}::{n_results}"
    cached = (
        _cache.load(
            "metaculus", cache_key, ttl_seconds=cache_ttl,
            base_dir=_cache.FORECASTS_CACHE_DIR,
        )
        if use_cache
        else None
    )
    if cached:
        return cached

    with _client() as client:
        r = client.get(
            f"{API_BASE}/posts/",
            params={
                "search": query_text,
                "forecast_type": "binary",
                "statuses": "open",
                "limit": n_results,
            },
        )
        if r.status_code in (401, 403):
            raise MetaculusAuthRequired(f"Metaculus auth rejected: {r.status_code}")
        if r.status_code != 200:
            raise MetaculusError(f"Metaculus search {r.status_code}: {r.text[:200]}")
        data = r.json()

    out: list[dict[str, Any]] = []
    for post in data.get("results", []) or []:
        question = _extract_binary_question(post)
        if not question:
            continue
        current, _ = _community_prediction(question)
        pid = post.get("id") or question.get("id") or ""
        out.append({
            "question_id": str(pid),
            "title": post.get("title") or question.get("title") or "",
            "url": f"https://www.metaculus.com/questions/{pid}/",
            "current_prediction": current,  # may be None at default access tier
            "nr_forecasters": post.get("nr_forecasters") or question.get("nr_forecasters"),
        })
    _cache.store("metaculus", cache_key, out, base_dir=_cache.FORECASTS_CACHE_DIR)
    return out


def auth_available() -> bool:
    return bool(os.environ.get("METACULUS_API_TOKEN"))


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.forecasts.metaculus")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_f = sub.add_parser("fetch")
    p_f.add_argument("question_id_or_url")
    p_f.add_argument("--no-cache", action="store_true")

    p_s = sub.add_parser("search")
    p_s.add_argument("text")
    p_s.add_argument("--n", type=int, default=5)

    args = p.parse_args()

    if not auth_available():
        print("METACULUS_API_TOKEN is not set.", file=sys.stderr)
        print("Sidecar is gated; export the token and re-run.", file=sys.stderr)
        sys.exit(2)

    if args.cmd == "fetch":
        t0 = time.perf_counter()
        f = fetch_forecast(args.question_id_or_url, use_cache=not args.no_cache)
        elapsed = time.perf_counter() - t0
        print(json.dumps(f.model_dump(mode="json"), indent=2, default=str))
        print(f"\n[fetched in {elapsed:.2f}s]", file=sys.stderr)
        return

    if args.cmd == "search":
        results = search_forecasts(args.text, n_results=args.n)
        for r in results:
            cp = r.get("current_prediction")
            cp_str = f"{cp:.0%}" if cp is not None else "n/a"
            print(f"  [{cp_str}] {r['question_id']:>10}  {r['title'][:80]}")
        if not results:
            print("(no results)", file=sys.stderr)
        return


if __name__ == "__main__":
    _cli()
