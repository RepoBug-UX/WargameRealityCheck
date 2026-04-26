# Asia Strategic Posture 2026 — Illustrative Synthetic Demo

`wargame.yaml` in this directory is **an illustrative fixture, not a real wargame.** It exists for one purpose: to demonstrate the audit pipeline visually on a densely-matched input.

## What this fixture is

- **Hand-authored** by the project author. Not extracted from any published wargame.
- **Branches deliberately chosen** to map to currently-liquid prediction markets on Polymarket and Kalshi (Taiwan invasion, Russia-Ukraine ceasefire, nuclear use, US-China tariffs, Federal Reserve, Israel-Iran, etc.).
- **Used to demonstrate** the assumption map, the populated 2x2 plot, the drilldown panels, and the stress-test workflow — all on a small input where the matcher is expected to find market matches for most branches.

## What this fixture is not

- **Not extracted from a real wargame.** A real analytical wargame would not look like this — most real wargame branches are conditional on a precipitating event having occurred and have no market counterpart. See `examples/csis_first_battle/` for the realistic case.
- **Not a forecast.** The probability values reflect a plausible analyst's prior — they are illustrative, not authoritative.
- **Not internally connected.** Most branches in a real wargame depend on each other (if invasion, then ...). The branches here are independent forward-looking questions about the strategic environment, chosen for market-mappability rather than scenario coherence.

## Why two demo inputs

The CSIS *First Battle* fixture (`examples/csis_first_battle/`) is the **honest demo** — it shows what the audit produces on a real wargame: sparse market coverage, a thinly-populated 2x2, and the no-match reporting + Metaculus sidecar doing most of the work.

This fixture is the **visual demo** — it shows the audit pipeline working end-to-end on a densely-matched input, so a viewer can see the UI, the disagreement scoring, and the stress-test workflow without first having to interpret a mostly-empty 2x2.

Both demos matter. Showing only the dense-match case would oversell the tool. Showing only the sparse-match case would make it hard to evaluate the analysis pipeline. Showing both is the honest pitch.
