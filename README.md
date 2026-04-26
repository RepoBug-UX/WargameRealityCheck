# Wargame Reality Check

*Wargames depend on assumptions nobody priced. Markets price assumptions nobody wargamed. This tool puts them in the same room — and tells the analyst what kind of signal they're looking at.*

## Status

Work in progress. Phase 1 (prediction-market data layer) and Phase 2 (signal profile) implemented. See `/Users/justinli/.claude/plans/build-plan-wargame-abundant-waterfall.md` for the full plan.

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

Prediction-market prices aggregate two distinct kinds of costly signal:

1. **Distributed crowd judgment** — many traders, modest positions, distributed conviction. The price moves because many people independently update on public information.
2. **Informed-trader signal** — few traders, concentrated positions, sharp price moves often without public catalysts. The price moves because someone with private information is putting money down.

Both are useful for wargame auditing, but they mean different things. The tool does not classify markets as crowd-driven or insider-driven — that classification is unknowable from public data. Instead, the tool surfaces the *observable features* that distinguish the two patterns and lets the analyst interpret.

### What the tool measures

- **Position concentration** — how much of resting order-book liquidity sits in the top-1 / top-3 / top-10 levels.
- **Volume velocity** — 24-hour volume vs. a recent baseline (Kalshi only; see below).
- **Price-move events** — moves over a configurable threshold (default 5pt) within a rolling 24-hour window.
- **Cross-platform divergence** — when the same question is priced on both platforms, the gap between them.

A flat market — low volatility, no significant moves, narrow 30-day range — is a valid output, not a failure. It represents stable crowd consensus on the question. The tool surfaces this as a *quiet slow-converging* flag and reports the price as it would any other; the analyst should read it as "the market has converged on this number and isn't moving," not as "no signal."

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

## Demo

(To be added in Phase 8.)

## References

(To be added: Bartels, Downes-Martin, MORS WG; Atanasov et al, Mellers et al; SCSP/RAND wargaming AI work.)
