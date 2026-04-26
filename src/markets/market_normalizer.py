from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["polymarket", "kalshi"]


class PricePoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    ts: datetime
    price: float


class PriceMove(BaseModel):
    model_config = ConfigDict(frozen=True)
    ts: datetime
    delta: float
    from_price: float
    to_price: float


class PositionConcentration(BaseModel):
    model_config = ConfigDict(frozen=True)
    top1: float | None = None
    top3: float | None = None
    top10: float | None = None
    note: str | None = None


class Market(BaseModel):
    """Unified prediction-market record. Boundary type for the entire pipeline.

    Fields are grouped by purpose: identity, price, volume, participation, movement.
    `Market` is intentionally distinct from `Forecast` (Metaculus) — they live in
    different namespaces and never substitute for each other in the analysis layer.
    """

    model_config = ConfigDict(frozen=True)

    # identity
    question_text: str
    source_platform: Platform
    market_id: str
    resolution_criteria: str | None = None
    end_date: datetime | None = None
    url: str | None = None

    # price
    current_price: float
    price_history_30d: list[PricePoint] = Field(default_factory=list)

    # volume
    volume_24h: float | None = None
    volume_total: float | None = None
    # Ratio of last-24h volume to mean daily volume over the prior 7 days.
    # 1.0 = on baseline; 3.0 = 3x normal flow (a spike). `None` when the
    # platform's public API doesn't expose per-period volume (Polymarket today).
    volume_velocity_24h_vs_7d: float | None = None

    # participation
    # NOTE: a `n_traders` field was deliberately omitted. Neither Polymarket's
    # nor Kalshi's public read API exposes a unique-trader count for free;
    # approximating it from order-book or trade-tape fingerprints would be
    # noisy enough to mislead. We measure liquidity dispersion via
    # `top_position_concentration` instead and document the gap in the README.
    top_position_concentration: PositionConcentration | None = None

    # movement
    price_moves_30d: list[PriceMove] = Field(default_factory=list)
    last_significant_move: PriceMove | None = None

    # provenance
    fetched_at: datetime
