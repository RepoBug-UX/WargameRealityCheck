# Wargame Reality Check

*Wargames depend on assumptions nobody priced. Markets price assumptions nobody wargamed. This tool puts them in the same room — and tells the analyst what kind of signal they're looking at.*

## Status

Work in progress. Phase 1 (prediction-market data layer) and Phase 2 (signal profile) implemented. See  for the full plan.

## Problem

Wargames produce conclusions that depend on probability assumptions buried in their branches: *will Japan grant base access in 72 hours? will Germany hold the embargo? will the cyber retaliation stay sub-kinetic?* Most of these probabilities are inherited from the analyst's prior or pulled from doctrine — rarely externally checked. Meanwhile, prediction markets price exactly these kinds of questions, with traders putting money behind their positions.

## Approach

1. Take a wargame (Snow Globe history JSON or a hand-authored YAML/JSON spec).
2. Extract its probability assumptions (LLM-assisted, human-reviewed).
3. Match each assumption to a prediction market on Polymarket or Kalshi.
4. For each matched assumption, surface the disagreement (correctness axis) and the sensitivity (consequence axis), and place it on a 2x2.
5. The action list is the top-right quadrant: high disagreement *and* high sensitivity.

Alongside each disagreement number, the tool surfaces the matched market's *signal profile* — observable features about how the price was made (liquidity dispersion, volume velocity, recent price-move pattern). The analyst interprets, not the tool.

## Epistemic stance

The tool's primary signal is **prediction markets** — Polymarket and Kalshi — where traders put money behind positions. This is a costly signal. Other forecasting platforms (Metaculus) are treated as a distinct, sidecar reference, never as a substitute, and never enter the disagreement score.

The Metaculus-as-sidecar treatment is **methodological, not access-driven.** Even with full Metaculus access, the sidecar role would be appropriate because forecasting-platform signals are a different epistemic object — *reputation-based*, with calibrated forecasters putting their public track record at stake — than market prices, where traders put their wallet at stake. The two signals are both useful and they correlate, but they're produced by different incentive structures and they fail in different ways. As of early 2026, Metaculus's default API access tier returns question metadata and forecaster counts but withholds community-prediction values; the sidecar surfaces what's accessible (URL, question text, forecaster count) with an explicit "click through to view the live aggregate" caveat. The current API access tier reinforces but does not determine the asymmetry.

Prediction-market prices aggregate two distinct kinds of costly signal:

1. **Distributed crowd judgment** — many traders, modest positions, distributed conviction. The price moves because many people independently update on public information.
2. **Informed-trader signal** — few traders, concentrated positions, sharp price moves often without public catalysts. The price moves because someone with private information is putting money down.

Both are useful for wargame auditing, but they mean different things. The tool does not classify markets as crowd-driven or insider-driven — that classification is unknowable from public data. Instead, the tool surfaces the *observable features* that distinguish the two patterns and lets the analyst interpret.

### What the tool measures

- **Position concentration** — how much of resting order-book liquidity sits in the top-1 / top-3 / top-10 levels.
- **Volume velocity** — 24-hour volume vs. a recent baseline (Kalshi only; see below).
- **Price-move events** — moves over a configurable threshold (default 5pt) within a rolling 24-hour window.
- **Cross-platform divergence** — when the same question is priced on both platforms, the gap between them.
- **Structural sensitivity** — for each assumption, the fraction of other branches that transitively depend on it. A trigger event in a Taiwan wargame (the invasion itself) scores near 1.0 because most other branches are conditional on it; an isolated forward-looking question scores near 0.

Sensitivity is computed **structurally via the wargame's dependency graph, not via outcome simulation.** Most policy-analysis wargames are not runnable models — they are structured arguments with branch-level probabilities — so simulating an outcome distribution under perturbed assumptions is not generally possible. Structural reach is the appropriate signal at this scope: it answers "how much of this wargame hangs on this assumption?" using the analyst's stated dependency structure, which is information they have actually committed to. A future tool that consumes Snow Globe-style runnable wargames could swap in a real perturb-and-resimulate loop without any other change to the audit pipeline.

A flat market — low volatility, no significant moves, narrow 30-day range — is a valid output, not a failure. It represents stable crowd consensus on the question. The tool surfaces this as a *quiet slow-converging* flag and reports the price as it would any other; the analyst should read it as "the market has converged on this number and isn't moving," not as "no signal."

### Coverage is sparse — graceful failure is the primary path

Most strategic-political assumptions in policy-analysis wargames are **conditional on the precipitating event having occurred** ("if invasion, what's the probability Japan grants basing within 72 hours?"). Prediction markets predominantly price **unconditional** outcomes ("will China invade Taiwan by Y?"). The tool's audit pipeline therefore covers a smaller subset of any given wargame's assumptions than the wargame contains — typically the unconditional or near-unconditional branches, plus the small set of conditional branches that happen to be priced (rare, generally only on Metaculus).

**The "no plausible market match" path is the primary path, not the edge case.** This is a property of the domain, not a deficiency of the tool. For the CSIS *First Battle* fixture (21 branches, 16 strategic-political), the realistic match rate is roughly 5-6 partial or strong matches — about 25-35%. The remaining branches will be reported with a clear "no market match found" label, with optional Metaculus sidecar references where they exist.

