---
name: CS2 kills over-projection calibration
description: Settled pick data showed systematic over-projection of kills; hyperpriors reduced.
---

## Rule
CS2 kills projections were over-estimating by ~4–5 kills per 2-map match. Always check prop safety hit rates before trusting the hyperpriors.

## Why
Settled data (n=158 maps_1_2_kills OVER picks) showed 10.1% hit rate — far below the ~50% expected if projections were accurate. `EXPECTED_ROUNDS_2MAPS=40` was too high; T2/T3 matches frequently see one-sided maps (16–20 rounds per map in blowouts), averaging closer to 36 total.

## Changes made
| Constant | Before | After |
|---|---|---|
| `HYPER_PRIOR["maps_1_2_kills"]` | 27.0 | 22.0 |
| `HYPER_PRIOR["map1_kills"]` | 16.0 | 14.0 |
| `HYPER_PRIOR["kills"]` | 16.0 | 14.0 |
| `HYPER_PRIOR["maps_1_3_kills"]` | 43.0 | 36.0 |
| `HYPER_PRIOR["maps_1_2_headshots"]` | 11.0 | 9.0 |
| `HYPER_PRIOR["headshots"]` | 6.5 | 5.5 |
| `HYPER_PRIOR["map3_kills"]` | 16.0 | 14.0 |
| `HYPER_PRIOR["map3_headshots"]` | 6.5 | 5.5 |
| `HYPER_PRIOR["maps_1_3_headshots"]` | 17.5 | 14.5 |
| `KPR_HYPER` | 0.63 | 0.58 |
| `EXPECTED_ROUNDS_PER_MAP` | 22.0 | 20.0 |
| `EXPECTED_ROUNDS_2MAPS` | 40.0 | 36.0 |

## How to apply
If CS2 OVER hit rates climb above 45% on kills, the priors can be nudged back up in 1–2 unit increments. If they stay below 30%, reduce further. Check `[PROP SAFETY]` in backend startup logs.
