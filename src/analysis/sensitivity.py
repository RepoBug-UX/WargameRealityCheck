"""Sensitivity proxy via dependency-graph reach.

True sensitivity analysis would require re-running the wargame with the
target assumption's probability perturbed. Hand-authored YAML wargames
aren't runnable simulations — there's no outcome model that takes branch
probabilities and produces an outcome distribution.

We use a tractable structural proxy: an assumption's sensitivity is the
fraction of other branches that depend (transitively) on it. A trigger
branch like `prc_invasion_2026` will score near 1.0 because most other
branches in a Taiwan wargame derive from it. An isolated branch like
`india_singapore_thailand_vietnam_passive` will score low because nothing
downstream depends on it.

This is honest about what it measures: structural reach, not outcome
shift. The signal is informative — "many things change with this
assumption" is a genuine sensitivity property — but it is not a
substitute for a proper outcome simulation, and the README/docs should
say so.
"""
from __future__ import annotations

from ..ingest.types import WargameAssumption


def downstream_count(branch_id: str, assumptions: list[WargameAssumption]) -> int:
    """Count assumptions that transitively depend on `branch_id`."""
    by_id = {a.branch_id: a for a in assumptions}
    if branch_id not in by_id:
        return 0
    visited: set[str] = set()
    queue = [branch_id]
    while queue:
        current = queue.pop()
        for a in assumptions:
            if current in a.dependencies and a.branch_id not in visited:
                visited.add(a.branch_id)
                queue.append(a.branch_id)
    return len(visited)


def sensitivity_score(branch_id: str, assumptions: list[WargameAssumption]) -> float:
    """Normalize downstream count to [0, 1] by total potential dependents."""
    n = len(assumptions)
    if n <= 1:
        return 0.0
    return downstream_count(branch_id, assumptions) / (n - 1)


def sensitivity_for_all(
    assumptions: list[WargameAssumption],
) -> dict[str, float]:
    return {a.branch_id: sensitivity_score(a.branch_id, assumptions) for a in assumptions}
