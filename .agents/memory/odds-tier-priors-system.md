---
name: Odds-Tier Self-Learning Priors
description: "Living" odds-tier empirical prior system that auto-learns from settled picks. Mirrors scenario_priors.py architecture.
---

## System

`backend/odds_tier_priors.py` implements a self-learning layer that mines settled picks for (oddsTier x position x propType x recommendation) buckets.

- **Refresh**: every 6h via background loop in `server.py` (mirrors scenario_priors/league_priors cadence)
- **Min sample**: n=8 (stricter than league_priors n=4)
- **Cap**: +/-6% multiplicative nudge (full cap, no scenario-uncertainty discount)
- **Shrinkage**: James-Stein n/(n+30)

## Odds tier resolution (prediction time)

1. Moneyline (`americanOdds` from `match_odds`) first
2. Fallback to projected possession (`match_dominance.expectedPoss`)
3. Classifier thresholds: heavy_favorite >=75%, strong >=66.7%, moderate >=56.5%, slight >=52.4%, close >=47.6%, slight_underdog >=40%, moderate >=28.6%, heavy_underdog <28.6%

## Integration points

- `server.py`: background refresh loop (startup + 6h)
- `routes/predict.py`: resolves odds tier, calls `lookup_single()` for BOTH over/under sides, passes `odds_tier_priors_result` + `odds_tier_priors_mode` into `compute_bayesian_projection()`
- `bayesian_engine.py`: applies multiplicative nudge in same pattern as scenario_priors (shadow/live/off mode)
- `routes/picks.py`: persists `oddsTier` from `bayesianMetrics.oddsTierPriors.oddsTier` on save
- `routes/admin.py`: `/odds-tier-priors` and `/odds-tier-priors/refresh` endpoints for inspection

## Env var control

`ODDS_TIER_PRIORS_MODE`: `off` | `shadow` (default) | `live`
- Default is `shadow`: computes and logs what it WOULD do, but does NOT change the projection
- Switch to `live` when ready to apply the nudge to real predictions

## Backtest findings (2,275 settled picks with oddsTier)

| Finding | Data | System response |
|---------|------|-----------------|
| Heavy favorite pass_attempts under-project by -6.66 | n=87, mean error | System applies +6% boost for heavy_favorite/CDM/pass_attempts/OVER |
| Heavy underdog CB pass_attempts OVER = 0% hit | 0/5 | System has no bucket (n<8), stays neutral |
| pass_attempts UNDER overall = 63.7% hit | 654/1026 | System boosts pass_attempts UNDER across tiers where data exists |
| Away CDM/RB tackles OVER = 85.7% hit | 12/14 | System boosts away CDM tackles OVER |

## Why this matters for accuracy

The Bayesian engine's hyperprior + momentum + possession model is structurally conservative for favorites (it doesn't fully account for how dominant teams inflate certain stat types). The odds-tier layer is an empirical correction — it observes "when the model said X for heavy favorites, actual was X+6.66" and adjusts future projections in that direction. It does NOT trust the scouting report blindly; it trusts the DATA.

## Status: LIVE (flipped 2026-07-03)

`ODDS_TIER_PRIORS_MODE=live` is set. Confirmed via a real prediction that `bayesianMetrics.oddsTierPriors.applied=true` and the multiplier changes `posterior_mean`.

Two bugs surfaced only once live traffic actually hit the possession fallback path (shadow mode never exposed them because a crash there just no-ops silently into "not found"):
1. Possession-fallback tier resolver assumed `match_dominance["expectedPoss"]` was a `{"home":.., "away":..}` dict; it is actually a single float already remapped to the player's own team's perspective. Fix: pass `expectedPoss`/`oppExpectedPoss` straight through per player venue, don't call `.get()` on them.
2. `bayesian_engine.py` referenced `_MAX_NUDGE` for the odds-tier nudge cap without importing it (it's module-local to `odds_tier_priors.py`/`league_priors.py`/`scenario_priors.py`, not shared). Any new nudge-cap-style layer that copies this pattern must import its own `_MAX_NUDGE`, never assume it's in scope.

Lesson: shadow-mode "successfully" logging `found: false` for every real request is not proof the resolution logic works — it can mean every lookup is silently exception-swallowed. Verify shadow mode by checking for the resolution print/success log line (e.g. `[ODDS TIER] ... tier`), not just the absence of a crash.

## Venue stratification (added 2026-07-03)

Buckets were originally venue-agnostic — a "heavy favorite" bucket mixed home and away picks, even though home advantage stacks on top of favorite/underdog status (e.g. home dominant team's CDM recycles possession differently than an away dominant team's).

Fix: `_refresh()` now builds TWO parallel bucket sets — coarse (original 4-key: tier×position×prop×side) and fine (5-key: + venue). `lookup_single(..., venue=...)` tries the fine bucket first and falls back to coarse when the venue-split sample is too thin (n<8). This is strictly additive — venue-splitting never loses coverage vs the original system, only adds precision when data supports it.

Real-data check before implementing (worth repeating for any future stratification idea): simulate the bucket-count tradeoff first. Splitting existing settled-pick data by venue took passing (n>=8) buckets from 63→73 despite tripling the total combinatorial space — net positive, so implemented. If a proposed extra dimension had *shrunk* the passing-bucket count, it would not be worth adding.

Response now includes `oddsTierPriors.venueSplit: true/false` so you can tell whether a given prediction actually pulled the fine-grained venue bucket or fell back to coarse.
