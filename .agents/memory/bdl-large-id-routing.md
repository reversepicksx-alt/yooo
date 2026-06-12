---
name: BDL large-ID routing bug
description: BDL assigns some players IDs > 100k (its own internal IDs), which the code wrongly routes to the MLB Stats API, causing empty data for those players.
---

# BDL large player IDs (> 100k) misrouted to MLB Stats API

## The rule
BDL's own player database can assign IDs ≥ 100k (e.g. Andrew Painter = 4668116, debut_year=2025). The codebase treats `player_id >= _STATSAPI_ID_THRESHOLD (100k)` as "this is an MLB Stats API ID" and calls `/people/{id}` on statsapi.mlb.com. But BDL internal IDs ≠ MLB Stats API IDs — 4668116 returns "Object not found" on statsapi; the real statsapi ID is 691725.

## Why it was failing
- BDL search returned full_name="Andrew Painter" for player 4668116 → `bdl_has_full_match=True`
- statsapi fallback in `search_players` only ran when `not bdl_has_full_match`
- Mobile app sent player_id=4668116 to predict route
- All stat calls for 4668116 via MLB Stats API returned empty
- Code threw 404 "No stats found"

## Fixes applied (mlb_client.py + mlb_routes.py)

### search_players — always statsapi-search when BDL has large IDs
`bdl_has_large_id = any(p.get("id", 0) >= _STATSAPI_ID_THRESHOLD for p in players)`
Runs `_statsapi_search_players` when any BDL result has a large ID and prepends the correct statsapi result (e.g. 691725) ahead of the BDL entry.

### mlb_predict — ID remap when data is empty for large IDs
When game_logs + season_stats + prev_season_stats are all empty AND player_id >= 100k:
- calls `_statsapi_search_players(playerName)` to find the real statsapi ID
- retries `_fetch_mlb_data` with the correct ID
- logs `"[MLB PREDICT] Large-BDL ID remap: 4668116→691725 for Andrew Painter"`
Handles existing devices that already have the wrong ID cached.

**Why:** BDL periodically adds MLB players using their own sequential ID scheme which can exceed 100k. The routing assumption `ID >= 100k → statsapi` is not always valid.

## How to apply
If another player gets "No stats found" despite being active, check if their BDL-returned ID >= 100k. The remap will auto-fix it at predict time, and the search fix will surface the correct ID for new searches.
