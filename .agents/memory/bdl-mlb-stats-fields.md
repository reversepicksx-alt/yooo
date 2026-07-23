---
name: BDL MLB /stats field names
description: Actual field names BDL returns in the /mlb/v1/stats endpoint — different from what _transform_bdl_log originally expected.
---

# BDL MLB /stats endpoint — actual field names

## The rule
`_transform_bdl_log` must read pitcher stats from the actual BDL field names:
`p_k`, `ip`, `p_hits`, `er`, `p_bb`, `pitch_count` — NOT the old expected names
`strikeouts`, `innings_pitched`, `earned_runs`, `pitches`.

Batter fields are: `hits`, `hr`, `rbi`, `bb`, `k`, `runs`, `total_bases`, `stolen_bases`, `doubles`, `plate_appearances`.

**Why:** The function was written expecting a different API shape. BDL returns stats directly on the top-level object (not nested under a `game{}` sub-dict). The only field that matched by coincidence was `batters_faced`, which is why that was the sole non-None value.

**How to apply:**
- `game = raw.get("game") or {}` → **wrong** (returns empty dict)
- `date_str = game.get("date")` → **wrong** (always empty; BDL /stats has no date field)
- `raw.get("strikeouts")` → **wrong** (field doesn't exist)
- ✓ `raw.get("p_k")`, `raw.get("ip")`, `raw.get("er")`, `raw.get("p_bb")`, `raw.get("pitch_count")`
- Date must be resolved separately: BDL /stats has `game_id` (top-level int). Use `_enrich_game_logs` with a `games_by_id` dict to map game_id → date/opponent from team schedule.
- `ip` from BDL is an **integer** (e.g. `7`), not the StatsAPI string format `"6.2"`. `_ip_to_float` handles both via `str(ip).split(".")`.
- Cache key bumped to `mlb_gl3` when field names were corrected (2026-07-23).
