---
name: Calibration hard-override danger
description: Why confidence_calibration.py must use James-Stein blending, not a hard override, and what thresholds to set.
---

## The rule
`confidence_calibration.py` must blend raw engine score with empirical hit rate using James-Stein shrinkage, never replace wholesale.

**Why:** At _MIN_BUCKET_N=20 with a hard override, a single thin bucket (20 picks) fully replaced a 87.9% raw confidence with a 52% empirical rate — a 36-point haircut on Rodri from 20 data points. This is statistically illiterate: the 95% CI on 20 picks is ±22pp.

## Current settings (as of 2026-07-15)
- `_MIN_BUCKET_N = 50` — bucket must have ≥50 picks before calibration fires at all
- `_BLEND_K = 50` — James-Stein shrinkage constant
- Formula: `shrink = n / (n + 50)`, output = `raw*(1-shrink) + empirical*shrink`
  - n=50: 50% weight on empirical
  - n=200: 80% empirical
  - n=500: 91% empirical
  - n=1675 (pass_attempts): 97% empirical

**Why blend_k=50:** Matches _MIN_BUCKET_N — at the minimum qualifying threshold (n=50) you get exactly 50% trust on the empirical signal, not full trust.

## Odds-tier priors companion setting
`odds_tier_priors.py _MIN_SAMPLE = 30` — aligned to James-Stein shrinkage constant k=30.  
At n<30, shrink<50% meaning the prior trust outweighs the data. Not worth firing.  
Raising this from 8→30 dropped total active buckets from 136 (63 coarse + 73 fine) to 41 (22 coarse + 19 fine), eliminating 95 noisy thin buckets.

## How to apply
- Never lower _MIN_BUCKET_N below 50 or add a hard-override return path in `calibrate()`.
- If adding a new calibration tier (e.g. position-split), apply the same blending pattern via `_blend()`.
- Nightly calibration (projection bias offsets, `calibration.py`) is intentionally DISABLED in `server.py` — do not re-enable without explicit user request and backtest validation.

## Backtest snapshot (soccer, 2026-07-15)
- Overall hit rate: 61.2% | OVER: 54.5% | UNDER: 65.1%
- pass_attempts: 2,434 picks, 61.4% hit rate
- shots: 150 picks, 64.7% | saves: 122 picks, 59.0%
- clearances: 18 picks, 33.3% (too few for any calibration to fire)
