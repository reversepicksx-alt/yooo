---
name: BDL soccer data tiers and ID quirks
description: BDL soccer player_match_stats stat availability, ID scheme mismatch, and match enrichment strategy for soccer_bdl_client.py
---

## Stat availability tiers

**Tier-1 (always populated)**: `goals`, `assists`, `shots_total`, `shots_on_target`,
`fouls_committed`, `fouls_suffered` (→`fouls_drawn`), `yellow_cards`, `red_cards`, `offsides`.

**Tier-2 (often None for current/recent seasons)**: `passes_total`, `tackles`, `clearances`,
`key_passes`, `dribbles_attempted`, `interceptions`, `crosses_total`, `minutes_played`,
`rating`, `expected_goals`.

BDL is populating these via secondary data providers; expect Tier-2 to fill in over time.

**Why it matters:** The quality gate in `predict.py` checks `≥3 games with target stat populated`.
This means shots/goals/assists use BDL (saving API-Football quota), while pass_attempts/tackles/
clearances fall back to API-Football until BDL Tier-2 data becomes available.

## minutes_played = None workaround

When `minutes_played=None` but `appearances=1`, the player clearly played.
`_norm()` uses: `minutes = int(minutes_played) if minutes_played is not None else (90 if appearances >= 1 else 0)`.
This prevents BDL logs from being discarded by the `minutes > 0` filter downstream.

## match_id ≠ matches.id (ID scheme mismatch)

`player_match_stats.match_id` (e.g. 14983) is a BDL-internal **round scheduling ID**.
`matches.id` (e.g. 1936) is a **different per-match ID**. They cannot be joined directly.
`GET /epl/v2/matches?id=14983` returns all matches in that round (not match 14983).

## Match enrichment strategy (sequential)

Cannot join `player_match_stats.match_id` → `matches.id`. Instead:
1. Use `player.team_ids[0]` from player search as the reliable team ID (the `team_id` field
   in player_match_stats is unreliable / may refer to opponent or another context).
2. Fetch full team season schedule: `GET /{league}/v1/matches?team_ids[]=N&season=YYYY`
   Returns all team matches sorted by date (newest first with correct `per_page`).
3. Sort both stat rows (already newest-first) and team matches (newest-first) then map by index:
   `stat_row[i] ↔ team_match[i]`.
4. If the player missed games, opponent/venue metadata drifts by ±1 match — acceptable (metadata only,
   never affects the stat values used by the Bayesian engine).

## BDL soccer API params

- Array params require `[]` suffix: `player_ids[]`, `seasons[]`, `team_ids[]`, `match_ids[]`.
- Auth header: `Authorization: {key}` (no "Bearer" prefix).
- League paths: EPL → `/epl/v2`, others → `/{league}/v1`.
- Player search returns `team_ids: [primary_id, former_id, ...]` — use index 0 for current team.
- Results cached in `db.bdl_soccer_cache` (Atlas, not local mongod).

## predict.py BDL stage

BDL runs for ALL BDL-supported leagues regardless of whether fixture cache already has data.
Quality gate (≥3 games with target field populated) decides whether to override fixture-cache logs.
Falls through to API-Football PLAYER-DIRECT if quality gate fails.

**Why:** Original `if not player_game_logs` condition meant BDL was never tried when API-Football
fixture cache had data — defeating the quota-saving purpose entirely. Removed that guard.
