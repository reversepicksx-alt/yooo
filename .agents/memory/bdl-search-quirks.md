---
name: BDL search quirks
description: BallDontLie API search limitations and workarounds for all sport clients (NBA, WNBA, NFL, NHL, NCAAB)
---

## Rule
BDL `/players?search=` behavior varies by sport:
- **NBA / WNBA / NFL / NCAAB**: search param works but only for single tokens; multi-word queries return 0 results. Use last-name fallback + token-score sort.
- **NHL**: search param is silently ignored — returns all players sorted by ID regardless of query. Must use `_get_all_current_players(season)` (full roster via `seasons[]=` filter, paginated, cached 7 days) and fuzzy-match locally.

**Why:** BDL API does a prefix match on a single field; spaces cause no results, not a partial match.

**How to apply:**
1. Try the full query first.
2. If 0 results AND query contains a space, retry with `query.rsplit(" ", 1)[-1]` (last name only).
3. After the fallback, **score** results by token overlap against the original query and sort descending. Without scoring, `search=James` returns James Ennis III before LeBron James.
4. Never cache empty results — transient 429 errors must not poison the cache.
5. Cache key prefix is currently `search3:` — bump the digit any time you change search behavior or need to bust stale caches.

## NFL/NHL specific
BDL NFL and NHL players have no `full_name` field — must synthesise: `f"{first_name} {last_name}"`.

## NHL stats unavailable
BDL NHL has **no stat endpoints** at the current subscription tier. All `/stats`, `/season_averages`, `/player_stats` calls return 404. `get_player_game_logs()` returns `[]` immediately (no API call). NHL predict route returns HTTP 503 with a descriptive message.

NHL `get_player()` must use `/players?player_ids[]={id}` (list endpoint), NOT `/players/{id}` (returns 404).
