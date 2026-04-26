"""Multi-dimension confidence scoring for an (assumption, market) candidate.

Four axes:
  1. semantic_similarity   — embedding cosine similarity (load-bearing)
  2. time_horizon          — wargame horizon vs market resolution date
  3. conditional_structure — does the assumption's conditionality match
                              the market's? (load-bearing for honest scoring)
  4. specificity           — soft proxy for "the market resolves on
                              precisely the right event." Hard to compute
                              cleanly, kept as a default for v1 and refined
                              by the human reviewer.

Combined score: weighted arithmetic mean, capped at min(semantic,
conditional_structure). The cap is the key honesty guard — neither a strong
horizon match nor a high specificity should rescue a candidate whose
semantic similarity is weak or whose conditional structure mismatches.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..ingest.types import WargameAssumption


class MatchScores(BaseModel):
    model_config = ConfigDict(frozen=True)
    semantic: float
    time_horizon: float
    conditional_structure: float
    specificity: float
    combined: float
    explain: list[str]  # short notes on which axes were degraded and why


# Weights sum to 1.0
_W_SEMANTIC = 0.50
_W_HORIZON = 0.20
_W_CONDITIONAL = 0.20
_W_SPECIFICITY = 0.10


def _semantic_from_distance(cosine_distance: float) -> float:
    """ChromaDB cosine distance ∈ [0, 2] → similarity ∈ [0, 1]."""
    sim = 1.0 - (cosine_distance / 2.0)
    return max(0.0, min(1.0, sim))


# Loose horizon parser — extract a target date from free-form analyst horizons
# like "end of 2026", "2027", "first 30 days", "12 months". Returns the
# implied end-of-window datetime in UTC, or None if unparseable.
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_MONTHS_RE = re.compile(r"\b(\d+)\s*month", re.IGNORECASE)
_DAYS_RE = re.compile(r"\b(\d+)\s*day", re.IGNORECASE)


def _parse_horizon_to_date(horizon: str | None, anchor: datetime | None = None) -> datetime | None:
    if not horizon:
        return None
    h = horizon.lower()
    now = anchor or datetime.now(tz=timezone.utc)

    # Explicit year reference
    m = _YEAR_RE.search(horizon)
    if m:
        year = int(m.group(1))
        # "end of 2026" → 2026-12-31; bare "2026" → same
        return datetime(year, 12, 31, tzinfo=timezone.utc)

    # Relative durations
    m = _MONTHS_RE.search(h)
    if m:
        months = int(m.group(1))
        return _add_months(now, months)
    m = _DAYS_RE.search(h)
    if m:
        days = int(m.group(1))
        return now.replace(microsecond=0).fromtimestamp(now.timestamp() + days * 86400, tz=timezone.utc)

    # Common short forms
    if "72h" in h or "72 hour" in h:
        return now.replace(microsecond=0).fromtimestamp(now.timestamp() + 3 * 86400, tz=timezone.utc)
    if "campaign" in h or "ongoing" in h:
        return None  # genuinely open-ended
    return None


def _add_months(dt: datetime, months: int) -> datetime:
    y = dt.year + (dt.month - 1 + months) // 12
    m = (dt.month - 1 + months) % 12 + 1
    return dt.replace(year=y, month=m)


def _parse_market_end_date(end_date_raw: str | int | float | None) -> datetime | None:
    if not end_date_raw:
        return None
    if isinstance(end_date_raw, (int, float)):
        return datetime.fromtimestamp(end_date_raw, tz=timezone.utc)
    s = str(end_date_raw).strip()
    if not s:
        return None
    # Normalize trailing Z form to +00:00 for fromisoformat
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _score_horizon(
    assumption: WargameAssumption, market_end_date: datetime | None
) -> tuple[float, str | None]:
    """1.0 perfect, 0.5 loose, 0.0 mismatch. Default 0.5 if either side missing."""
    target = _parse_horizon_to_date(assumption.horizon)
    if target is None or market_end_date is None:
        return 0.5, "horizon: unparseable on one side, defaulted to 0.5"
    delta_days = abs((market_end_date - target).total_seconds()) / 86400
    if delta_days <= 60:
        return 1.0, None
    if delta_days <= 180:
        return 0.7, f"horizon: market resolves {int(delta_days)}d off target (loose match)"
    if delta_days <= 365:
        return 0.4, f"horizon: market resolves {int(delta_days)}d off target"
    return 0.1, f"horizon: market resolves {int(delta_days)}d off target (mismatch)"


def _score_conditional_structure(
    assumption: WargameAssumption, market_question: str
) -> tuple[float, str | None]:
    """Penalize when an assumption is conditional but the market is unconditional.

    Heuristic for conditionality: the assumption depends on other branches
    (its `dependencies` is non-empty). Markets are treated as unconditional
    unless we detect explicit conditional language in the question text.
    """
    assumption_conditional = bool(assumption.dependencies)
    q = market_question.lower()
    market_conditional = any(
        marker in q for marker in (" if ", " given ", "conditional on ", "assuming ")
    )

    if not assumption_conditional and not market_conditional:
        return 1.0, None
    if assumption_conditional and market_conditional:
        return 0.9, None  # both conditional, but matching condition is hard to verify
    if assumption_conditional and not market_conditional:
        # Apples-to-oranges veto: conditional-vs-unconditional pairs cannot
        # be a strong (apples-to-apples) match. We cap at 0.42, just above
        # the partial threshold, so an otherwise-good candidate can still
        # surface as a *partial* match with a clear caveat — analyst-
        # reviewable but never auto-trusted as a hard match.
        return 0.42, (
            "conditional mismatch: assumption is conditional on "
            f"{','.join(assumption.dependencies)} but market is unconditional"
        )
    # unconditional assumption vs conditional market — odd, mild penalty
    return 0.7, "market appears conditional but assumption is unconditional"


# Acronyms that have widely-used full-form equivalents. If the assumption
# uses the acronym and the market uses a known alias (or vice-versa), the
# specificity check should not penalize. Aliases are matched as
# case-insensitive substrings against the market text.
_ACRONYM_ALIASES: dict[str, tuple[str, ...]] = {
    "PRC": ("China", "Beijing", "Chinese"),
    "ROC": ("Taiwan", "Taipei"),
    "DPRK": ("North Korea",),
    "ROK": ("South Korea",),
    "USFK": ("U.S. Forces Korea",),
    "JSDF": ("Self-Defense Force", "Japanese forces"),
    "PLA": ("Chinese military", "People's Liberation Army"),
    "PLAAF": ("Chinese Air Force",),
    "PLAN": ("Chinese Navy",),
    "EU": ("European Union", "Europe"),
    "NATO": ("Atlantic Treaty",),
    "CCP": ("Communist Party",),
    "UN": ("United Nations",),
    "UK": ("United Kingdom", "Britain"),
    # No alias for TSMC, ICBM, EDCA, MLR, IRBM, MRBM, OOB, ROE — these
    # stay distinctive and a missing one is a real specificity miss.
}


def _acronym_satisfied(acro: str, market_question: str) -> bool:
    """True if the acronym OR any of its aliases appears in the market text."""
    if acro in market_question:
        return True
    for alias in _ACRONYM_ALIASES.get(acro, ()):
        if alias.lower() in market_question.lower():
            return True
    return False


def _score_specificity(
    assumption: WargameAssumption, market_question: str
) -> tuple[float, str | None]:
    """Soft default. Penalize cheap-to-detect specificity mismatches.

    Acronym check: all-caps tokens of 3+ chars are highly identifying —
    if the assumption uses one and the market doesn't (and no known alias
    appears), the questions are almost certainly about different things.

    Otherwise, count proper-noun overlap and grade.
    """
    a_caps = _capitalized_tokens(assumption.question_text)
    m_caps = _capitalized_tokens(market_question)
    if not a_caps:
        return 0.7, None

    a_acronyms = {t for t in a_caps if t.isupper() and len(t) >= 3}
    missing_acros = sorted(a for a in a_acronyms if not _acronym_satisfied(a, market_question))
    if missing_acros:
        return 0.30, (
            f"specificity: assumption acronym(s) missing from market "
            f"(checked aliases): {missing_acros}"
        )

    overlap = a_caps & m_caps
    if not overlap:
        return 0.40, f"specificity: no proper-noun overlap (assumption: {sorted(a_caps)[:3]})"
    if len(overlap) >= 2:
        return 0.85, None
    return 0.60, "specificity: only one proper-noun overlap (weak)"


_CAP_RE = re.compile(r"\b([A-Z][A-Za-z]{2,})\b")
_STOPCAPS = {
    "Will", "The", "Would", "Has", "Have", "Does", "Does", "Did", "Be",
    "United", "States", "U.S.", "US",  # too common to count as identifying
}


def _capitalized_tokens(text: str) -> set[str]:
    return {t for t in _CAP_RE.findall(text or "") if t not in _STOPCAPS}


def score_candidate(
    assumption: WargameAssumption,
    *,
    cosine_distance: float,
    market_question: str,
    market_metadata: dict[str, Any],
) -> MatchScores:
    semantic = _semantic_from_distance(cosine_distance)
    end_date = _parse_market_end_date(market_metadata.get("end_date"))
    horizon, h_note = _score_horizon(assumption, end_date)
    conditional, c_note = _score_conditional_structure(
        assumption, market_question
    )
    specificity, s_note = _score_specificity(assumption, market_question)

    weighted = (
        _W_SEMANTIC * semantic
        + _W_HORIZON * horizon
        + _W_CONDITIONAL * conditional
        + _W_SPECIFICITY * specificity
    )
    # Honesty cap: combined cannot exceed any veto-class axis.
    # Always-veto: semantic (must be similar at all), conditional (must
    # agree on conditionality). Conditional-veto when severely off:
    # horizon (>1y mismatch) and specificity (no proper-noun overlap on
    # an assumption that has identifying nouns to overlap with).
    cap_axes = [semantic, conditional]
    cap_reasons = [f"semantic={semantic:.2f}", f"conditional={conditional:.2f}"]
    if horizon <= 0.3:
        cap_axes.append(horizon)
        cap_reasons.append(f"horizon={horizon:.2f}")
    if specificity <= 0.4:
        cap_axes.append(specificity)
        cap_reasons.append(f"specificity={specificity:.2f}")
    combined = min(weighted, *cap_axes)

    explain: list[str] = []
    for n in (h_note, c_note, s_note):
        if n:
            explain.append(n)
    if combined < weighted:
        explain.append(f"combined capped at min({', '.join(cap_reasons)})")

    return MatchScores(
        semantic=round(semantic, 3),
        time_horizon=round(horizon, 3),
        conditional_structure=round(conditional, 3),
        specificity=round(specificity, 3),
        combined=round(combined, 3),
        explain=explain,
    )


# Two-tier thresholds. A "matched" candidate is comparable apples-to-apples;
# a "partial" candidate is the closest the corpus offers but carries a
# veto-class warning the analyst should read before trusting the comparison.
# Anything below `PARTIAL_THRESHOLD` is reported as no-match.
MATCH_THRESHOLD = 0.55
PARTIAL_THRESHOLD = 0.40


def tier_for(combined: float) -> str:
    if combined >= MATCH_THRESHOLD:
        return "matched"
    if combined >= PARTIAL_THRESHOLD:
        return "partial"
    return "no_match"
