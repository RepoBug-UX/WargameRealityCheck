"""Interactive review of matcher output.

The matcher produces top-K candidates per assumption with confidence scores
and tier (matched / partial / no_match). This CLI walks the human reviewer
through them: accept the top candidate, pick a different candidate from the
top-K, reject, or skip.

The chosen candidate gets `approved=True`; others stay False. Phase 6's
analysis layer consumes only approved candidates. Non-interactive helpers
`--auto-accept-matched` and `--auto-accept-all` exist for end-to-end
pipeline testing.

A thoughtful default: `--auto-accept-matched` approves the top candidate of
every "matched" tier result and leaves "partial" and "no_match" alone for
human review. That's the realistic semi-automated workflow.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .matcher import MatchCandidate, MatchResult


def _print_panel(m: MatchResult, idx: int, total: int) -> None:
    glyph = {"matched": "✓", "partial": "~", "no_match": "✗"}[m.tier]
    print()
    print(f"========== ASSUMPTION {idx}/{total} ==========")
    print(f"branch_id:  {m.branch_id}")
    print(f"tier:       [{glyph}] {m.tier}")
    if m.tier == "no_match":
        print(f"reason:     {m.no_match_reason}")
    if not m.candidates:
        print("(no candidates returned)")
        return
    print(f"\ntop {len(m.candidates)} candidate(s):")
    for i, c in enumerate(m.candidates, start=1):
        flag = " ★" if c.approved else "  "
        print(f"  {i}.{flag} [{c.market_platform:>10}] combined={c.scores.combined:.2f}  "
              f"sem={c.scores.semantic:.2f}  cond={c.scores.conditional_structure:.2f}  "
              f"hor={c.scores.time_horizon:.2f}  spec={c.scores.specificity:.2f}")
        print(f"        {c.market_question[:90]}")
        if c.scores.explain:
            for e in c.scores.explain:
                print(f"        caveat: {e}")


def _approve_index(
    m: MatchResult, idx: int, polarity: str = "aligned"
) -> MatchResult:
    """Set candidates[idx].approved=True with the given polarity; clear others."""
    if idx < 0 or idx >= len(m.candidates):
        raise IndexError(idx)
    new_cands: list = []
    for i, c in enumerate(m.candidates):
        if i == idx:
            new_cands.append(c.model_copy(update={"approved": True, "polarity": polarity}))
        else:
            new_cands.append(c.model_copy(update={"approved": False}))
    return m.model_copy(update={"candidates": new_cands})


def _reject_all(m: MatchResult) -> MatchResult:
    new_cands = [c.model_copy(update={"approved": False}) for c in m.candidates]
    return m.model_copy(update={"candidates": new_cands})


def _save(matches: list[MatchResult], path: Path) -> None:
    path.write_text(json.dumps([m.model_dump(mode="json") for m in matches], indent=2))


def review(
    matches_path: str | Path,
    *,
    auto_accept_matched: bool = False,
    auto_accept_all: bool = False,
) -> list[MatchResult]:
    p = Path(matches_path)
    raw = json.loads(p.read_text())
    matches = [MatchResult.model_validate(item) for item in raw]

    if auto_accept_matched and auto_accept_all:
        raise ValueError("cannot combine --auto-accept-matched and --auto-accept-all")

    if auto_accept_matched:
        out: list[MatchResult] = []
        for m in matches:
            if m.tier == "matched" and m.candidates:
                out.append(_approve_index(m, 0))
            else:
                out.append(m)
        _save(out, p)
        n_app = sum(1 for m in out if any(c.approved for c in m.candidates))
        print(f"auto-accepted top of {n_app} matched results  →  {p}")
        return out

    if auto_accept_all:
        out = []
        for m in matches:
            if m.candidates:
                out.append(_approve_index(m, 0))
            else:
                out.append(m)
        _save(out, p)
        n_app = sum(1 for m in out if any(c.approved for c in m.candidates))
        print(f"auto-accepted top of {n_app} results (including partial / no_match)  →  {p}")
        return out

    out = []
    quit_early = False
    for i, m in enumerate(matches, start=1):
        if quit_early:
            out.append(m)
            continue
        _print_panel(m, i, len(matches))
        if not m.candidates:
            print("(no candidates — skipping)")
            out.append(m)
            continue
        prompt = (
            "\n[1-N]   approve as aligned polarity (market YES = assumption YES)\n"
            "[1-N!]  approve as INVERTED polarity (market YES = assumption NO)\n"
            "[1-N?]  approve as UNCLEAR polarity (Phase 6 will not auto-compute)\n"
            "[r]eject all   [s]kip   [q]uit > "
        )
        while True:
            choice = input(prompt).strip().lower()
            if choice in ("s", "skip", ""):
                out.append(m)
                break
            if choice in ("r", "reject"):
                out.append(_reject_all(m))
                break
            if choice in ("q", "quit"):
                out.append(m)
                quit_early = True
                break
            polarity = "aligned"
            num_str = choice
            if choice.endswith("!"):
                polarity = "inverted"
                num_str = choice[:-1]
            elif choice.endswith("?"):
                polarity = "unclear"
                num_str = choice[:-1]
            try:
                idx = int(num_str) - 1
                out.append(_approve_index(m, idx, polarity=polarity))
                break
            except (ValueError, IndexError):
                print(f"(unrecognized — pick 1-{len(m.candidates)}[!?] / r / s / q)")
    _save(out, p)
    n_app = sum(1 for m in out if any(c.approved for c in m.candidates))
    print(f"\nreview complete: {n_app}/{len(out)} approved  →  {p}")
    return out


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.matching.overrides")
    p.add_argument("matches_path")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--auto-accept-matched", action="store_true",
                     help="approve top candidate of all 'matched' tier results")
    grp.add_argument("--auto-accept-all", action="store_true",
                     help="approve top candidate of every result (testing only)")
    args = p.parse_args()

    if not sys.stdin.isatty() and not (args.auto_accept_matched or args.auto_accept_all):
        print("review requires a tty unless --auto-accept-matched or --auto-accept-all is set",
              file=sys.stderr)
        sys.exit(2)
    review(
        args.matches_path,
        auto_accept_matched=args.auto_accept_matched,
        auto_accept_all=args.auto_accept_all,
    )


if __name__ == "__main__":
    _cli()
