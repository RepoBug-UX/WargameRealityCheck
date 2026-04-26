"""Kalshi client.

Pulls market metadata, current price + history (candlesticks), and orderbook.
Normalizes to `Market`. Public read endpoints — no auth needed.

Endpoints:
  Market metadata    GET /trade-api/v2/markets/{ticker}
  Orderbook          GET /trade-api/v2/markets/{ticker}/orderbook
  Candlesticks       GET /trade-api/v2/series/{series}/markets/{ticker}/candlesticks
                          ?start_ts=&end_ts=&period_interval=
  Recent trades      GET /trade-api/v2/markets/trades?ticker=&limit=

Kalshi's schema is in flux: newer markets carry `_dollars`/`_fp` suffixed fields
(e.g. `last_price_dollars`, `volume_24h_fp`) while legacy markets use the
unsuffixed forms (`last_price` in cents, `volume` as int). We try both.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from . import _cache
from .market_normalizer import (
    Market,
    PositionConcentration,
    PriceMove,
    PricePoint,
)

BASE = "https://api.elections.kalshi.com/trade-api/v2"
HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class KalshiError(RuntimeError):
    pass


def _client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True)


def _get(client: httpx.Client, path: str, **params: Any) -> dict[str, Any]:
    r = client.get(f"{BASE}{path}", params=params or None)
    if r.status_code != 200:
        raise KalshiError(f"Kalshi {r.status_code} on {path}: {r.text[:200]}")
    return r.json()


def _market_meta(client: httpx.Client, ticker: str) -> dict[str, Any]:
    data = _get(client, f"/markets/{ticker}")
    if "market" not in data:
        raise KalshiError(f"Unexpected market response for {ticker}: {data}")
    return data["market"]


def _series_ticker(market: dict[str, Any]) -> str | None:
    """Derive series ticker from event_ticker prefix (everything before the first '-')."""
    et = market.get("event_ticker") or market.get("ticker") or ""
    if "-" in et:
        return et.split("-", 1)[0]
    return et or None


def _candlesticks_history(
    client: httpx.Client, market: dict[str, Any], *, days: int = 30
) -> tuple[list[PricePoint], list[tuple[int, float]]]:
    """Return (price points, raw [end_ts, volume] tuples) so callers can compute volume velocity."""
    series = _series_ticker(market)
    ticker = market["ticker"]
    if not series:
        return [], []
    end_ts = int(time.time())
    start_ts = end_ts - days * 86400
    try:
        data = _get(
            client,
            f"/series/{series}/markets/{ticker}/candlesticks",
            start_ts=start_ts,
            end_ts=end_ts,
            period_interval=60,  # 60-min candles → ~720 points / 30d
        )
    except KalshiError:
        return [], []
    points: list[PricePoint] = []
    volume_points: list[tuple[int, float]] = []
    for c in data.get("candlesticks", []) or []:
        ts_raw = c.get("end_period_ts") or c.get("ts")
        if ts_raw is None:
            continue
        # Kalshi price block has shifted schemas. Newer markets use
        # {"price": {"close_dollars": "0.31"}} (string dollars), older use
        # {"price": {"close": 31}} (int cents). Some endpoints return a bare
        # float. Try each in turn.
        price_block = c.get("price")
        price: float | None = None
        if isinstance(price_block, dict):
            close_dollars = price_block.get("close_dollars")
            if close_dollars is not None:
                try:
                    price = float(close_dollars)
                except (TypeError, ValueError):
                    price = None
            if price is None and price_block.get("close") is not None:
                try:
                    price = float(price_block["close"]) / 100.0
                except (TypeError, ValueError):
                    price = None
        elif isinstance(price_block, (int, float)):
            price = float(price_block)
            if price > 1.0:
                price = price / 100.0
        if price is None:
            continue
        points.append(
            PricePoint(ts=datetime.fromtimestamp(int(ts_raw), tz=timezone.utc), price=price)
        )
        vol_raw = c.get("volume_fp") or c.get("volume")
        if vol_raw is not None:
            try:
                volume_points.append((int(ts_raw), float(vol_raw)))
            except (TypeError, ValueError):
                pass
    return points, volume_points


def _volume_velocity(volume_points: list[tuple[int, float]]) -> float | None:
    """Ratio of last-24h volume to mean daily volume over the prior 7 days."""
    if not volume_points:
        return None
    now = max(ts for ts, _ in volume_points)
    last_24h = sum(v for ts, v in volume_points if now - ts <= 86400)
    prior_7d = [v for ts, v in volume_points if 86400 < now - ts <= 8 * 86400]
    if not prior_7d:
        return None
    baseline = sum(prior_7d) / 7.0
    if baseline <= 0:
        # Can't form a stable ratio (last-24h could still be positive). Surface
        # as None rather than infinity — downstream treats None as "unknown."
        return None
    return last_24h / baseline


def _orderbook(client: httpx.Client, ticker: str) -> dict[str, Any] | None:
    try:
        data = _get(client, f"/markets/{ticker}/orderbook")
    except KalshiError:
        return None
    # Two known wrapping keys in the wild.
    return data.get("orderbook") or data.get("orderbook_fp") or {}


def _book_concentration(book: dict[str, Any] | None) -> PositionConcentration | None:
    if not book:
        return None
    sizes: list[float] = []
    for side_key in ("yes", "no", "yes_dollars", "no_dollars"):
        side = book.get(side_key) or []
        for level in side:
            # Each level is [price, size] on Kalshi.
            try:
                sizes.append(float(level[1]))
            except (IndexError, TypeError, ValueError):
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


def _detect_price_moves(
    history: list[PricePoint], *, threshold: float = 0.05, window_hours: int = 24
) -> list[PriceMove]:
    if len(history) < 2:
        return []
    moves: list[PriceMove] = []
    window = window_hours * 3600
    for i, end in enumerate(history):
        for j in range(i - 1, -1, -1):
            start = history[j]
            if (end.ts - start.ts).total_seconds() > window:
                break
            delta = end.price - start.price
            if abs(delta) >= threshold:
                moves.append(
                    PriceMove(
                        ts=end.ts, delta=delta, from_price=start.price, to_price=end.price
                    )
                )
                break
    return moves


def _coerce_price(meta: dict[str, Any]) -> float:
    """Prefer last trade; fall back to mid of bid/ask. Return 0.0 if nothing usable."""
    for k in ("last_price_dollars",):
        v = meta.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    last = meta.get("last_price")
    if last is not None:
        try:
            return float(last) / 100.0
        except (TypeError, ValueError):
            pass
    bid = meta.get("yes_bid_dollars") or (meta.get("yes_bid") and float(meta["yes_bid"]) / 100.0)
    ask = meta.get("yes_ask_dollars") or (meta.get("yes_ask") and float(meta["yes_ask"]) / 100.0)
    if bid is not None and ask is not None:
        try:
            return (float(bid) + float(ask)) / 2.0
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _coerce_volume(meta: dict[str, Any], key_fp: str, key_legacy: str) -> float | None:
    v = meta.get(key_fp)
    if v is None:
        v = meta.get(key_legacy)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_market(
    ticker: str, *, use_cache: bool = True, cache_ttl: int = 3600
) -> Market:
    cached = _cache.load("kalshi", ticker, ttl_seconds=cache_ttl) if use_cache else None
    if cached:
        return Market.model_validate(cached)

    with _client() as client:
        meta = _market_meta(client, ticker)
        history, volume_points = _candlesticks_history(client, meta)
        book = _orderbook(client, ticker)

    moves = _detect_price_moves(history)
    velocity = _volume_velocity(volume_points)

    end_date_raw = meta.get("close_time") or meta.get("expiration_time")
    end_date = datetime.fromisoformat(end_date_raw.replace("Z", "+00:00")) if end_date_raw else None

    market = Market(
        question_text=(meta.get("title") or meta.get("yes_sub_title") or "").strip(),
        source_platform="kalshi",
        market_id=ticker,
        resolution_criteria=meta.get("rules_primary") or meta.get("rules_secondary"),
        end_date=end_date,
        url=f"https://kalshi.com/markets/{ticker}",
        current_price=_coerce_price(meta),
        price_history_30d=history,
        volume_24h=_coerce_volume(meta, "volume_24h_fp", "volume_24h"),
        volume_total=_coerce_volume(meta, "volume_fp", "volume"),
        volume_velocity_24h_vs_7d=velocity,
        top_position_concentration=_book_concentration(book),
        price_moves_30d=moves,
        last_significant_move=moves[-1] if moves else None,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    _cache.store("kalshi", ticker, market.model_dump(mode="json"))
    return market


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.markets.kalshi")
    p.add_argument("command", choices=["fetch"])
    p.add_argument("ticker")
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args()

    t0 = time.perf_counter()
    m = fetch_market(args.ticker, use_cache=not args.no_cache)
    elapsed = time.perf_counter() - t0
    print(json.dumps(m.model_dump(mode="json"), indent=2, default=str))
    print(f"\n[fetched in {elapsed:.2f}s]", file=sys.stderr)


if __name__ == "__main__":
    _cli()
