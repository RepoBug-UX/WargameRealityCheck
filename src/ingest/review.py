"""Interactive review of extracted wargame assumptions.

Per assumption: show the record, prompt for accept / reject / edit / skip / quit.
Writes the resulting list back to the same file with the `approved` flag set.
The matcher (Phase 4) consumes only `approved == True` records.

Non-interactive helpers `--auto-accept` and `--reject-all` exist to make
end-to-end pipeline tests possible without a human in the loop. They are
not a substitute for review on real wargames.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .types import WargameAssumption


def _print_panel(a: WargameAssumption, idx: int, total: int) -> None:
    print()
    print(f"========== ASSUMPTION {idx}/{total} ==========")
    print(f"branch_id:    {a.branch_id}")
    print(f"domain:       {a.domain:<22}horizon: {a.horizon or 'n/a'}")
    print(f"probability:  {a.wargame_probability:.2f}")
    print(f"question:     {a.question_text}")
    if a.narrative_context:
        print("narrative:    " + _wrap(a.narrative_context, indent=14))
    else:
        print("narrative:    (none — passthrough extraction adds none)")
    if a.dependencies:
        print(f"dependencies: {', '.join(a.dependencies)}")
    print(f"extracted via: {a.extracted_via}")
    print(f"current approved: {a.approved}")


def _wrap(text: str, *, indent: int, width: int = 78) -> str:
    import textwrap

    body_width = max(20, width - indent)
    paragraphs = text.splitlines() or [text]
    out_lines: list[str] = []
    for i, para in enumerate(paragraphs):
        wrapped = textwrap.wrap(para, width=body_width) or [""]
        for j, line in enumerate(wrapped):
            if i == 0 and j == 0:
                out_lines.append(line)
            else:
                out_lines.append(" " * indent + line)
    return "\n".join(out_lines)


def _prompt_edit(a: WargameAssumption) -> WargameAssumption:
    print("(press Enter to keep current value)")
    new_q = input(f"  question_text [{a.question_text[:60]}...]: ").strip()
    new_p_raw = input(f"  probability [{a.wargame_probability:.2f}]: ").strip()
    new_p = a.wargame_probability
    if new_p_raw:
        try:
            new_p = float(new_p_raw)
            if not 0.0 <= new_p <= 1.0:
                raise ValueError("out of range")
        except ValueError as e:
            print(f"  (invalid probability: {e}, keeping {a.wargame_probability})")
            new_p = a.wargame_probability
    return a.model_copy(
        update={
            "question_text": new_q or a.question_text,
            "wargame_probability": new_p,
            "approved": True,  # editing implies approval
        }
    )


def review(
    assumptions_path: str | Path,
    *,
    auto_accept: bool = False,
    reject_all: bool = False,
) -> list[WargameAssumption]:
    p = Path(assumptions_path)
    raw = json.loads(p.read_text())
    assumptions = [WargameAssumption.model_validate(item) for item in raw]

    if auto_accept and reject_all:
        raise ValueError("cannot combine --auto-accept and --reject-all")

    if auto_accept:
        out = [a.model_copy(update={"approved": True}) for a in assumptions]
        _save(out, p)
        print(f"auto-accepted {len(out)} assumptions  →  {p}")
        return out
    if reject_all:
        out = [a.model_copy(update={"approved": False}) for a in assumptions]
        _save(out, p)
        print(f"rejected all {len(out)} assumptions  →  {p}")
        return out

    out: list[WargameAssumption] = []
    quit_early = False
    for i, a in enumerate(assumptions, start=1):
        if quit_early:
            out.append(a)  # leave remaining unchanged
            continue
        _print_panel(a, i, len(assumptions))
        while True:
            choice = input("\n[a]ccept  [r]eject  [e]dit  [s]kip  [q]uit > ").strip().lower()
            if choice in ("a", "accept"):
                out.append(a.model_copy(update={"approved": True}))
                break
            if choice in ("r", "reject"):
                out.append(a.model_copy(update={"approved": False}))
                break
            if choice in ("e", "edit"):
                out.append(_prompt_edit(a))
                break
            if choice in ("s", "skip"):
                out.append(a)
                break
            if choice in ("q", "quit"):
                out.append(a)
                quit_early = True
                break
            print("(unrecognized — pick a / r / e / s / q)")

    _save(out, p)
    n_approved = sum(1 for a in out if a.approved)
    print(f"\nreview complete: {n_approved}/{len(out)} approved  →  {p}")
    return out


def _save(assumptions: list[WargameAssumption], path: Path) -> None:
    path.write_text(
        json.dumps([a.model_dump(mode="json") for a in assumptions], indent=2)
    )


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.ingest.review")
    p.add_argument("assumptions_path")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--auto-accept", action="store_true", help="approve everything (testing only)")
    grp.add_argument("--reject-all", action="store_true", help="reject everything (testing only)")
    args = p.parse_args()

    if not sys.stdin.isatty() and not (args.auto_accept or args.reject_all):
        print("review requires a tty unless --auto-accept or --reject-all is set", file=sys.stderr)
        sys.exit(2)
    review(args.assumptions_path, auto_accept=args.auto_accept, reject_all=args.reject_all)


if __name__ == "__main__":
    _cli()
