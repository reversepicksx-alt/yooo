---
name: Possession extreme mismatch fix
description: Root cause chain for WC/tournament possession showing wrong team dominating (Uzbekistan 72%, Ghana 60%). Three bugs, all required.
---

## The bug: "Uzbekistan will dominate Portugal"

Three separate bugs combined to give the weak team 72% possession:

### Bug 1 — req.odds was silently discarded
`PredictionRequest` had no `odds` field → FastAPI silently dropped whatever the mobile app sent.
`compute_match_dominance` received `match_odds` (API-fetched), which for WC/tournament games
returns empty (no `bookmakerOdds`, no `americanOdds`). Every odds-based correction silently
skipped → default `expectedPoss: 50.0` returned.

**Fix:** Added `odds: Optional[dict] = None` to `PredictionRequest` in `backend/models.py`.
In `predict.py`, built `_eff_odds`: if `match_odds` has no odds keys and `req.odds` does,
merge `req.odds` into `_eff_odds` before calling `compute_match_dominance`.

### Bug 2 — Neutral venue odds direction inverted
For neutral games (`is_neutral=True`), the code always went to the `else` branch:
`home_avg = avg_poss(opp_stats_list)` → formula-home = OPPONENT.

The post-correction reads `_ep_fh_prob = odds["americanOdds"]["home"] prob` as the
formula-home team's win probability. But in a neutral game where the player's team IS the
fixture-home (e.g. Portugal listed as `odds.home = -1111`), the opponent (Uzbekistan) is
fixture-AWAY → need `odds["americanOdds"]["away"]` prob instead.

**Fix:** `_ep_use_away = is_neutral AND playerIsHome`. When `_ep_use_away=True`, use
`_eia` (fixture-away prob) as formula-home prob instead of `_eih`.

Non-neutral games always use `_eih` (fixture-home = formula-home, regardless of player side).

### Bug 3 — Silent NameError in odds-only fallback
The `elif (home_avg is None or away_avg is None):` path's note string referenced `_ho`
and `_ao`, which were only defined in the `if _has_bk:` (bookmakerOdds) branch. The
americanOdds path raised `NameError`, caught silently by `except Exception:` → 50% default.

**Fix:** Removed `_ho`/`_ao` from the note format string.

## End-to-end verified results
| Match | Pre-fix | Post-fix |
|---|---|---|
| Portugal -1111 vs Uzbekistan +2200 (WC neutral) | Uzbekistan 72%! | Portugal 72% ✓ |
| England -455 vs Ghana +1150 (Ghana home) | Ghana 60%! | England 71% ✓ |
| Iraq +2200 vs France -1111 (Iraq home) | Iraq 55%! | Iraq 27% ✓ |
| PSG -200 vs Lyon +170 (normal) | PSG 54% | PSG 57% (no change) ✓ |

## How to apply
Any time possession shows a massive underdog dominating, check:
1. Is `req.odds` populated? Check `PredictionRequest.odds` field exists.
2. Is it a neutral venue? Check `_ep_use_away` logic with `playerIsHome`.
3. Does the odds-only fallback have any string formatting vars from the wrong branch?
