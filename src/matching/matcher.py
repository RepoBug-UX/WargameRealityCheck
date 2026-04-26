"""Top-level matcher: WargameAssumption → top-K market candidates with scores.

For each assumption:
  1. Query each platform's index for top-N nearest documents by embedding.
  2. Score each candidate on the four confidence axes (see confidence.py).
  3. Sort by combined score, take top K.
  4. If best candidate's combined score is below MATCH_THRESHOLD, mark as
     no_match and emit an honest reason rather than a forced low-confidence
     match.

The output is a `MatchResult` per assumption — JSON-serializable, persisted
to data/matches/<wargame_namespace>/matches.json. Phase 6's analysis layer
consumes only candidates with .approved=True (the human-review gate); the
auto-match output is the starting point for that review.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..ingest.assumption_extractor import assumptions_path_for
from ..ingest.types import WargameAssumption
from . import index
from .confidence import (
    MATCH_THRESHOLD,
    PARTIAL_THRESHOLD,
    MatchScores,
    score_candidate,
    tier_for,
)

Platform = Literal["polymarket", "kalshi"]


Polarity = Literal["aligned", "inverted", "unclear"]


class MatchCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    market_platform: Platform
    market_id: str
    market_question: str
    market_url: str | None = None
    market_end_date: str | None = None  # ISO string; None if unknown
    market_volume_total: float | None = None
    scores: MatchScores
    approved: bool = False  # set True by human reviewer (overrides.py)
    # Polarity of the matched market relative to the assumption:
    #   aligned  — market resolves YES on the same event the assumption asks
    #              about (e.g., assumption "Xi remains" + market "Xi remains")
    #   inverted — market resolves YES on the negation
    #              (e.g., assumption "Xi remains" + market "Xi out")
    #   unclear  — reviewer is uncertain; Phase 6 should not auto-compute
    #              disagreement on this match.
    # Default "aligned" because the auto-matcher cannot detect inversion;
    # reviewer flips it via overrides.py when needed. Phase 6's disagreement
    # computation must check this and either flip the market price (1 - p)
    # for inverted matches or route them to a different output type.
    polarity: Polarity = "aligned"


class MatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    branch_id: str
    candidates: list[MatchCandidate] = Field(default_factory=list)
    tier: Literal["matched", "partial", "no_match"] = "no_match"
    no_match_reason: str | None = None  # populated when tier == "no_match"

    @property
    def best(self) -> MatchCandidate | None:
        if self.tier == "no_match" or not self.candidates:
            return None
        return self.candidates[0]


def _platform_candidates(
    assumption: WargameAssumption, platform: Platform, *, n_results: int
) -> list[MatchCandidate]:
    raw = index.query(platform, assumption.question_text, n_results=n_results)
    out: list[MatchCandidate] = []
    for r in raw:
        meta = r.get("metadata") or {}
        scores = score_candidate(
            assumption,
            cosine_distance=r["distance"],
            market_question=r["document"],
            market_metadata=meta,
        )
        end_date = meta.get("end_date") or None
        out.append(
            MatchCandidate(
                market_platform=platform,
                market_id=r["id"],
                market_question=r["document"],
                market_url=meta.get("url") or None,
                market_end_date=str(end_date) if end_date else None,
                market_volume_total=meta.get("volume_total"),
                scores=scores,
            )
        )
    return out


def match_assumption(
    assumption: WargameAssumption,
    *,
    per_platform: int = 8,
    keep_top: int = 5,
) -> MatchResult:
    candidates: list[MatchCandidate] = []
    for platform in ("polymarket", "kalshi"):
        candidates.extend(_platform_candidates(assumption, platform, n_results=per_platform))
    candidates.sort(key=lambda c: -c.scores.combined)
    candidates = candidates[:keep_top]
    if not candidates:
        return MatchResult(
            branch_id=assumption.branch_id,
            candidates=[],
            tier="no_match",
            no_match_reason="no candidates returned by either index",
        )
    best_score = candidates[0].scores.combined
    tier = tier_for(best_score)
    no_match_reason: str | None = None
    if tier == "no_match":
        reasons = candidates[0].scores.explain or []
        no_match_reason = (
            f"best candidate combined={best_score:.2f} below partial threshold "
            f"({PARTIAL_THRESHOLD:.2f}); top axis notes: "
            f"{'; '.join(reasons) or 'low semantic similarity'}"
        )
    return MatchResult(
        branch_id=assumption.branch_id,
        candidates=candidates,
        tier=tier,
        no_match_reason=no_match_reason,
    )


def match_wargame(assumptions: list[WargameAssumption]) -> list[MatchResult]:
    return [match_assumption(a) for a in assumptions if a.approved or True]
    # NOTE: matcher does not enforce approved=True; it's run on the full set
    # so the human reviewer can see candidates for everything. Phase 6's
    # analysis is the layer that filters on approved.


def matches_path_for(wargame_path: str | Path) -> Path:
    p = Path(wargame_path).resolve()
    repo = Path(__file__).resolve().parents[2]
    parent = p.parent.name
    generic = {"examples", "wargames", "data", ""}
    namespace = parent if parent and parent not in generic else p.stem
    return repo / "data" / "matches" / namespace / "matches.json"


def write_matches(out_path: Path, matches: list[MatchResult]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([m.model_dump(mode="json") for m in matches], indent=2)
    )


def _summarize(matches: list[MatchResult]) -> str:
    n = len(matches) or 1
    by_tier = {"matched": 0, "partial": 0, "no_match": 0}
    for m in matches:
        by_tier[m.tier] += 1
    return (
        f"\nmatched:  {by_tier['matched']:>2}/{n}  ({by_tier['matched']*100//n}%)\n"
        f"partial:  {by_tier['partial']:>2}/{n}  ({by_tier['partial']*100//n}%)\n"
        f"no_match: {by_tier['no_match']:>2}/{n}  ({by_tier['no_match']*100//n}%)\n"
        f"audit-eligible (matched + partial): "
        f"{by_tier['matched']+by_tier['partial']}/{n}  "
        f"({(by_tier['matched']+by_tier['partial'])*100//n}%)"
    )


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.matching.matcher")
    p.add_argument("wargame_path", help="path to wargame YAML/JSON")
    p.add_argument(
        "--assumptions",
        help="explicit path to assumptions.json (else derived from wargame_path)",
    )
    p.add_argument("--out", help="output path (default: data/matches/<ns>/matches.json)")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    a_path = (
        Path(args.assumptions) if args.assumptions else assumptions_path_for(args.wargame_path)
    )
    if not a_path.exists():
        print(
            f"no assumptions file at {a_path} — run src.ingest.assumption_extractor first",
            file=sys.stderr,
        )
        sys.exit(2)
    assumptions = [
        WargameAssumption.model_validate(item) for item in json.loads(a_path.read_text())
    ]

    t0 = time.perf_counter()
    matches = match_wargame(assumptions)
    elapsed = time.perf_counter() - t0

    out_path = Path(args.out) if args.out else matches_path_for(args.wargame_path)
    write_matches(out_path, matches)

    if not args.quiet:
        glyph = {"matched": "✓", "partial": "~", "no_match": "✗"}
        for m in matches:
            print(f"  {glyph[m.tier]}  [{m.tier:>8}] {m.branch_id}")
            if m.tier == "no_match":
                print(f"      no plausible match — {m.no_match_reason}")
            else:
                top = m.candidates[0]
                print(f"      → [{top.market_platform}] {top.market_id}")
                print(f"      combined={top.scores.combined:.2f}  "
                      f"sem={top.scores.semantic:.2f}  hor={top.scores.time_horizon:.2f}  "
                      f"cond={top.scores.conditional_structure:.2f}  spec={top.scores.specificity:.2f}")
                print(f"      market: {top.market_question[:90]}")
                if top.scores.explain and m.tier == "partial":
                    for e in top.scores.explain:
                        print(f"      caveat: {e}")
    print(_summarize(matches))
    print(f"\nwrote {out_path}  ({elapsed:.1f}s, threshold={MATCH_THRESHOLD})")


if __name__ == "__main__":
    _cli()
