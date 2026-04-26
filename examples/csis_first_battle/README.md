# CSIS First Battle of the Next War — Demo Input

`wargame.yaml` is a structured representation of the CSIS report:

> Cancian, M. F., Cancian, M., & Heginbotham, E. (January 2023).
> *The First Battle of the Next War: Wargaming a Chinese Invasion of Taiwan.*
> Center for Strategic and International Studies.
> https://csis-website-prod.s3.amazonaws.com/s3fs-public/publication/230109_Cancian_FirstBattle_NextWar.pdf

## Extraction provenance

Assumptions are **manually extracted from the original report** by reading the
PDF and translating the report's qualitative base-case judgments into the
audit tool's structured schema. Each branch carries a `citation` field
pointing to the page(s) in the CSIS report where the assumption is discussed,
so a reader can verify the extraction directly against source.

Primary sources within the report:

- **Executive Summary** (pp. 1–5) — overall outcome assumptions, the four
  conditions for success, and the warning against striking the Chinese
  mainland.
- **Chapter 4: Assumptions — Base Cases and Excursion Cases** (pp. 52–82),
  particularly:
  - **Table 2** (pp. 53–54) — the explicit base-case / excursion table.
  - **Grand Strategic Assumptions: Political Context and Decision** (pp. 54–63)
    — the qualitative reasoning behind each base case.

Tactical and operational base cases (force exchange ratios, missile
effectiveness, ship survivability) are **not extracted** — they don't map
to prediction-market questions and would dilute the audit signal. The audit
tool focuses on strategic-political assumptions where market coverage
exists.

## Probability values

The CSIS report does **not** assign explicit numeric probabilities to its
base cases. The report uses qualitative language: a base case is "the most
likely value of a given variable" and "more likely than other possibilities"
but "does not mean certain" (p. 52).

The probability values in `wargame.yaml` are therefore **the auditor's
interpretation** of the report's qualitative judgments, calibrated as
follows:

- **0.75–0.85** — base case strongly defended in the text, hedging language
  absent or minimal, no excursion case strongly developed.
- **0.55–0.70** — base case defended but the report acknowledges material
  uncertainty (multiple excursions explored, hedging language present).
- **0.85–0.95** — implicit assumptions the report treats as near-certainties
  for the timeline modeled (e.g., conflict remaining sub-nuclear for the
  first 30 days).

These are exactly the kind of unstated input the audit tool is designed to
surface for review. A wargame's conclusion is conditional on the analyst's
implicit probabilities — translating them into numbers, citing them, and
auditing them against external evidence is the point.

## What this fixture is and is not

- It is **a structured representation** of the CSIS report's stated
  assumptions, with page-level citations.
- It is **not the CSIS report's official position** — CSIS did not publish
  numeric probabilities and the auditor's interpretive layer should be
  reviewed before any conclusions are drawn from the audit output.
- It is **not exhaustive** — operational/tactical base cases are
  intentionally omitted. ~21 strategic-political branches; the report has
  many more variables that don't map to market-priced questions.
- It is **a 2023 snapshot** — the report's base-case judgments reflect the
  geopolitical context at time of writing. Re-running the audit against
  current market prices is part of the value, but neither the CSIS
  judgments nor the auditor's probability assignments have been updated.

## Re-extracting

If the schema changes or the report is updated, re-extract by:

1. Reading the executive summary (pp. 1–5) for outcome-level assumptions.
2. Walking Table 2 (pp. 53–54) row by row for explicit base/excursion pairs.
3. Reading the supporting prose (pp. 54–63 for Grand Strategic) to calibrate
   the auditor's probability assignment.
4. Citing each branch with the page(s) where the assumption is discussed.

The pipeline downstream (`src/ingest/wargame_loader.py` →
`src/ingest/assumption_extractor.py`) consumes any conformant `wargame.yaml`
without modification.
