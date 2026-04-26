"""Boundary types for the audit analysis layer.

Phase 6 produces two structurally different audit outputs depending on the
matched candidate's tier and polarity (see plan, Phase 6 section):

  - `MarketDisagreement` — strict-match output. Single signed delta plus
    its absolute magnitude. Plotted on the 2x2.
  - `StructuredComparison` — partial-match or unclear-polarity output.
    A structured comparison object with explicit caveats. NOT a single
    number; NOT plotted on the 2x2; lives in a separate review panel.

The two share the signal-profile attachment but differ in everything else.
Combining them into one number would be the apples-to-oranges error the
project is built to avoid.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..markets.signal_profile import SignalProfile

OutputShape = Literal["strict", "structured", "no_match", "no_approval"]
Polarity = Literal["aligned", "inverted", "unclear"]


class MarketDisagreement(BaseModel):
    """Strict-match disagreement: appropriate when tier=matched and
    polarity is aligned or inverted (with the inversion correctly applied).
    """
    model_config = ConfigDict(frozen=True)

    branch_id: str
    wargame_probability: float
    raw_market_price: float
    market_price_compared: float  # post-polarity adjustment
    polarity_applied: Polarity
    delta: float  # signed: market_compared - wargame
    abs_delta: float
    market_id: str
    market_platform: str
    market_url: str | None
    market_question: str
    signal_profile: SignalProfile


class StructuredComparison(BaseModel):
    """Partial / type-mismatch / unclear-polarity comparison.

    Captures the wargame's conditional probability and the market's
    unconditional price as separate numbers, with an explicit caveat
    saying they measure different things. Phase 7's UI must visually
    distinguish this from a strict disagreement.
    """
    model_config = ConfigDict(frozen=True)

    branch_id: str
    wargame_probability: float
    conditioning_event: str | None  # joined branch_ids the assumption depends on
    raw_market_price: float
    polarity_applied: Polarity  # informational only — no auto-flip on structured
    market_id: str
    market_platform: str
    market_url: str | None
    market_question: str
    comparison_caveat: str  # literal text the UI must render verbatim
    signal_profile: SignalProfile


class InternalTension(BaseModel):
    """An in-document inconsistency surfaced from the wargame input itself,
    independent of any market data."""
    model_config = ConfigDict(frozen=True)

    branch_id: str
    tension_text: str  # excerpt from narrative_context that triggered detection
    matched_keywords: list[str]
    citation: str | None  # source page/section if available


class BranchAuditOutput(BaseModel):
    """Per-branch audit output. Exactly one of `strict` / `structured` is
    populated; the others are None depending on `shape`. `internal_tensions`
    is independent and may be populated regardless of market match status.
    `sensitivity` is the structural-proxy score (0-1)."""
    model_config = ConfigDict(frozen=True)

    branch_id: str
    shape: OutputShape
    sensitivity: float
    strict: MarketDisagreement | None = None
    structured: StructuredComparison | None = None
    internal_tensions: list[InternalTension] = Field(default_factory=list)
    no_match_reason: str | None = None  # populated when shape == "no_match"


class AuditReport(BaseModel):
    """Top-level audit output. Three panels (strict, structured, tension)
    plus an action list and a no-match list. Phase 7's UI renders these
    as separate panels — never merged into a single ranking."""
    model_config = ConfigDict(frozen=True)

    wargame_name: str
    generated_at: datetime
    n_assumptions_total: int
    n_assumptions_approved: int

    branches: list[BranchAuditOutput]

    # Convenience views — all derivable from `branches` but pre-computed
    # for the UI.
    strict_panel: list[MarketDisagreement] = Field(default_factory=list)  # plotted on 2x2
    structured_panel: list[StructuredComparison] = Field(default_factory=list)
    tension_panel: list[InternalTension] = Field(default_factory=list)
    no_match_branches: list[str] = Field(default_factory=list)
    no_approval_branches: list[str] = Field(default_factory=list)

    # Action list: branch_ids in the top-right quadrant of the 2x2 (high
    # disagreement AND high sensitivity). Strict-shape only.
    action_list: list[str] = Field(default_factory=list)
