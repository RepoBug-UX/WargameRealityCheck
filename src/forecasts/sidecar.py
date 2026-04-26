"""Sidecar lookup: for each assumption with weak/no market coverage, find
related Metaculus forecasts as labeled reference material.

Two important properties enforced here:

  1. **Output structure is distinct from matches.json.** The sidecar lives
     at `data/forecasts/<wargame_namespace>/sidecar.json` with a
     `SidecarEntry` schema that uses `Forecast` records, never `Market`.
     Phase 6's analysis layer reads from `data/matches/` only — this file
     never enters the disagreement score.

  2. **Always emit an explicit caveat label.** Every sidecar entry carries
     a `note` field stating Metaculus is a forecasting platform with no
     money at stake. The web UI in Phase 7 must display this prominently.

When `METACULUS_API_TOKEN` isn't set, the sidecar still produces a
structured file — empty forecasts lists, with a note explaining the
sidecar was skipped due to missing auth. This way the rest of the
pipeline never breaks because Metaculus isn't configured.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import _cache
from ..ingest.assumption_extractor import assumptions_path_for
from ..ingest.types import WargameAssumption
from ..matching.matcher import MatchResult, matches_path_for
from . import metaculus

SidecarRole = Literal["coverage_gap_filler", "cross_reference"]


class SidecarForecastSummary(BaseModel):
    """Compact reference, not a full Forecast — analysts can drill in by URL."""
    model_config = ConfigDict(frozen=True)
    question_id: str
    title: str
    url: str
    current_prediction: float | None
    nr_forecasters: int | None


class SidecarEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    branch_id: str
    role: SidecarRole
    market_tier: Literal["matched", "partial", "no_match"]
    forecasts: list[SidecarForecastSummary] = Field(default_factory=list)
    note: str  # always present — the caveat label


_NOTE_DEFAULT = (
    "Metaculus is a forecasting platform with calibrated forecaster polling — "
    "no money at stake. This sidecar is reference only and does not enter the "
    "disagreement score or appear on the 2x2 plot."
)
_NOTE_DEGRADED = (
    "Metaculus reference (forecasting platform — no money at stake). "
    "Community prediction not accessible at current API tier — click through "
    "to view the live aggregate on Metaculus. The forecaster count below "
    "indicates how much attention each question is receiving."
)
_NOTE_NO_TOKEN = (
    "Sidecar skipped: METACULUS_API_TOKEN not set. Set the env var and "
    "re-run to populate references. The pipeline does not require Metaculus."
)


def sidecar_path_for(wargame_path: str | Path) -> Path:
    p = Path(wargame_path).resolve()
    parent = p.parent.name
    generic = {"examples", "wargames", "data", ""}
    namespace = parent if parent and parent not in generic else p.stem
    return _cache.FORECASTS_CACHE_DIR.parent / "forecasts" / namespace / "sidecar.json"


def build_sidecar(
    wargame_path: str | Path,
    *,
    target_tiers: tuple[str, ...] = ("no_match", "partial"),
    n_per_branch: int = 3,
) -> list[SidecarEntry]:
    a_path = assumptions_path_for(wargame_path)
    m_path = matches_path_for(wargame_path)
    if not a_path.exists() or not m_path.exists():
        raise FileNotFoundError(
            f"missing assumptions ({a_path.exists()}) or matches ({m_path.exists()}). "
            "Run extraction and matching first."
        )

    assumptions = {
        a.branch_id: a
        for a in (
            WargameAssumption.model_validate(item)
            for item in json.loads(a_path.read_text())
        )
    }
    matches = [MatchResult.model_validate(item) for item in json.loads(m_path.read_text())]

    have_token = metaculus.auth_available()
    out: list[SidecarEntry] = []
    for m in matches:
        if m.tier not in target_tiers:
            continue
        a = assumptions.get(m.branch_id)
        if not a:
            continue
        if not have_token:
            out.append(
                SidecarEntry(
                    branch_id=m.branch_id,
                    role="coverage_gap_filler",
                    market_tier=m.tier,
                    forecasts=[],
                    note=_NOTE_NO_TOKEN,
                )
            )
            continue
        try:
            raw = metaculus.search_forecasts(a.question_text, n_results=n_per_branch)
        except metaculus.MetaculusAuthRequired:
            out.append(
                SidecarEntry(
                    branch_id=m.branch_id, role="coverage_gap_filler",
                    market_tier=m.tier, forecasts=[], note=_NOTE_NO_TOKEN,
                )
            )
            continue
        except Exception as e:
            out.append(
                SidecarEntry(
                    branch_id=m.branch_id, role="coverage_gap_filler",
                    market_tier=m.tier, forecasts=[],
                    note=f"Metaculus search failed: {e}. {_NOTE_DEFAULT}",
                )
            )
            continue

        summaries = [
            SidecarForecastSummary(
                question_id=str(r["question_id"]),
                title=str(r["title"]),
                url=str(r["url"]),
                current_prediction=r.get("current_prediction"),
                nr_forecasters=r.get("nr_forecasters"),
            )
            for r in raw
        ]
        # Pick the right caveat label based on whether CP data is actually
        # accessible. The default tier today returns None on every CP →
        # use the "click through" label that tells analysts where the live
        # aggregate is, instead of misleadingly suggesting we surfaced one.
        any_cp = any(s.current_prediction is not None for s in summaries)
        note = _NOTE_DEFAULT if any_cp else _NOTE_DEGRADED
        out.append(
            SidecarEntry(
                branch_id=m.branch_id,
                role="coverage_gap_filler",
                market_tier=m.tier,
                forecasts=summaries,
                note=note,
            )
        )
    return out


def write_sidecar(entries: list[SidecarEntry], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
                "entries": [e.model_dump(mode="json") for e in entries],
            },
            indent=2,
        )
    )


def _summarize(entries: list[SidecarEntry]) -> str:
    n = len(entries)
    with_refs = sum(1 for e in entries if e.forecasts)
    skipped = sum(1 for e in entries if e.note == _NOTE_NO_TOKEN)
    return (
        f"\nsidecar entries: {n}\n"
        f"  with refs:    {with_refs}\n"
        f"  no-ref/empty: {n - with_refs - skipped}\n"
        f"  skipped (no token): {skipped}"
    )


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.forecasts.sidecar")
    p.add_argument("wargame_path")
    p.add_argument("--out", help="output path (default: data/forecasts/<ns>/sidecar.json)")
    args = p.parse_args()

    entries = build_sidecar(args.wargame_path)
    out_path = Path(args.out) if args.out else sidecar_path_for(args.wargame_path)
    write_sidecar(entries, out_path)

    for e in entries:
        head = f"  [{e.market_tier:>8}] {e.branch_id}"
        if e.forecasts:
            print(f"{head}  ({len(e.forecasts)} Metaculus refs)")
            for f in e.forecasts:
                cp = (
                    f"{f.current_prediction:.0%}"
                    if f.current_prediction is not None
                    else "—"  # CP not accessible at this tier; click through
                )
                fc = f"{f.nr_forecasters} forecasters" if f.nr_forecasters else "n/a"
                print(f"      [cp={cp}  {fc:>14}]  {f.title[:65]}")
                print(f"        {f.url}")
        else:
            print(f"{head}  (no refs — {e.note[:60]}...)")
    print(_summarize(entries))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    _cli()
