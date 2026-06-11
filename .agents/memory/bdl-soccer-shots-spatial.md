---
name: BDL Soccer Spatial Shot Data
description: BDL EPL v2 /match_shots endpoint — what works, what doesn't, key quirks for integration.
---

## What works
- `GET /epl/v2/match_shots?player_ids[]=ID&per_page=100` — fetches all shots for one player
- Returns: `{id, match_id, player_id, team_id, is_home, shot_type, xg, xgot, player_x, player_y, goal_mouth_x/y, block_x/y, time_minute}`
- `per_page` max = **100** (400 error if higher). Use cursor pagination for prolific scorers.
- `match_id` in shot rows = same namespace as `/epl/v2/matches` IDs (can join directly)

## What does NOT work
- `/epl/v2/average_positions` → 404
- `/epl/v2/heatmaps` → 404
- Only EPL v2 tested; other leagues may support it but unverified

## shot_type semantics (always SHOOTER's perspective)
- `"goal"` = player scored (xgot=high)
- `"miss"` = player shot wide/high (xgot=0)
- `"save"` = keeper saved player's shot (xgot>0 ← on target)
- `"blocked"` = blocked before keeper
- `player_id` = the shooter, NOT the goalkeeper — there is no GK attribution in this endpoint

## On-target proxy
`xgot > 0` ↔ shot required keeper action ↔ equivalent to "shots on target"

## Integration
- `soccer_bdl_client._fetch_player_shots()` aggregates by match_id → {xg_shot, xgot_shot, shots_spatial, shots_on_target_spatial, avg_shot_x}
- `get_game_logs()` step 4b joins via `_real_match_id` recorded during sequential match mapping
- Data-gap fill: populates `shots_total` / `shots_on` when BDL Tier-2 fields are None
- Bayesian covariate 3f uses xg_shot/xgot_shot series for goals/shots_on_target/shots props

**Why:** Spatial xG is an independent quality signal from accumulated stats (3e uses shot ratios; 3f uses coordinates). Stacking is correct. Also fills frequent BDL Tier-2 data gaps.
