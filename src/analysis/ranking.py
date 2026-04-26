"""2x2 placement and action-list construction.

The 2x2 axes are:
  X (correctness): absolute disagreement |market - wargame|
  Y (consequence): structural sensitivity (downstream-reach proxy)

Action list = top-right quadrant: high disagreement AND high sensitivity.
We use percentile cutoffs computed within the strict-panel only (partials
and structured comparisons do not enter the 2x2 — see Phase 6 plan).

Thresholds default to the 50th percentile on each axis. With small
strict panels (e.g., CSIS often has 1 strict candidate), the default
percentile yields trivial results — we return everything in the strict
panel as the action list when n <= 2.
"""
from __future__ import annotations

from .types import MarketDisagreement


def _percentile(values: list[float], pct: float) -> float:
    """Inclusive percentile for small lists. pct ∈ [0, 100]."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def action_list(
    strict_panel: list[MarketDisagreement],
    sensitivity_by_branch: dict[str, float],
    *,
    disagreement_pct: float = 50.0,
    sensitivity_pct: float = 50.0,
) -> list[str]:
    """Top-right quadrant of the 2x2: high disagreement AND high sensitivity."""
    if not strict_panel:
        return []
    if len(strict_panel) <= 2:
        # Too few points for percentile thresholds to mean anything; return all.
        return [d.branch_id for d in strict_panel]
    d_threshold = _percentile([d.abs_delta for d in strict_panel], disagreement_pct)
    s_threshold = _percentile(
        [sensitivity_by_branch.get(d.branch_id, 0.0) for d in strict_panel],
        sensitivity_pct,
    )
    out: list[str] = []
    for d in strict_panel:
        sensitivity = sensitivity_by_branch.get(d.branch_id, 0.0)
        if d.abs_delta >= d_threshold and sensitivity >= s_threshold:
            out.append(d.branch_id)
    return out
