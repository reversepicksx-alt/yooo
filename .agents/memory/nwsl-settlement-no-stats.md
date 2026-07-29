---
name: NWSL settlement — no player stats from API
description: API-Football fixtures/players returns 0 teams for NWSL; can't auto-settle. Season mismatch also affects fixture lookup.
---

API-Football does not provide player-level statistics for NWSL (leagueId=254). `GET fixtures/players?fixture=<fid>` returns `response: []` for all NWSL fixtures, making automated settlement impossible.

Additionally, NWSL uses season=2026 (calendar year), while CURRENT_SEASON=2025 in config.py. The settlement loop does try `next_s = CURRENT_SEASON + 1` (which resolves to 2026) so fixture discovery works — but because fixtures/players is empty, the pick can't settle.

The explicit NWSL season constants are now used by player search, manual search, and team season-stat cache sync. The API `/players?league=254&season=2026` returns valid NWSL player IDs and names; the dashboard IDs page itself is Cloudflare-protected and is not an API data source.

**How to apply:**
- NWSL picks will never auto-settle via the API path. After the pick reaches the stale-void threshold (4 days), they void automatically as push.
- If manual backfill is needed, void them immediately as push with reason "NWSL player stats not available from API — voided as push".
- Do NOT attempt to unsettle and re-run the bot for NWSL picks; the API will never return player data.
- If NWSL support is added in the future, test with `fixtures/players?fixture=<nwsl_fid>` first before building any settlement path.

**Why:** API-Football's coverage for women's leagues (especially NWSL) is limited to match scores only, not player-level stats. This is a data provider limitation, not a code bug.
