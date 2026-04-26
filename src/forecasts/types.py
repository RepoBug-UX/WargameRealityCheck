"""Forecast — the boundary type for non-market forecasting platforms.

Deliberately NOT named `Market`. Lives in `src/forecasts/`, not
`src/markets/`. Has no price, no volume, no position concentration, no
liquidity dispersion — those concepts don't apply to a forecaster poll.

Phase 5 of the project's plan: Metaculus is a sidecar reference only.
A Forecast never enters the disagreement score and never appears on the 2x2.
The asymmetry between Market and Forecast is structural, not just
documentary — keeping the types in different namespaces means downstream
code can't accidentally substitute one for the other.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ForecastSource = Literal["metaculus"]


class ForecastPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    ts: datetime
    community_prediction: float  # binary forecast aggregate, 0-1


class Forecast(BaseModel):
    """One forecasting-platform question with current and historical aggregate.

    `community_prediction` is the platform's aggregated forecaster signal —
    on Metaculus this is the recency-weighted median. `n_forecasters` is
    the count of distinct forecasters who have submitted; this is the
    closest analog to "how much human attention is on this question."

    Note the absence of price, volume, concentration, etc. Forecasters do
    not put money on positions, so those features do not exist for
    Forecasts. This is the methodological asymmetry the project's epistemic
    stance is built around.
    """

    model_config = ConfigDict(frozen=True)

    # identity
    question_text: str
    source: ForecastSource
    question_id: str
    resolution_criteria: str | None = None
    end_date: datetime | None = None
    url: str | None = None

    # current + history of community aggregate.
    # `community_prediction` is `None` when the API access tier in use does
    # not expose aggregations (current default tier as of early 2026 — the
    # `aggregation_explorer` endpoint is gated behind a separate access
    # request). When None, downstream code must surface the question text,
    # forecaster count, and URL with a "click through" caveat rather than
    # silently dropping it.
    community_prediction: float | None = None
    n_forecasters: int | None = None
    prediction_history: list[ForecastPoint] = Field(default_factory=list)

    # provenance
    fetched_at: datetime
