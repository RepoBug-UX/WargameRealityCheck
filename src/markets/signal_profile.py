"""Signal profile — observable features that distinguish how a market price was made.

This is the project's distinguishing feature: instead of classifying a market
as "crowd-driven" or "informed-trader," we surface the *features* that pattern
the two and let the analyst interpret. Flag names are descriptive, not
interpretive ("concentrated liquidity," not "insider trading").

`build_profile(market)` is a pure function over a `Market` record — no API
calls, no I/O. Phase 4's matcher needs to compute profiles in bulk; Phase 6's
disagreement layer attaches a profile to every disagreement number.

Quality bar (Phase 2): for any loaded market, produces 4-6 features and 0-3
flags. An analyst can look at the panel and form a view on whether the market
reads as crowd-driven or possibly informed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .market_normalizer import Market

FlagKind = Literal[
    "well-distributed liquidity",
    "concentrated liquidity",
    "highly concentrated liquidity",
    "sharp recent move",
    "quiet slow-converging",
    "volume spike",
]


class SignalProfile(BaseModel):
    """Display-ready panel for one market.

    `features` are quantitative observations. `flags` are qualitative labels
    derived from features via thresholds. `notes` are caveats — gaps the
    analyst should know about so they don't over-read the panel.
    """

    model_config = ConfigDict(frozen=True)

    market_id: str
    source_platform: str

    # 6 core features + 1 platform-specific. All optional so a sparse market
    # produces a sparse panel rather than fabricated zeros.
    current_price: float
    concentration_top3: float | None  # primary single-number concentration metric
    volume_24h: float | None
    volume_velocity_24h_vs_7d: float | None  # Kalshi only today
    n_significant_moves_30d: int
    largest_move_30d: float | None  # signed delta of biggest 24h-window move
    price_range_30d: float | None  # max - min over the 30d history

    flags: list[FlagKind]
    notes: list[str]


# Thresholds. Set conservatively to keep the false-positive rate low — better
# to miss a flag than to flag a market spuriously, since flags drive analyst
# attention and a bad flag erodes trust faster than a missed one.
_T_TOP1_CONCENTRATED = 0.50
_T_TOP3_HIGHLY_CONCENTRATED = 0.85
_T_TOP1_DISTRIBUTED = 0.20
_T_TOP3_DISTRIBUTED = 0.50
_T_SHARP_MOVE_DELTA = 0.10  # 10pt move
_T_SHARP_MOVE_LOOKBACK_DAYS = 7
_T_QUIET_RANGE = 0.05  # 5pt total range over 30d
_T_VOLUME_SPIKE = 3.0  # 3x baseline


def _largest_move(market: Market) -> float | None:
    if not market.price_moves_30d:
        return None
    biggest = max(market.price_moves_30d, key=lambda m: abs(m.delta))
    return biggest.delta


def _price_range(market: Market) -> float | None:
    if not market.price_history_30d:
        return None
    prices = [p.price for p in market.price_history_30d]
    return max(prices) - min(prices)


def _has_recent_sharp_move(market: Market) -> bool:
    if not market.price_moves_30d:
        return False
    cutoff_days = _T_SHARP_MOVE_LOOKBACK_DAYS
    now = datetime.now(tz=timezone.utc)
    for move in market.price_moves_30d:
        age_days = (now - move.ts).total_seconds() / 86400
        if age_days <= cutoff_days and abs(move.delta) >= _T_SHARP_MOVE_DELTA:
            return True
    return False


def _derive_flags(
    market: Market,
    *,
    price_range_30d: float | None,
    n_moves: int,
) -> list[FlagKind]:
    flags: list[FlagKind] = []
    conc = market.top_position_concentration

    if conc and conc.top1 is not None and conc.top3 is not None:
        if conc.top1 >= _T_TOP1_CONCENTRATED:
            flags.append("concentrated liquidity")
        if conc.top3 >= _T_TOP3_HIGHLY_CONCENTRATED:
            flags.append("highly concentrated liquidity")
        if (
            conc.top1 <= _T_TOP1_DISTRIBUTED
            and conc.top3 <= _T_TOP3_DISTRIBUTED
        ):
            flags.append("well-distributed liquidity")

    if _has_recent_sharp_move(market):
        flags.append("sharp recent move")

    if price_range_30d is not None and price_range_30d < _T_QUIET_RANGE and n_moves == 0:
        flags.append("quiet slow-converging")

    if (
        market.volume_velocity_24h_vs_7d is not None
        and market.volume_velocity_24h_vs_7d >= _T_VOLUME_SPIKE
    ):
        flags.append("volume spike")

    return flags


def _derive_notes(market: Market) -> list[str]:
    notes: list[str] = []
    if market.top_position_concentration and market.top_position_concentration.note:
        notes.append(f"concentration: {market.top_position_concentration.note}")
    if market.source_platform == "polymarket":
        notes.append("volume velocity unavailable: Polymarket public API does not expose per-period volume")
    if not market.price_history_30d:
        notes.append("no price history available — flags and range are degraded")
    return notes


def build_profile(market: Market) -> SignalProfile:
    n_moves = len(market.price_moves_30d)
    price_range_30d = _price_range(market)
    return SignalProfile(
        market_id=market.market_id,
        source_platform=market.source_platform,
        current_price=market.current_price,
        concentration_top3=(
            market.top_position_concentration.top3
            if market.top_position_concentration
            else None
        ),
        volume_24h=market.volume_24h,
        volume_velocity_24h_vs_7d=market.volume_velocity_24h_vs_7d,
        n_significant_moves_30d=n_moves,
        largest_move_30d=_largest_move(market),
        price_range_30d=price_range_30d,
        flags=_derive_flags(market, price_range_30d=price_range_30d, n_moves=n_moves),
        notes=_derive_notes(market),
    )


def _format_panel(profile: SignalProfile) -> str:
    """Human-readable panel for the CLI / debugging."""
    lines = [
        f"signal profile  [{profile.source_platform}]  {profile.market_id}",
        "  features:",
        f"    current price          {profile.current_price:.4f}",
    ]
    if profile.concentration_top3 is not None:
        lines.append(f"    concentration (top3)  {profile.concentration_top3:.2%}")
    else:
        lines.append("    concentration (top3)  n/a")
    if profile.volume_24h is not None:
        lines.append(f"    24h volume             {profile.volume_24h:,.2f}")
    if profile.volume_velocity_24h_vs_7d is not None:
        lines.append(f"    volume velocity        {profile.volume_velocity_24h_vs_7d:.2f}x baseline")
    lines.append(f"    significant moves 30d  {profile.n_significant_moves_30d}")
    if profile.largest_move_30d is not None:
        lines.append(f"    largest 24h move       {profile.largest_move_30d:+.4f}")
    if profile.price_range_30d is not None:
        lines.append(f"    price range 30d        {profile.price_range_30d:.4f}")
    lines.append("  flags:")
    if profile.flags:
        for f in profile.flags:
            lines.append(f"    [{f}]")
    else:
        # Make zero-flags look intentional rather than broken. Stay descriptive
        # (no axis crossed its threshold) rather than interpretive (no claim
        # about trader behavior).
        lines.append("    none — no axis crossed its flag threshold")
    if profile.notes:
        lines.append("  notes:")
        for n in profile.notes:
            lines.append(f"    - {n}")
    return "\n".join(lines)


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.markets.signal_profile")
    p.add_argument("platform", choices=["polymarket", "kalshi"])
    p.add_argument("market_id")
    p.add_argument("--json", action="store_true", help="emit JSON instead of the formatted panel")
    args = p.parse_args()

    if args.platform == "polymarket":
        from . import polymarket
        market = polymarket.fetch_market(args.market_id)
    else:
        from . import kalshi
        market = kalshi.fetch_market(args.market_id)

    profile = build_profile(market)
    if args.json:
        print(json.dumps(profile.model_dump(mode="json"), indent=2, default=str))
    else:
        print(_format_panel(profile))


if __name__ == "__main__":
    _cli()
