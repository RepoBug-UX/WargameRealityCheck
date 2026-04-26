"""Detect in-document inconsistencies — the second disagreement axis.

The signal here is the wargame source's own contradictions, surfaced from
the analyst's own narrative_context / notes. Auditable from the wargame
input alone, valuable even when no market match exists.

This is a *different epistemic object* from market-vs-wargame disagreement
and never combines into a single score. Output is a separate panel.

v1 implementation: keyword pattern matching against narrative_context.
The CSIS extraction surfaces tensions explicitly with phrases like:
  "Tension in the report: Table 2 lists base case as 'Authorized,' but
   Chapter 7 recommendations explicitly warn 'Do not plan on striking
   the mainland'"

These patterns are stable enough to detect with a small keyword list.
A future LLM-based detector could find subtler contradictions.
"""
from __future__ import annotations

import re

from ..ingest.types import WargameAssumption
from .types import InternalTension

# Phrases that the analyst typically uses when flagging an in-document
# inconsistency. Match case-insensitively. Order is intentional —
# more-specific phrases first.
_TENSION_PATTERNS = [
    "tension in",
    "internal contradiction",
    "internal tension",
    "but chapter",
    "but table",
    "contradicts",
    "contradiction between",
    "conflict between",
    "inconsistent with",
    "inconsistency",
    "however, chapter",
    "however, table",
]

_PATTERN_RE = re.compile(
    r"|".join(re.escape(p) for p in _TENSION_PATTERNS),
    re.IGNORECASE,
)


def detect_for_branch(assumption: WargameAssumption) -> list[InternalTension]:
    text = assumption.narrative_context or ""
    if not text:
        return []
    matches = list(_PATTERN_RE.finditer(text))
    if not matches:
        return []
    matched_keywords = sorted({m.group(0).lower() for m in matches})
    # Pull a tight excerpt around the first match — enough context to
    # render in a panel without dumping the whole narrative.
    first = matches[0]
    start = max(0, first.start() - 40)
    end = min(len(text), first.end() + 200)
    excerpt = text[start:end].strip()
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(text):
        excerpt = excerpt + "…"
    return [
        InternalTension(
            branch_id=assumption.branch_id,
            tension_text=excerpt,
            matched_keywords=matched_keywords,
            citation=assumption.citation,
        )
    ]


def detect_all(assumptions: list[WargameAssumption]) -> list[InternalTension]:
    out: list[InternalTension] = []
    for a in assumptions:
        out.extend(detect_for_branch(a))
    return out
