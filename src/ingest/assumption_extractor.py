"""Wargame branches → review-ready WargameAssumption records.

Two paths:

  - passthrough — no LLM. Maps a structured `WargameBranch` directly into a
    `WargameAssumption`. Useful when the analyst's hand-authored questions
    are already prediction-market-shaped, and as the unconditional fallback
    when no Anthropic key is available.

  - llm-enriched — uses Claude Opus 4.7 to canonicalize question phrasing,
    write narrative context, and surface dependencies the analyst missed.
    Batches all branches in one call so the model can see cross-branch
    context. Falls back to passthrough on any failure.

The output of either path is a list of `WargameAssumption` records with
`approved=False`. Human review (src/ingest/review.py) is the gate before
matching consumes them.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .. import _env
from .types import (
    ExtractionSource,
    Wargame,
    WargameAssumption,
    WargameBranch,
)
from .wargame_loader import load_wargame

_env.load()

DEFAULT_MODEL = "claude-opus-4-7"


def _passthrough_assumption(branch: WargameBranch) -> WargameAssumption:
    return WargameAssumption(
        branch_id=branch.branch_id,
        question_text=branch.question,
        wargame_probability=branch.probability,
        domain=branch.domain,
        horizon=branch.horizon,
        dependencies=list(branch.depends_on),
        narrative_context=branch.notes or "",
        citation=branch.citation,
        extracted_via="passthrough",
    )


def extract_passthrough(wargame: Wargame) -> list[WargameAssumption]:
    return [_passthrough_assumption(b) for b in wargame.branches]


def _llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


_SYSTEM_PROMPT = (
    "You are auditing a wargame's probability assumptions against live "
    "prediction-market prices. Your job is to canonicalize each wargame "
    "branch into a clear, externally-verifiable question — the kind that "
    "could plausibly appear on Polymarket or Kalshi — and to explain why "
    "the branch matters for the wargame's strategic conclusion. Be terse "
    "and concrete. Avoid hedging language. Do not change the analyst's "
    "probability — that's their input."
)


def _build_user_prompt(wargame: Wargame) -> str:
    branches_payload = [
        {
            "branch_id": b.branch_id,
            "question": b.question,
            "probability": b.probability,
            "domain": b.domain,
            "horizon": b.horizon,
            "depends_on": b.depends_on,
            "notes": b.notes,
        }
        for b in wargame.branches
    ]
    return (
        f"Wargame name: {wargame.name}\n"
        f"Brief:\n{wargame.brief}\n\n"
        f"Branches (JSON):\n{json.dumps(branches_payload, indent=2)}\n\n"
        "For each branch, produce an object with keys:\n"
        "  branch_id (echo)\n"
        "  canonical_question (one sentence, externally-verifiable, market-shaped)\n"
        "  narrative_context (1-2 sentences on why this branch matters for the conclusion)\n"
        "  additional_dependencies (list of other branch_ids this plausibly depends on; "
        "may be empty)\n\n"
        "Respond with ONLY a JSON array of these objects. No prose, no markdown fences."
    )


def _parse_llm_response(text: str) -> list[dict[str, Any]]:
    # Strip optional code fences defensively even though we asked for raw JSON.
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
        s = s.rsplit("```", 1)[0]
    return json.loads(s)


def extract_with_llm(
    wargame: Wargame, *, model: str = DEFAULT_MODEL
) -> list[WargameAssumption]:
    """Enrich each branch via Claude. Returns passthrough output on any failure."""
    try:
        import anthropic
    except ImportError:
        return _annotate_source(extract_passthrough(wargame), "passthrough")

    client = anthropic.Anthropic()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(wargame)}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        enriched = _parse_llm_response(text)
    except Exception:
        # LLM failure should never block extraction. Log via stderr in the CLI;
        # the library path stays silent and falls back honestly.
        return _annotate_source(extract_passthrough(wargame), "passthrough")

    by_id: dict[str, dict[str, Any]] = {}
    for item in enriched:
        if isinstance(item, dict) and "branch_id" in item:
            by_id[str(item["branch_id"])] = item

    out: list[WargameAssumption] = []
    for branch in wargame.branches:
        e = by_id.get(branch.branch_id)
        if not e:
            out.append(_passthrough_assumption(branch))
            continue
        deps = list(branch.depends_on)
        for d in e.get("additional_dependencies", []) or []:
            if d not in deps and d != branch.branch_id:
                deps.append(str(d))
        out.append(
            WargameAssumption(
                branch_id=branch.branch_id,
                question_text=str(e.get("canonical_question") or branch.question),
                wargame_probability=branch.probability,  # never let LLM rewrite this
                domain=branch.domain,
                horizon=branch.horizon,
                dependencies=deps,
                narrative_context=str(e.get("narrative_context") or branch.notes or ""),
                citation=branch.citation,  # provenance is the analyst's, not the LLM's
                extracted_via="llm-enriched",
            )
        )
    return out


def _annotate_source(
    assumptions: list[WargameAssumption], source: ExtractionSource
) -> list[WargameAssumption]:
    return [a.model_copy(update={"extracted_via": source}) for a in assumptions]


def extract(wargame: Wargame, *, prefer_llm: bool = True) -> list[WargameAssumption]:
    if prefer_llm and _llm_available():
        return extract_with_llm(wargame)
    return extract_passthrough(wargame)


def assumptions_path_for(wargame_path: str | Path) -> Path:
    """Where extracted assumptions are written for a given wargame input.

    Convention: each wargame lives in its own subdirectory (e.g.,
    `examples/csis_first_battle/wargame.yaml`), and the parent directory
    name becomes the namespace. This avoids collisions between demos that
    all name their input file `wargame.yaml`. For loose files in a generic
    parent (`examples/`, `data/wargames/`), we fall back to the file stem.
    """
    p = Path(wargame_path).resolve()
    repo = Path(__file__).resolve().parents[2]
    parent = p.parent.name
    generic = {"examples", "wargames", "data", ""}
    namespace = parent if parent and parent not in generic else p.stem
    return repo / "data" / "wargames" / namespace / "assumptions.json"


def write_assumptions(out_path: Path, assumptions: list[WargameAssumption]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([a.model_dump(mode="json") for a in assumptions], indent=2)
    )


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.ingest.assumption_extractor")
    p.add_argument("wargame_path")
    p.add_argument("--no-llm", action="store_true", help="skip LLM enrichment even if a key is set")
    p.add_argument("--out", help="output path (default: data/wargames/<name>/assumptions.json)")
    args = p.parse_args()

    wg = load_wargame(args.wargame_path)
    use_llm = (not args.no_llm) and _llm_available()
    if not use_llm and not args.no_llm:
        print("[no ANTHROPIC_API_KEY — running in passthrough mode]")
    assumptions = extract(wg, prefer_llm=use_llm)
    out_path = Path(args.out) if args.out else assumptions_path_for(args.wargame_path)
    write_assumptions(out_path, assumptions)

    via = assumptions[0].extracted_via if assumptions else "n/a"
    print(f"extracted {len(assumptions)} assumptions  [via: {via}]  →  {out_path}")
    for a in assumptions:
        print(f"  [{a.domain:>22}]  p={a.wargame_probability:.2f}  {a.branch_id}")
        print(f"      {a.question_text[:100]}")


if __name__ == "__main__":
    _cli()
