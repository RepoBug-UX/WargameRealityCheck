"""Polymarket client.

Pulls market metadata (Gamma), current price + history (CLOB prices-history),
and order-book / recent trades (CLOB book/trades). Normalizes to `Market`.

Endpoints used:
  Gamma metadata    GET https://gamma-api.polymarket.com/markets/{id}
                    GET https://gamma-api.polymarket.com/markets?slug=...
  CLOB price hist   GET https://clob.polymarket.com/prices-history
  CLOB book         GET https://clob.polymarket.com/book?token_id=...
  CLOB price        GET https://clob.polymarket.com/price?token_id=...&side=...
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import _cache
from .market_normalizer import (
    Market,
    PositionConcentration,
    PriceMove,
    PricePoint,
)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

# Polymarket free read tier: 4,000 req / 10s on Gamma, 9,000 / 10s on CLOB.
# Per-endpoint book/price 1,500 / 10s. We're nowhere near these for an audit run.
HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class PolymarketError(RuntimeError):
    pass


def _client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True)


def _gamma_market(client: httpx.Client, market_id_or_slug: str) -> dict[str, Any]:
    # Try id first, then slug. Gamma exposes both.
    if market_id_or_slug.isdigit():
        r = client.get(f"{GAMMA_BASE}/markets/{market_id_or_slug}")
        if r.status_code == 200:
            return r.json()
    r = client.get(f"{GAMMA_BASE}/markets", params={"slug": market_id_or_slug})
    if r.status_code != 200:
        raise PolymarketError(f"Gamma {r.status_code}: {r.text[:200]}")
    arr = r.json()
    if not arr:
        raise PolymarketError(f"No Polymarket market found for {market_id_or_slug!r}")
    return arr[0]


def _prices_history(
    client: httpx.Client, token_id: str, *, interval: str = "1m", fidelity: int = 60
) -> list[PricePoint]:
    # interval=1m → past month; fidelity=60 → one point per hour, ~720 points.
    r = client.get(
        f"{CLOB_BASE}/prices-history",
        params={"market": token_id, "interval": interval, "fidelity": fidelity},
    )
    if r.status_code != 200:
        return []
    history = r.json().get("history", []) or []
    return [
        PricePoint(ts=datetime.fromtimestamp(p["t"], tz=timezone.utc), price=float(p["p"]))
        for p in history
    ]


def _order_book(client: httpx.Client, token_id: str) -> dict[str, Any] | None:
    r = client.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
    if r.status_code != 200:
        return None
    return r.json()


def _detect_price_moves(
    history: list[PricePoint], *, threshold: float = 0.05, window_hours: int = 24
) -> list[PriceMove]:
    """Flag moves >= `threshold` (default 5pt) over any rolling `window_hours` window."""
    if len(history) < 2:
        return []
    moves: list[PriceMove] = []
    window = window_hours * 3600
    for i, end in enumerate(history):
        # Walk back to the earliest point still inside the window.
        for j in range(i - 1, -1, -1):
            start = history[j]
            if (end.ts - start.ts).total_seconds() > window:
                break
            delta = end.price - start.price
            if abs(delta) >= threshold:
                moves.append(
                    PriceMove(
                        ts=end.ts,
                        delta=delta,
                        from_price=start.price,
                        to_price=end.price,
                    )
                )
                break  # one flag per end-point
    return moves


def _book_concentration(book: dict[str, Any] | None) -> PositionConcentration | None:
    """Compute concentration from resting liquidity at the top of the book.

    NOTE: this is order-book-derived, not true open-interest concentration.
    True per-trader OI requires the Polymarket data API. Phase 2 may swap in
    a richer source; the field shape stays the same.
    """
    if not book:
        return None
    sizes: list[float] = []
    for side in ("bids", "asks"):
        for level in (book.get(side) or []):
            try:
                sizes.append(float(level["size"]))
            except (KeyError, ValueError):
                continue
    if not sizes:
        return None
    sizes.sort(reverse=True)
    total = sum(sizes)
    if total <= 0:
        return None

    def frac(n: int) -> float:
        return sum(sizes[:n]) / total

    return PositionConcentration(
        top1=frac(1),
        top3=frac(3),
        top10=frac(10),
        note="order-book-derived (resting liquidity, not true OI)",
    )


def fetch_market(
    market_id_or_slug: str, *, use_cache: bool = True, cache_ttl: int = 3600
) -> Market:
    """Return a fully-populated Market record. Quality bar: < 2s warm, < 2s cold for one market."""
    cached = _cache.load("polymarket", market_id_or_slug, ttl_seconds=cache_ttl) if use_cache else None
    if cached:
        return Market.model_validate(cached)

    with _client() as client:
        meta = _gamma_market(client, market_id_or_slug)

        # Gamma encodes these as JSON strings inside the JSON. Easy bug source.
        token_ids = json.loads(meta.get("clobTokenIds") or "[]")
        outcome_prices = json.loads(meta.get("outcomePrices") or "[]")
        yes_token = token_ids[0] if token_ids else None
        current_price = float(outcome_prices[0]) if outcome_prices else 0.0

        history: list[PricePoint] = []
        book: dict[str, Any] | None = None
        if yes_token:
            history = _prices_history(client, yes_token)
            book = _order_book(client, yes_token)

    moves = _detect_price_moves(history)
    end_date_raw = meta.get("endDate")
    end_date = datetime.fromisoformat(end_date_raw.replace("Z", "+00:00")) if end_date_raw else None

    market = Market(
        question_text=meta.get("question") or "",
        source_platform="polymarket",
        market_id=str(meta.get("id")),
        resolution_criteria=meta.get("description"),
        end_date=end_date,
        url=f"https://polymarket.com/market/{meta.get('slug')}" if meta.get("slug") else None,
        current_price=current_price,
        price_history_30d=history,
        volume_24h=float(meta["volume24hr"]) if meta.get("volume24hr") is not None else None,
        volume_total=float(meta["volumeNum"]) if meta.get("volumeNum") is not None else None,
        top_position_concentration=_book_concentration(book),
        price_moves_30d=moves,
        last_significant_move=moves[-1] if moves else None,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    _cache.store("polymarket", market_id_or_slug, market.model_dump(mode="json"))
    return market


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.markets.polymarket")
    p.add_argument("command", choices=["fetch"])
    p.add_argument("market_id_or_slug")
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args()

    t0 = time.perf_counter()
    m = fetch_market(args.market_id_or_slug, use_cache=not args.no_cache)
    elapsed = time.perf_counter() - t0
    print(json.dumps(m.model_dump(mode="json"), indent=2, default=str))
    print(f"\n[fetched in {elapsed:.2f}s]", file=sys.stderr)


if __name__ == "__main__":
    _cli()
