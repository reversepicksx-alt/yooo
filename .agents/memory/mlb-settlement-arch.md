---
name: MLB settlement architecture
description: BDL vs Stats-API ID handling, field normalisation, and composite prop settlement.
---

## Rule
All player IDs go through `mlb_client.get_player_game_logs()` for settlement.
BDL IDs (< 100_000) are normalised to Stats-API field shape by `_transform_bdl_log()`.
Stats API IDs (≥ 100k) return native field names from statsapi.mlb.com.
Both paths produce the same schema — `_try_settle_mlb` and the live loop use a
single code path.

## Why
BDL raw fields are `"strikeouts"`, `"hits"`, `"walks"` etc. while settlement
code looked for `"p_k"`, `"p_hits"`, `"p_bb"`. This mismatch caused
`current_value = None` → pick never settled → stale-final escape pushed it
after 48h. 301 real hit/miss picks were wrongly recorded as PUSH and had to
be repaired with a data script.

## How to apply
- `_transform_bdl_log(raw)` is called in BOTH `get_player_game_logs` (BDL path)
  and `get_game_player_stats` (BDL path). Do not apply to Stats-API returns.
- Cache keys are `mlb_gl2:` and `mlb_gps2:` (bumped to invalidate old BDL format).
- Composite props (`hitter_fantasy_points`, `hits_runs_rbis`, `pitcher_fantasy_score`,
  `pitching_outs`) use placeholder field names like `__fantasy_pts__`.
  In `_try_settle_mlb` these are handled by `_COMPOSITE_HANDLERS` dict mapping
  each placeholder to its compute function (`_compute_fantasy_pts`, etc. from mlb_engine.py).
- `_try_settle_mlb` docstring updated to reflect both ID spaces.
- Live loop: `get_bdl_team_id_for_statsapi()` resolves BDL 1-30 team ID before
  calling `get_today_and_live_games()`.
