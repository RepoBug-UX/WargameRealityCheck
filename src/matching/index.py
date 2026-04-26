"""Build and query the prediction-market corpus index.

For each platform we maintain a ChromaDB collection of currently-active
markets, embedded with the default MiniLM-L6-v2 model (ONNX, 384-dim,
no torch dependency). The index lives at `data/markets/index/` and is
persistent — building it is a one-time cost per refresh cycle.

The matcher (src/matching/matcher.py) queries each platform's collection
separately so cross-platform divergence is computable downstream.

Design notes:
- Platforms are kept in separate collections (not merged) so each can be
  rebuilt and filtered independently.
- We store only the identity / horizon / volume metadata in the collection.
  Full Market records still live in `data/markets/{platform}/` cache and
  are looked up on demand for signal-profile computation.
- The corpus is intentionally bounded (top-N by volume) — covering the
  long tail of low-volume markets would dilute the search results without
  improving coverage on the questions analysts actually wargame.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import chromadb
import httpx
from chromadb.config import Settings
from chromadb.utils import embedding_functions

REPO = Path(__file__).resolve().parents[2]
INDEX_DIR = REPO / "data" / "markets" / "index"

# Kalshi tickers we never want in the index — sports/entertainment dominate
# the API's default ordering and crowd out strategic markets. Substring match
# against the event_ticker (uppercased).
_KALSHI_SKIP_SUBSTRINGS = (
    # Major US team sports
    "NBA", "MLB", "NHL", "NFL", "WNBA",
    # Combat / racing / college / tennis
    "MMA", "UFC", "BOX", "NCAA", "ATP", "WTA", "ITFMATCH",
    "GOLF", "PGA", "F1RACE", "NASCAR",
    # Per-game / per-match wrappers
    "MULTIGAME", "MULTIMARKET", "CROSSCATEGORY", "SPORTS",
    "GAME-", "MATCH-", "RACE-",
    # Entertainment
    "MUSIC", "BOXOFFICE", "OSCAR", "EMMY", "GRAMMY", "BILLBOARD",
)

DEFAULT_N_MARKETS = 300
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _client() -> chromadb.PersistentClient:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(INDEX_DIR),
        settings=Settings(anonymized_telemetry=False),
    )


def _collection(name: str) -> chromadb.api.models.Collection.Collection:
    embed_fn = embedding_functions.DefaultEmbeddingFunction()
    return _client().get_or_create_collection(name=name, embedding_function=embed_fn)


def _scrub_metadata(meta: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """ChromaDB only accepts scalar metadata. Replace None with empty string."""
    out: dict[str, str | int | float | bool] = {}
    for k, v in meta.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


# ----- Polymarket -----

def _fetch_polymarket(n_markets: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    page_size = 100
    offset = 0
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        while len(out) < n_markets:
            r = client.get(
                "https://gamma-api.polymarket.com/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": page_size,
                    "offset": offset,
                    "order": "volumeNum",
                    "ascending": "false",
                },
            )
            if r.status_code != 200:
                raise RuntimeError(f"Polymarket Gamma {r.status_code}: {r.text[:200]}")
            batch = r.json()
            if not batch:
                break
            for m in batch:
                mid = str(m.get("id") or "")
                if not mid or mid in seen_ids:
                    continue
                if not m.get("question"):
                    continue
                seen_ids.add(mid)
                out.append(m)
                if len(out) >= n_markets:
                    break
            offset += page_size
            if len(batch) < page_size:
                break
    return out


def build_polymarket_index(n_markets: int = DEFAULT_N_MARKETS) -> int:
    coll = _collection("polymarket")
    markets = _fetch_polymarket(n_markets)
    if not markets:
        return 0
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict[str, Any]] = []
    for m in markets:
        ids.append(str(m["id"]))
        docs.append(str(m["question"]).strip())
        metas.append(_scrub_metadata({
            "platform": "polymarket",
            "slug": m.get("slug") or "",
            "end_date": m.get("endDate") or "",
            "volume_total": float(m.get("volumeNum") or 0),
            "volume_24h": float(m.get("volume24hr") or 0),
            "url": f"https://polymarket.com/market/{m.get('slug', '')}" if m.get("slug") else "",
        }))
    coll.upsert(ids=ids, documents=docs, metadatas=metas)
    return len(ids)


# ----- Kalshi -----

def _fetch_kalshi(n_markets: int) -> list[dict[str, Any]]:
    """Fetch strategic-political Kalshi markets via the events endpoint.

    The global `/markets?status=open` endpoint returns thousands of per-game
    sports markets that drown out the few strategic ones. The `/events`
    endpoint surfaces the persistent series (presidential elections,
    leadership succession, geopolitical events, etc.) which is what we want
    in the audit corpus. We walk events, then pull each event's markets.
    """
    events: list[dict[str, Any]] = []
    cursor: str | None = None
    pages_seen = 0
    max_pages = 30
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        while pages_seen < max_pages:
            params: dict[str, Any] = {"limit": 200, "status": "open"}
            if cursor:
                params["cursor"] = cursor
            if pages_seen > 0:
                time.sleep(0.4)
            r = client.get(
                "https://api.elections.kalshi.com/trade-api/v2/events",
                params=params,
            )
            if r.status_code == 429:
                time.sleep(3.0)
                r = client.get(
                    "https://api.elections.kalshi.com/trade-api/v2/events",
                    params=params,
                )
            if r.status_code != 200:
                raise RuntimeError(f"Kalshi events {r.status_code}: {r.text[:200]}")
            data = r.json()
            batch = data.get("events", []) or []
            if not batch:
                break
            for e in batch:
                et = (e.get("event_ticker") or "").upper()
                if any(s in et for s in _KALSHI_SKIP_SUBSTRINGS):
                    continue
                events.append(e)
            cursor = data.get("cursor") or None
            pages_seen += 1
            if not cursor:
                break

        # For each surviving event, fetch its markets.
        out: list[dict[str, Any]] = []
        seen_tickers: set[str] = set()
        for e in events:
            if len(out) >= n_markets:
                break
            event_ticker = e.get("event_ticker") or ""
            if not event_ticker:
                continue
            time.sleep(0.2)
            r = client.get(
                "https://api.elections.kalshi.com/trade-api/v2/markets",
                params={"event_ticker": event_ticker, "limit": 100},
            )
            if r.status_code == 429:
                time.sleep(3.0)
                r = client.get(
                    "https://api.elections.kalshi.com/trade-api/v2/markets",
                    params={"event_ticker": event_ticker, "limit": 100},
                )
            if r.status_code != 200:
                continue  # don't fail the whole index for one bad event
            for m in r.json().get("markets", []) or []:
                t = m.get("ticker") or ""
                if not t or t in seen_tickers:
                    continue
                if not (m.get("title") or m.get("yes_sub_title")):
                    continue
                seen_tickers.add(t)
                out.append(m)
                if len(out) >= n_markets:
                    break

    # Sort by volume so the bounded set is the most-traded subset
    out.sort(key=lambda m: -float(m.get("volume_fp") or m.get("volume") or 0))
    return out[:n_markets]


def build_kalshi_index(n_markets: int = DEFAULT_N_MARKETS) -> int:
    coll = _collection("kalshi")
    markets = _fetch_kalshi(n_markets)
    if not markets:
        return 0
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict[str, Any]] = []
    for m in markets:
        ticker = m["ticker"]
        # Build a richer document by combining title + sub_title — this gives
        # the embedder more signal than the often-terse title alone.
        title = (m.get("title") or "").strip()
        sub = (m.get("yes_sub_title") or "").strip()
        doc = f"{title}: {sub}" if title and sub and title != sub else (title or sub)
        ids.append(ticker)
        docs.append(doc)
        metas.append(_scrub_metadata({
            "platform": "kalshi",
            "ticker": ticker,
            "event_ticker": m.get("event_ticker") or "",
            "end_date": m.get("close_time") or m.get("expiration_time") or "",
            "volume_total": float(m.get("volume_fp") or m.get("volume") or 0),
            "volume_24h": float(m.get("volume_24h_fp") or m.get("volume_24h") or 0),
            "url": f"https://kalshi.com/markets/{ticker}",
        }))
    coll.upsert(ids=ids, documents=docs, metadatas=metas)
    return len(ids)


# ----- query -----

def query(
    platform: str, text: str, *, n_results: int = 10
) -> list[dict[str, Any]]:
    """Return up to n_results candidates with id, document, distance, metadata."""
    coll = _collection(platform)
    res = coll.query(
        query_texts=[text],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )
    if not res["ids"] or not res["ids"][0]:
        return []
    out: list[dict[str, Any]] = []
    for i in range(len(res["ids"][0])):
        out.append({
            "id": res["ids"][0][i],
            "document": res["documents"][0][i],
            "distance": float(res["distances"][0][i]),
            "metadata": dict(res["metadatas"][0][i]) if res["metadatas"] else {},
        })
    return out


def index_status() -> dict[str, int]:
    out: dict[str, int] = {}
    for name in ("polymarket", "kalshi"):
        try:
            out[name] = _collection(name).count()
        except Exception:
            out[name] = 0
    return out


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.matching.index")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build")
    p_build.add_argument("--platform", choices=["polymarket", "kalshi", "all"], default="all")
    p_build.add_argument("--n", type=int, default=DEFAULT_N_MARKETS)

    sub.add_parser("status")

    p_q = sub.add_parser("query")
    p_q.add_argument("platform", choices=["polymarket", "kalshi"])
    p_q.add_argument("text")
    p_q.add_argument("--n", type=int, default=5)

    args = p.parse_args()

    if args.cmd == "status":
        for name, n in index_status().items():
            print(f"{name}: {n} markets indexed")
        return

    if args.cmd == "build":
        if args.platform in ("polymarket", "all"):
            t0 = time.perf_counter()
            n = build_polymarket_index(args.n)
            print(f"polymarket: indexed {n} markets in {time.perf_counter() - t0:.1f}s")
        if args.platform in ("kalshi", "all"):
            t0 = time.perf_counter()
            n = build_kalshi_index(args.n)
            print(f"kalshi: indexed {n} markets in {time.perf_counter() - t0:.1f}s")
        return

    if args.cmd == "query":
        results = query(args.platform, args.text, n_results=args.n)
        for r in results:
            print(f"  d={r['distance']:.3f}  {r['id'][:32]:<32} | {r['document'][:80]}")
        if not results:
            print("  (no results — is the index built?)", file=sys.stderr)
        return


if __name__ == "__main__":
    _cli()
