---
name: BDL search quirks
description: BallDontLie API search limitations and workarounds for all sport clients (NBA, WNBA, NFL, NHL, NCAAB)
---

## BDL /players?search= behavior (varies by sport)
- **NBA / WNBA / NFL / NCAAB**: search param works but only for single tokens; multi-word queries return 0 results. Use last-name fallback + token-score sort. Cache prefix `search3:`.
- **NHL**: search param is silently ignored — returns all players sorted by ID regardless of query. Must use `_get_all_current_players(season)` (full roster via `seasons[]=` filter, cursor-paginated, cached 7 days in Atlas `nhl_cache`). Then fuzzy-match locally with token-overlap scoring. Cache prefix `nhl_search:`.

**Why:** BDL API prefix-matches a single field; spaces produce no results, not a partial match.

**How to apply (NBA/WNBA/NFL/NCAAB):**
1. Try the full query first.
2. If 0 results AND query contains a space, retry with `query.rsplit(" ", 1)[-1]` (last name only).
3. After the fallback, score results by token overlap and sort descending — without scoring `search=James` returns James Ennis III before LeBron James.
4. Never cache empty results (transient 429 must not poison the cache).

## NHL-specific quirks

### Endpoints (ALL-ACCESS tier, base URL `https://api.balldontlie.io/nhl/v1`)
| Purpose | Endpoint |
|---------|----------|
| Player list (paginated) | `/players?seasons[]=2025&per_page=100` |
| Single player | `/players?player_ids[]={id}&per_page=1` (NOT `/players/{id}` — 404) |
| Season stats | `/players/{id}/season_stats?season=2025` → returns `[{name, value}]` array |
| Team games | `/games?team_ids[]={team_id}&seasons[]={year}&per_page=100` |
| Box scores | `/box_scores?game_ids[]={id}&per_page=100` (max 100, NOT 500) |
| Injuries | `/player_injuries` |
| Odds | `/odds?dates[]=YYYY-MM-DD` |

### Season format
Integer year: `2025` = 2024-25 season. NOT the string `"20252026"`.

### Season stats format
`/players/{id}/season_stats` returns `[{"name": "goals", "value": 44}, ...]` — must convert to flat dict: `{item["name"]: item["value"] for item in data}`.

### Game log strategy
BDL NHL has no `/stats` or `/player_game_stats` endpoint (all 404). Game logs require:
1. `get_player()` to find team_id from `player.teams[]` (sorted by season desc)
2. `/games?team_ids[]={team_id}&seasons[]={year}` → filter `game_state == "OFF"` for completed games
3. Batch `/box_scores?game_ids[]=...` (10 per call, per_page=100), filter rows by player id
4. `_transform_nhl_log()` uses `shots_on_goal` field (not `shots`)

### Game states
`game_state`: `PRE` | `LIVE` | `CRIT` (critical — close game, late) | `OFF` (official/final)

## NFL — no player stats endpoint
BDL NFL has NO `/player_stats` or `/box_scores` endpoint at any subscription tier. Live tracking for NFL picks is score-context-only (matchScore/period/homeTeam/awayTeam); player stat settlement must be deferred to a background loop or manual correction.

## Live game tracking (_process_bdl_live in routes/picks.py)
Wired into list_picks and /api/picks/live-update alongside soccer.
- NBA: `/v1/games?dates[]=today` (status="Final"/ISO=scheduled/else=live) + `/v1/stats?game_ids[]=`
- WNBA: `/wnba/v1/games` (status=pre/in/final) + `/wnba/v1/player_stats?game_ids[]=`
- NHL: `/nhl/v1/games` (game_state PRE/LIVE/CRIT/OFF) + `/nhl/v1/box_scores?game_ids[]=`
- NFL: `/nfl/v1/games` (status=scheduled/in_progress/Final) — score only
