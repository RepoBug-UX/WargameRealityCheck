"""Load a hand-authored wargame from YAML or JSON into typed `Wargame` records.

Expected file shape (YAML form):

    name: First Battle — Taiwan 2027
    brief: |
      A condensed structured representation of the CSIS First Battle wargame.
      Each branch is a strategic-political decision point with the analyst's
      assumed probability.
    branches:
      - branch_id: japan_base_access_72h
        question: Japan grants U.S. forces base access within 72 hours of the invasion
        probability: 0.85
        domain: strategic-political
        horizon: 72h
        depends_on: []
        notes: Premised on a credible attack signature and a sitting LDP government.

JSON form is the same shape, just parsed by the json stdlib.

Validation: branch_ids must be unique and any `depends_on` entries must
reference an existing branch_id. Bad inputs raise immediately rather than
poisoning extraction.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from .types import Wargame, WargameBranch


class WargameLoadError(ValueError):
    pass


def load_wargame(path: str | Path) -> Wargame:
    p = Path(path)
    if not p.exists():
        raise WargameLoadError(f"wargame file not found: {p}")
    text = p.read_text()
    if p.suffix.lower() in (".yaml", ".yml"):
        raw = yaml.safe_load(text)
    elif p.suffix.lower() == ".json":
        raw = json.loads(text)
    else:
        raise WargameLoadError(f"unsupported wargame file extension: {p.suffix}")

    if not isinstance(raw, dict):
        raise WargameLoadError("top-level wargame document must be a mapping")

    branches_raw = raw.get("branches") or []
    if not isinstance(branches_raw, list) or not branches_raw:
        raise WargameLoadError("wargame must have a non-empty 'branches' list")

    branches: list[WargameBranch] = []
    seen_ids: set[str] = set()
    for i, b in enumerate(branches_raw):
        if not isinstance(b, dict):
            raise WargameLoadError(f"branch #{i} is not a mapping")
        try:
            branch = WargameBranch.model_validate(b)
        except Exception as e:  # pydantic ValidationError, etc.
            raise WargameLoadError(f"branch #{i} ({b.get('branch_id', '?')}) failed validation: {e}") from e
        if branch.branch_id in seen_ids:
            raise WargameLoadError(f"duplicate branch_id: {branch.branch_id}")
        seen_ids.add(branch.branch_id)
        branches.append(branch)

    # Cross-reference dependencies after the full set is loaded.
    for branch in branches:
        for dep in branch.depends_on:
            if dep not in seen_ids:
                raise WargameLoadError(
                    f"branch {branch.branch_id} depends_on unknown branch_id: {dep}"
                )

    return Wargame(
        name=str(raw.get("name") or p.stem),
        brief=str(raw.get("brief") or ""),
        branches=branches,
    )


def _cli() -> None:
    p = argparse.ArgumentParser(prog="src.ingest.wargame_loader")
    p.add_argument("path")
    args = p.parse_args()
    wg = load_wargame(args.path)
    print(f"loaded: {wg.name}")
    print(f"  brief: {wg.brief[:120]}{'...' if len(wg.brief) > 120 else ''}")
    print(f"  branches: {len(wg.branches)}")
    for b in wg.branches:
        deps = f"  ← {','.join(b.depends_on)}" if b.depends_on else ""
        print(f"    [{b.domain:>22}]  p={b.probability:.2f}  {b.branch_id}{deps}")


if __name__ == "__main__":
    _cli()
