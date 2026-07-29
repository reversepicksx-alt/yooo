---
name: Projection line-anchor bug
description: Why projectedValue used to show exactly line±0.5 instead of the real Bayesian estimate, and how to prevent it recurring.
---

## The bug
Two blocks in `backend/routes/predict.py` hardcoded `line ± 0.5` as the `projectedValue` override:

1. **CONSISTENCY GUARD** (~line 7087): fires when `rec=UNDER` but `projectedValue > line` (or OVER + proj < line). Overwrote with `round((req.line - 0.5) * 2) / 2`.
2. **BAYESIAN TRUTH direction flip** (~line 7165): fires when Bayesian Truth changes the recommendation direction. Same hardcoded formula.

Because these blocks anchor to the sportsbook line, the displayed projection tracks the line perfectly (proj = line − 0.5) regardless of player stats. A user testing T. Porra/pass_attempts with lines 29.5 and 31.5 saw projections of 29.0 and 31.0 — always exactly line − 0.5.

**Root cause chain:**
1. League calibration + press boost push the Bayesian posterior slightly above the market line.
2. Bayesian Truth flips recommendation to UNDER (P(UNDER) > 50% even though mean > line).
3. The guard/flip sees `projectedValue > line, rec=UNDER` and "fixes" it to `line − 0.5`.

## The fix
Both blocks now use `real_bayes.get("posteriorMean")` (the independently computed Bayesian mean, with only 20% market fusion) instead of `line ± 0.5`. Falls back to `line ± 0.5` only when `real_bayes` is unavailable.

**Why:** The projected value must reflect the player's real statistical level. `line ± 0.5` is a fake display number that happens to change every time the sportsbook line moves.

## How to apply
- Never add new code of the form `prediction["projectedValue"] = round((req.line ± 0.5) * 2) / 2` unless it is explicitly a hard-block for a specific prop (e.g. clearances OVER 0% hit rate) where the projection is irrelevant.
- When any gate flips `recommendation`, always derive the corrected `projectedValue` from `real_bayes["posteriorMean"]`, not from `req.line`.
- `real_bayes` is the dict returned from `compute_bayesian_projection()`, available as a local in the predict handler.
