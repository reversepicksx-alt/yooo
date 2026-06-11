---
name: API Sports WC client
description: API Sports integration for FIFA World Cup predictions — separate account from suspended API-Football, covering WC group stage + qualifiers + previous WC history.
---

## Account & Key
- `API_SPORTS_KEY` secret — separate from the suspended API-Football account
- Base URL: `https://v3.football.api-sports.io`
- Auth header: `x-apisports-key: KEY` (not Bearer)
- Mega plan, active until 2026-07-11

## League IDs
- `WC_LEAGUE = 1` — FIFA World Cup (season 2026 = current, season 2022 = previous)
- `CONCACAF_QUAL_LEAGUE = 31` — CONCACAF WC 2026 Qualification

## Key endpoint: /fixtures/players
`GET /fixtures/players?fixture={id}&team={id}` returns per-fixture player stats:
- `statistics[0].games.minutes` — minutes played
- `statistics[0].passes.total/key/accuracy` — pass stats (accuracy is string, e.g. "55")
- `statistics[0].shots.total/on` — shots
- `statistics[0].goals.total/assists` — can be null (means 0)
- `statistics[0].tackles.total/interceptions/blocks` — defensive stats
- `statistics[0].duels.total/won` — duels
- `statistics[0].dribbles.attempts/success` — dribbles
- `statistics[0].fouls.drawn/committed` — fouls
- `statistics[0].cards.yellow/red` — cards
- `statistics[0].games.rating` — match rating string e.g. "7.3"

## Player search caveat
`GET /players?search=name&league=1&season=2026` often returns 0 results, especially for
WC 2026 before many games are played. Better approach:
1. Find team_id via `GET /teams?league=1&season=2026` (cached 24h)
2. Find finished fixtures via `GET /fixtures?team=T&league=1&season=S&status=FT`
3. For each fixture, fetch player stats and find by name match

## Mexico-specific: no CONCACAF qualifiers
Mexico, USA, and Canada are 2026 co-hosts → automatically qualified → 0 CONCACAF qualifier
fixtures. For other CONCACAF nations (Costa Rica, Honduras, Panama, etc.), league 31 has data.

## WC 2022 historical data
`GET /fixtures?team=16&league=1&season=2022&status=FT` returns 3-4 games for Mexico.
Confirmed data for César Montes:
- Poland (home, 11/22): 69 passes, 1 tackle, 6.9 rating
- Argentina (away, 11/26): 57 passes, 1 tackle, 6.6 rating
- Saudi Arabia (away, 11/30): 68 passes, 4 tackles, 7.3 rating

## Integration in predict.py
Injected after BDL WC block (line ~1386) for `_is_wc=True` predictions.
`_api_sports_wc.get_game_logs(req.playerName, req.teamName)` fetches all three sources.
API Sports wins date collisions (richer stats); BDL-only dates kept as supplement.

## Critical: Motor async client
`config.db` is a Motor (async MongoDB) client — `CACHE_COL.find_one(key)` returns a **coroutine**, not a dict.
Must use `await CACHE_COL.find_one(...)` directly. Do NOT wrap in `asyncio.to_thread` (thread has no event loop)
and do NOT call synchronously (returns unawaited coroutine = `_asyncio.Future`).
The bug manifests as `'_asyncio.Future' object has no attribute 'get'` in `_cache_fresh`.

## File location
`backend/api_sports_wc_client.py` — standalone async client with MongoDB caching.
Cache collection: `db["api_sports_wc_cache"]`. Cache TTLs: team list 24h, fixtures 30min,
per-fixture player stats 24h (historical data doesn't change).

## How to apply
Only activate for `_is_wc` (league_id=1) predictions. Not used for club league predictions.
Rate limit: ~0.4s between requests (Mega plan allows ~150 req/min conservatively).
