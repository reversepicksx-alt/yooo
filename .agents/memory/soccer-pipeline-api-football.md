---
name: Soccer pipeline — API-Football active
description: API-Football is the sole soccer data source; BDL soccer block is permanently disabled.
---

## Rule
`_is_bdl_league = False` in `backend/routes/predict.py`. The BDL soccer block is gated by `if _is_bdl_league and _bdl_soc.is_bdl_league(league_id)` so it never fires for any soccer prediction.

**Why:** User acquired an API-Football Mega plan (key=`API_SPORTS_KEY`, 150k/day) for World Cup 2026. BDL soccer data was a workaround during an expired API-Football subscription.

**How to apply:** Never set `_is_bdl_league = True` again unless explicitly asked to revert. The BDL soccer client code stays in place but is dormant.

## National team ID resolution
`find_team()` in `team_resolver.py` now has a **Strategy -1**: exact match against `cache_national` before any clubs search. This prevents "Mexico" → "New Mexico United" (4003) and "South Africa" → "Club Africain" (988).

In `predict.py` opponent ID resolution: if `req.opponentId` is already a national team (in `cache_national`), it is kept as-is and `find_team` is not called for the opponent.

## Team search (search.py)
`/api/search/teams` now always checks `cache_national` (485 entries) and prepends exact national team matches to the top of results. Covers Mexico (16), South Africa (1531), Brazil (6), etc.

## WC mode removed
`_is_wc` is hardcoded `False`. The old WC supplement block and `import api_sports_wc_client` were removed. League 1 (World Cup) goes through the standard pipeline with national team IDs resolved via `cache_national`.