This means the Metaculus sidecar (Phase 5) often produces more useful drilldown content than the 2x2 plot itself, and the tool's value depends on the *honesty* of its no-match reporting as much as on the disagreement scoring of the matched subset.

### What the tool does not measure (and why)

- **Unique-trader count.** Neither Polymarket's nor Kalshi's public read API exposes a unique-trader count. Approximating it from order-book or trade-tape fingerprints would be noisy enough to mislead. We measure *liquidity dispersion* as a proxy for crowd structure — a reasonable but imperfect substitute (a single trader can place many small resting orders).
- **Volume velocity on Polymarket.** The Polymarket public price-history endpoint does not include per-point volume. We compute volume velocity for Kalshi (which provides per-candle volume) and report "unavailable" for Polymarket rather than fabricating a number.
- **Per-trader open-interest concentration.** Same reason as unique-trader count. We use order-book-derived concentration and label it as such.
- **News-event correlation.** Auto-correlating sharp price moves with public news is an unsolved retrieval problem. We support a manual annotation field for demo cases; we do not auto-classify moves as "with" or "without" a public catalyst.

### What this is not

- Not claiming markets are right and the wargame is wrong.
- Not auto-importing prices into the wargame.
- Not a forecasting tool — it audits, it doesn't predict.
- Not claiming uniform coverage across regions; fails gracefully where prediction-market coverage is thin.
- Not treating Metaculus as equivalent to prediction markets — it's a sidecar, methodologically distinct.
- Not auto-classifying markets as "crowd" or "insider" — surfaces features, lets the analyst decide.

## Audit output: three first-class components

A complete audit produces three structurally distinct outputs. The web UI in Phase 7 will render them as separate panels; the CLI Markdown report renders them as separate sections. **All three are first-class outputs — none is a fallback for the others.**

### 1. Strict disagreements (markets-vs-wargame, plotted on the 2x2)

Used when the matched candidate is `tier=matched` AND polarity is `aligned` (or correctly `inverted` with auto-flip applied). Single signed delta plus its absolute magnitude. Each strict disagreement carries the matched market's signal profile (concentration, volume velocity, recent price-move pattern) so the analyst sees the disagreement number alongside how the market price was made. These are the points on the 2x2 plot; the action list is the top-right quadrant — high disagreement *and* high structural sensitivity.

### 2. Structured comparisons (apples-to-oranges flagged)

Used when the matched candidate is `tier=partial` (typically conditional-vs-unconditional) or polarity is `unclear`. Captures the wargame's conditional probability and the market's unconditional price as **separate numbers**, with an explicit literal caveat ("these measure different things — wargame probability is conditional on X; market price is unconditional"). NOT plotted on the 2x2; lives in a separate review-required panel. This shape is non-negotiable: collapsing it into a single delta would be the apples-to-oranges error the tool is built to avoid.

### 3. In-document tensions (no market data needed)

A second axis of disagreement — places where the wargame source contradicts itself, surfaced from the analyst's own narrative_context with citations. Auditable from the wargame input alone, valuable even when no market match exists. The CSIS *First Battle* fixture's "U.S. strikes on mainland" branch produces one of these directly: Table 2 (p.54) lists the base case as "Authorized," but Chapter 7 (p.4) explicitly recommends "Do not plan on striking the mainland... The National Command Authority might withhold permission because of the grave risks of escalation." The tool catches this contradiction without consulting any market.

The three components complement each other; they never combine into a single score. A wargame can have zero strict disagreements (sparse market coverage), several structured comparisons (conditional branches with related markets), and one or two in-document tensions, and that is a complete and informative audit.

## Demo inputs

The repository ships two structurally different demo wargames. They serve different purposes and read very differently in the audit output.

### `examples/csis_first_battle/` — real wargame, sparse-match demo

A manually-extracted structured representation of the CSIS report *The First Battle of the Next War: Wargaming a Chinese Invasion of Taiwan* (Cancian, Cancian, & Heginbotham, January 2023). 21 branches with page-level citations to the source report. Probability values are this auditor's interpretation of CSIS's qualitative base-case judgments — see `examples/csis_first_battle/README.md` for the extraction methodology.

This is the **honest demo**: most branches are conditional on the invasion having occurred, market coverage is sparse, the 2x2 plot will be thinly populated, and the tool's no-match reporting and Metaculus sidecar do most of the work. Use it to evaluate whether the tool is being honest about what it can and cannot audit.

### `examples/taiwan_synthetic_illustrative/` — synthetic wargame, dense-match demo

A small hand-authored fixture whose branches were **deliberately written to map cleanly to currently-liquid prediction markets**. Used to demonstrate the visual pipeline: assumption map, populated 2x2, drilldown panels, stress-test workflow.

This is **not extracted from a real wargame** and should not be read as one. It exists so a viewer can see the audit pipeline working end-to-end on a densely-matched input. Use it to evaluate the tool's UI and the disagreement/sensitivity computation; do not use it to make claims about how often real wargames produce dense matches (they don't — see the CSIS fixture for the realistic case).

## References

(To be added: Bartels, Downes-Martin, MORS WG; Atanasov et al, Mellers et al; SCSP/RAND wargaming AI work.)
