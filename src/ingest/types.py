"""Boundary types for the wargame ingestion → matching pipeline.

`Wargame` is the input-side representation: a hand-authored or Snow-Globe-derived
structure with branches and probabilities. `WargameAssumption` is the
extraction-side representation: one assumption ready to be matched against a
prediction market. Phase 4's matcher consumes lists of `WargameAssumption`.

Keep these types narrow. Anything wargame-runtime-specific (sensitivity loops,
outcome distributions) lives in `src/analysis/`, not here.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Domain tags help downstream filtering — strategic-political assumptions are
# the ones the tool is built to audit; tactical/logistics ones are usually
# off-target for prediction markets.
AssumptionDomain = Literal[
    "strategic-political",
    "operational-military",
    "tactical",
    "logistics",
    "economic",
    "technical",
    "other",
]

ExtractionSource = Literal["passthrough", "llm-enriched", "snowglobe-inferred"]


class WargameBranch(BaseModel):
    """One decision/outcome point in the wargame, as authored by the analyst."""

    model_config = ConfigDict(frozen=True)

    branch_id: str
    question: str  # natural-language form of the branch (raw, may be rough)
    probability: float = Field(ge=0.0, le=1.0)
    domain: AssumptionDomain = "other"
    horizon: str | None = None  # free-form, e.g. "72h", "first 7 days", "campaign"
    depends_on: list[str] = Field(default_factory=list)  # other branch_ids
    notes: str | None = None  # narrative context the analyst wants preserved
    # Provenance for extracted assumptions — page or section reference in
    # the source document (e.g., "CSIS p.55"). When present, the audit
    # output cites it so a reader can verify the extraction against source.
    citation: str | None = None

    @field_validator("branch_id")
    @classmethod
    def _no_whitespace(cls, v: str) -> str:
        if not v or any(c.isspace() for c in v):
            raise ValueError("branch_id must be non-empty and contain no whitespace")
        return v


class Wargame(BaseModel):
    """The top-level wargame input — a name, a brief, and the branch set."""

    model_config = ConfigDict(frozen=True)

    name: str
    brief: str  # 1-3 sentence scenario summary; gives the LLM and reviewer context
    branches: list[WargameBranch]


class WargameAssumption(BaseModel):
    """An extracted, review-ready assumption.

    `question_text` is the canonical natural-language form (post-LLM
    canonicalization if applicable). `wargame_probability` is the analyst's
    or inferred probability. `narrative_context` is 1-2 sentences explaining
    why this assumption matters for the wargame's conclusion.
    """

    model_config = ConfigDict(frozen=True)

    branch_id: str
    question_text: str
    wargame_probability: float = Field(ge=0.0, le=1.0)
    domain: AssumptionDomain
    horizon: str | None
    dependencies: list[str]
    narrative_context: str
    citation: str | None = None
    extracted_via: ExtractionSource
    approved: bool = False  # set True after human review
