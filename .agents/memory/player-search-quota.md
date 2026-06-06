---
name: Player search quota-exhausted + tournament league fix
description: Two bugs that make player search return empty; fixes for quota-exhausted state and WC/UCL league ID cache mismatch.
---

## Bug 1: Quota-exhausted state returns no results
When `/tmp/.api_sports_quota_exhausted` contains today's date, `is_quota_exhausted()` returns True and all `api_football_request()` calls return `[]` immediately (circuit breaker). The player search `_search_players_cache` had a gate that blocked single-word queries unless the cache contained a top-5 European league player — which blocked e.g. "Messi" (leagueId=667 MLS). Fix: pass `relaxed=True` to `_search_players_cache` when quota is exhausted, skipping the top-5 gate. Also bail out before API strategies entirely.

## Bug 2: Tournament league IDs (WC=1, UCL=9, etc.) filter out all cache results
Players are cached under their CLUB league IDs (e.g. Messi → leagueId=667 MLS), not under the tournament they played in (WC = leagueId=1). When a user selects "World Cup" and searches, the cache query had `leagueId=1` which returned no results.

Fix: `_TOURNAMENT_LEAGUES = {1, 9, 10, 11, 13, 15, 16, 17, 18}` — for these league IDs, set `effective_league_id=None` so the cache search ignores the league constraint. Also: if a non-tournament league constraint still returns empty, retry without the league filter.

## Bug 3: Sort didn't distinguish exact word match from substring match
`sort_key` used substring matching (`"messi" in "messias"` is True), so "Messias" and "L. Messi" both scored `all_match=0`. Added `exact_word` tier: 0 if every query part appears as a complete word in the name, 1 otherwise. This surfaces "L. Messi" above "Messias"/"Messina" correctly.

## Sort/quality filter hoisting
The sort helpers (`_strip`, `query_parts`, `sort_key`, `_apply_sort_and_quality`) are now defined BEFORE the cache early-return in `search_players`, so cache hits get sorted the same way as API results. Previously the early cache return bypassed sorting entirely.

**Why:** Quota resets at UTC midnight; WC/Copa/UCL are permanent tournament IDs, not ephemeral. The pattern will recur every time the daily quota is exhausted or users search for WC/UCL players.

**How to apply:** In `backend/routes/players.py`: `_TOURNAMENT_LEAGUES` set at module level; `relaxed` param in `_search_players_cache`; `quota_gone = is_quota_exhausted()` at top of handler; sort helpers defined before the cache early-return.
