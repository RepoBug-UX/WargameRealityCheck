"""Top-level audit: assumptions + matches → AuditReport.

Combines:
  - disagreement.compute_for_branch — strict or structured per branch
  - sensitivity.sensitivity_for_all  — structural reach per branch
  - internal_tension.detect_all      — second axis, separate panel
  - ranking.action_list              — 2x2 top-right quadrant

CLI: `python -m src.analysis.audit examples/<name>/wargame.yaml`
Writes the report to data/audits/<wargame_namespace>/audit.json.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ..ingest.assumption_extractor import assumptions_path_for
from ..ingest.types import WargameAssumption
from ..matching.matcher import MatchResult, matches_path_for
from . import disagreement, internal_tension, ranking, sensitivity
from .types import (
    AuditReport,
    BranchAuditOutput,
    MarketDisagreement,
    StructuredComparison,
)


def audit_path_for(wargame_path: str | Path) -> Path:
    p = Path(wargame_path).resolve()
    repo = Path(__file__).resolve().parents[2]
    parent = p.parent.name
    generic = {"examples", "wargames", "data", ""}
    namespace = parent if parent and parent not in generic else p.stem
    return repo / "data" / "audits" / namespace / "audit.json"


def run_audit(wargame_path: str | Path) -> AuditReport:
    a_path = assumptions_path_for(wargame_path)
    m_path = matches_path_for(wargame_path)
    if not a_path.exists():
        raise FileNotFoundError(f"missing assumptions: {a_path}")
    if not m_path.exists():
        raise FileNotFoundError(f"missing matches: {m_path}")

    assumptions = [
        WargameAssumption.model_validate(item)
        for item in json.loads(a_path.read_text())
    ]
    matches_by_branch: dict[str, MatchResult] = {
        item["branch_id"]: MatchResult.model_validate(item)
        for item in json.loads(m_path.read_text())
    }
    sens_by_branch = sensitivity.sensitivity_for_all(assumptions)
    tensions_by_branch: dict[str, list] = {}
    for t in internal_tension.detect_all(assumptions):
        tensions_by_branch.setdefault(t.branch_id, []).append(t)

    branches: list[BranchAuditOutput] = []
    strict_panel: list[MarketDisagreement] = []
    structured_panel: list[StructuredComparison] = []
    no_match: list[str] = []
    no_approval: list[str] = []

    for a in assumptions:
        match = matches_by_branch.get(a.branch_id)
        if match is None:
            no_match.append(a.branch_id)
            branches.append(
                BranchAuditOutput(
                    branch_id=a.branch_id,
                    shape="no_match",
                    sensitivity=sens_by_branch.get(a.branch_id, 0.0),
                    no_match_reason="no MatchResult found",
                    internal_tensions=tensions_by_branch.get(a.branch_id, []),
                )
            )
            continue

        strict, structured, shape = disagreement.compute_for_branch(a, match)
        if shape == "strict" and strict is not None:
            strict_panel.append(strict)
        elif shape == "structured" and structured is not None:
            structured_panel.append(structured)
        elif shape == "no_match":
            no_match.append(a.branch_id)
        elif shape == "no_approval":
            no_approval.append(a.branch_id)

        branches.append(
            BranchAuditOutput(
                branch_id=a.branch_id,
                shape=shape,
                sensitivity=sens_by_branch.get(a.branch_id, 0.0),
                strict=strict,
                structured=structured,
                internal_tensions=tensions_by_branch.get(a.branch_id, []),
                no_match_reason=match.no_match_reason if shape == "no_match" else None,
            )
        )

    actions = ranking.action_list(strict_panel, sens_by_branch)

    # Wargame name from the YAML if present, else from filename
    try:
        from ..ingest.wargame_loader import load_wargame
        name = load_wargame(wargame_path).name
    except Exception:
        name = Path(wargame_path).stem

    n_approved = sum(
        1
        for m in matches_by_branch.values()
        for c in m.candidates
        if c.approved
    )
    return AuditReport(
        wargame_name=name,
        generated_at=datetime.now(tz=timezone.utc),
        n_assumptions_total=len(assumptions),
        n_assumptions_approved=n_approved,
        branches=branches,
        strict_panel=strict_panel,
        structured_panel=structured_panel,
        tension_panel=[t for ts in tensions_by_branch.values() for t in ts],
        no_match_branches=no_match,
        no_approval_branches=no_approval,
        action_list=actions,
    )


def write_report(report: AuditReport, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, default=str))


def _render_markdown(report: AuditReport) -> str:
    """Human-readable Markdown audit report.

    Designed as a fallback shipping mode — the file is intended to be
    readable on its own, no web UI required. Renders all three first-class
    output components plus the no-match list as separate sections, in the
    order an analyst would consume them.
    """
    sens_by_branch = {b.branch_id: b.sensitivity for b in report.branches}
    out: list[str] = []
    out.append(f"# Audit Report — {report.wargame_name}")
    out.append("")
    out.append(f"_Generated: {report.generated_at.isoformat()}_")
    out.append("")
    out.append(f"- Total assumptions: **{report.n_assumptions_total}**")
    out.append(f"- Approved match candidates: **{report.n_assumptions_approved}**")
    out.append(f"- Strict disagreements (2x2-plotted): **{len(report.strict_panel)}**")
    out.append(f"- Structured comparisons (review-required): **{len(report.structured_panel)}**")
    out.append(f"- In-document tensions (no market data needed): **{len(report.tension_panel)}**")
    out.append(f"- No plausible market match: **{len(report.no_match_branches)}**")
    out.append(f"- Awaiting reviewer approval: **{len(report.no_approval_branches)}**")
    out.append("")

    # --- Action list
    if report.action_list:
        out.append("## Action list — top-right quadrant of the 2x2")
        out.append("")
        out.append("Branches with both high market disagreement and high structural sensitivity. Read these first.")
        out.append("")
        for bid in report.action_list:
            d = next((d for d in report.strict_panel if d.branch_id == bid), None)
            if d:
                out.append(
                    f"- **{bid}** — wargame `{d.wargame_probability:.2f}` vs market `{d.market_price_compared:.2f}` "
                    f"(Δ `{d.delta:+.2f}`, sensitivity `{sens_by_branch.get(bid, 0.0):.2f}`)"
                )
        out.append("")

    # --- Strict panel
    out.append("## 1. Strict disagreements (markets-vs-wargame)")
    out.append("")
    if not report.strict_panel:
        out.append("_No strict disagreements — no `matched`-tier candidates with confirmed polarity were approved._")
        out.append("")
    else:
        for d in sorted(report.strict_panel, key=lambda d: -d.abs_delta):
            star = " ★" if d.branch_id in report.action_list else ""
            out.append(f"### `{d.branch_id}`{star}")
            out.append("")
            out.append(f"- Wargame probability: **{d.wargame_probability:.2f}**")
            out.append(
                f"- Market price (post-polarity, polarity={d.polarity_applied}): "
                f"**{d.market_price_compared:.2f}** (raw `{d.raw_market_price:.2f}`)"
            )
            out.append(f"- Disagreement: **Δ {d.delta:+.2f}** (|Δ| `{d.abs_delta:.2f}`)")
            out.append(f"- Structural sensitivity: **{sens_by_branch.get(d.branch_id, 0.0):.2f}**")
            out.append(f"- Matched market: [{d.market_question}]({d.market_url}) ({d.market_platform})")
            sp = d.signal_profile
            flags = ", ".join(f"`{f}`" for f in sp.flags) if sp.flags else "_(no flags)_"
            out.append(f"- Signal-profile flags: {flags}")
            if sp.notes:
                for n in sp.notes:
                    out.append(f"  - _{n}_")
            out.append("")

    # --- Structured panel
    out.append("## 2. Structured comparisons (apples-to-oranges flagged)")
    out.append("")
    if not report.structured_panel:
        out.append("_No structured comparisons — no `partial`-tier or `unclear`-polarity candidates were approved._")
        out.append("")
    else:
        out.append(
            "These pairs do not produce a single disagreement number. The wargame's "
            "conditional probability and the market's unconditional price measure "
            "different things; treat them as starting points for review, not as audit findings."
        )
        out.append("")
        for s in report.structured_panel:
            out.append(f"### `{s.branch_id}`")
            out.append("")
            out.append(f"- Wargame probability: **{s.wargame_probability:.2f}**")
            if s.conditioning_event:
                out.append(f"  - Conditional on: `{s.conditioning_event}`")
            out.append(f"- Market price (unconditional): **{s.raw_market_price:.2f}**")
            out.append(f"- Matched market: [{s.market_question}]({s.market_url}) ({s.market_platform})")
            out.append(f"- **Caveat:** {s.comparison_caveat}")
            out.append("")

    # --- Tension panel
    out.append("## 3. In-document tensions (no market data needed)")
    out.append("")
    if not report.tension_panel:
        out.append("_No internal contradictions detected in the wargame's narrative context._")
        out.append("")
    else:
        out.append(
            "Contradictions surfaced from the wargame source's own narrative — the analyst "
            "or extractor flagged a tension between two parts of the source document. These "
            "are auditable without any market data."
        )
        out.append("")
        for t in report.tension_panel:
            out.append(f"### `{t.branch_id}` ⚠")
            out.append("")
            out.append(f"- Detected via keywords: {', '.join(f'`{k}`' for k in t.matched_keywords)}")
            if t.citation:
                out.append(f"- Citation: _{t.citation}_")
            out.append("")
            out.append("> " + t.tension_text.replace("\n", "\n> "))
            out.append("")

    # --- No-match list
    if report.no_match_branches:
        out.append("## No plausible market match")
        out.append("")
        out.append(
            "The matcher could not find a prediction-market candidate that survives "
            "the conditional-structure, horizon, and specificity checks. Most policy-"
            "analysis wargames have many such branches — see `data/forecasts/<ns>/sidecar.json` "
            "for Metaculus references where they exist."
        )
        out.append("")
        for bid in report.no_match_branches:
            out.append(f"- `{bid}`")
        out.append("")

    if report.no_approval_branches:
        out.append("## Awaiting reviewer approval")
        out.append("")
        out.append(
            "The matcher returned candidates but no human reviewer has approved one yet. "
            "Run `python -m src.matching.overrides data/matches/<ns>/matches.json` to review."
        )
        out.append("")
        for bid in report.no_approval_branches:
            out.append(f"- `{bid}`")
        out.append("")

    return "\n".join(out)


def write_markdown(report: AuditReport, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render_markdown(report))


def _print_summary(report: AuditReport) -> None:
    print(f"\n=== AUDIT REPORT — {report.wargame_name} ===")
    print(f"assumptions:        {report.n_assumptions_total}")
    print(f"approved matches:   {report.n_assumptions_approved}")
    print(f"strict panel:       {len(report.strict_panel)}  (plotted on 2x2)")
    print(f"structured panel:   {len(report.structured_panel)}  (review-required)")
    print(f"tension panel:      {len(report.tension_panel)}  (in-document inconsistencies)")
    print(f"no_match branches:  {len(report.no_match_branches)}")
    print(f"no_approval (matches not yet reviewed): {len(report.no_approval_branches)}")
    print()
    if report.strict_panel:
        print("--- strict disagreements (markets-vs-wargame) ---")
        for d in sorted(report.strict_panel, key=lambda d: -d.abs_delta):
            sens = next(
                (b.sensitivity for b in report.branches if b.branch_id == d.branch_id), 0.0
            )
            arrow = "↑" if d.delta > 0 else "↓"
            in_action = " ★" if d.branch_id in report.action_list else "  "
            print(
                f"  {arrow}{abs(d.delta):.2f} {in_action} {d.branch_id:<45}"
                f"  wargame={d.wargame_probability:.2f}  market={d.market_price_compared:.2f}"
                f"  sens={sens:.2f}  pol={d.polarity_applied}"
            )
    if report.action_list:
        print(f"\n--- action list (top-right quadrant) ---")
        for bid in report.action_list:
            print(f"  ★ {bid}")
    if report.tension_panel:
        print("\n--- in-document tensions ---")
        for t in report.tension_panel:
            print(f"  ⚠ {t.branch_id}  (matched: {', '.join(t.matched_keywords)})")
            print(f"     {t.tension_text[:150]}")
            if t.citation:
                print(f"     citation: {t.citation}")
    if report.structured_panel:
        print("\n--- structured comparisons (apples-to-oranges flagged) ---")
        for s in report.structured_panel:
            cond_note = f" | conditional on: {s.conditioning_event}" if s.conditioning_event else ""
            print(f"  ~ {s.branch_id:<45}  wargame={s.wargame_probability:.2f}  market={s.raw_market_price:.2f}{cond_note}")


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.analysis.audit")
    p.add_argument("wargame_path")
    p.add_argument("--out", help="JSON output path (default: data/audits/<ns>/audit.json)")
    p.add_argument(
        "--markdown",
        nargs="?",
        const="__default__",
        help="also write a Markdown report. Pass a path or omit for "
        "data/audits/<ns>/audit.md",
    )
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    report = run_audit(args.wargame_path)
    out_path = Path(args.out) if args.out else audit_path_for(args.wargame_path)
    write_report(report, out_path)

    md_path: Path | None = None
    if args.markdown is not None:
        md_path = (
            Path(args.markdown)
            if args.markdown != "__default__"
            else out_path.with_suffix(".md")
        )
        write_markdown(report, md_path)

    if not args.quiet:
        _print_summary(report)
    print(f"\nwrote {out_path}")
    if md_path:
        print(f"wrote {md_path}")


if __name__ == "__main__":
    _cli()
