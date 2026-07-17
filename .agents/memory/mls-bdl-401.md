---
name: MLS BDL 401 — use API-Football for live tracking
description: BDL /mls/v1 returns 401; MLS live tracking must use API-Football, not BDL
---

## Rule
MLS (leagueId=253) must NOT be in `LEAGUE_TO_BDL` in `soccer_bdl_client.py`.
`is_bdl_league(253)` must return False so live tracking routes to `_process_api_football_live`.

**Why:** The BDL API key (MLB_BDL_API_KEY) does not have access to the /mls/v1 endpoint — returns 401 Unauthorized. With 253 in LEAGUE_TO_BDL, `get_live_and_recent_matches(253)` always returns [] and every MLS pick stays PENDING indefinitely.

**How to apply:** If MLS picks ever stop going LIVE, verify `is_bdl_league(253)` returns False. API-Football's `fixtures?live=all&league=253` works correctly and returns live MLS matches in real time.
