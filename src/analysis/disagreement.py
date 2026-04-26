"""Markets-only disagreement computation — the correctness axis.

For each approved match:
  1. Fetch live `Market` (cached) to get current price + signal profile.
  2. Apply polarity correction:
        aligned   → market_compared = market_price
        inverted  → market_compared = 1 - market_price
        unclear   → route to StructuredComparison (no auto-compute)
  3. Compute signed delta and absolute delta.
  4. Attach signal profile (Phase 2).
  5. Choose output shape based on tier and polarity:
        matched   + aligned/inverted   → MarketDisagreement (strict)
        partial   + any                → StructuredComparison
        any       + unclear            → StructuredComparison

Metaculus is never invoked here. The forecasts/ namespace exists for
sidecar reference only and does not enter the disagreement score.
"""
from __future__ import annotations

from ..ingest.types import WargameAssumption
from ..markets import kalshi, polymarket
from ..markets.market_normalizer import Market
from ..markets.signal_profile import build_profile
from ..matching.matcher import MatchCandidate, MatchResult
from .types import MarketDisagreement, StructuredComparison


def _fetch_market(platform: str, market_id: str) -> Market:
    if platform == "polymarket":
        # The matcher stored the Gamma id; polymarket.fetch_market accepts id or slug.
        return polymarket.fetch_market(market_id)
    if platform == "kalshi":
        return kalshi.fetch_market(market_id)
    raise ValueError(f"unknown platform: {platform}")


def _apply_polarity(price: float, polarity: str) -> float:
    if polarity == "inverted":
        return 1.0 - price
    return price


def _conditioning_event(assumption: WargameAssumption) -> str | None:
    if not assumption.dependencies:
        return None
    return ", ".join(assumption.dependencies)


def _strict_disagreement(
    assumption: WargameAssumption,
    candidate: MatchCandidate,
    market: Market,
) -> MarketDisagreement:
    raw = market.current_price
    compared = _apply_polarity(raw, candidate.polarity)
    delta = compared - assumption.wargame_probability
    return MarketDisagreement(
        branch_id=assumption.branch_id,
        wargame_probability=assumption.wargame_probability,
        raw_market_price=raw,
        market_price_compared=compared,
        polarity_applied=candidate.polarity,
        delta=delta,
        abs_delta=abs(delta),
        market_id=market.market_id,
        market_platform=market.source_platform,
        market_url=market.url,
        market_question=market.question_text,
        signal_profile=build_profile(market),
    )


def _structured_comparison(
    assumption: WargameAssumption,
    candidate: MatchCandidate,
    market: Market,
    *,
    tier: str,
) -> StructuredComparison:
    cond = _conditioning_event(assumption)
    if cond and candidate.polarity != "unclear":
        caveat = (
            f"these measure different things — wargame probability is "
            f"conditional on {cond}; market price is unconditional. "
            f"Comparing the two as a single delta would be apples-to-oranges."
        )
    elif candidate.polarity == "unclear":
        caveat = (
            "polarity unresolved — the matched market may be the logical "
            "negation of the assumption. Do not auto-compute a delta until "
            "the reviewer confirms polarity."
        )
    else:
        caveat = (
            f"partial match (tier={tier}) — the matched market is semantically "
            f"related but the auto-matcher could not establish a strict "
            f"apples-to-apples comparison. Treat the comparison as indicative, "
            f"not definitive."
        )
    return StructuredComparison(
        branch_id=assumption.branch_id,
        wargame_probability=assumption.wargame_probability,
        conditioning_event=cond,
        raw_market_price=market.current_price,
        polarity_applied=candidate.polarity,
        market_id=market.market_id,
        market_platform=market.source_platform,
        market_url=market.url,
        market_question=market.question_text,
        comparison_caveat=caveat,
        signal_profile=build_profile(market),
    )


def compute_for_branch(
    assumption: WargameAssumption,
    match: MatchResult,
) -> tuple[MarketDisagreement | None, StructuredComparison | None, str]:
    """Return (strict, structured, shape). Exactly one of strict/structured
    is non-None when the branch has an approved candidate; both are None
    when no_match or no_approval. `shape` is one of strict / structured /
    no_match / no_approval.
    """
    if match.tier == "no_match":
        return None, None, "no_match"

    approved = next((c for c in match.candidates if c.approved), None)
    if approved is None:
        return None, None, "no_approval"

    market = _fetch_market(approved.market_platform, approved.market_id)

    if approved.polarity == "unclear":
        return None, _structured_comparison(assumption, approved, market, tier=match.tier), "structured"

    if match.tier == "matched":
        # Strict path: matched + aligned or inverted (with auto-flip)
        return _strict_disagreement(assumption, approved, market), None, "strict"

    # tier == "partial" — even with aligned polarity, the apples-to-oranges
    # caveat applies. Structured comparison preserves both numbers.
    return None, _structured_comparison(assumption, approved, market, tier=match.tier), "structured"
