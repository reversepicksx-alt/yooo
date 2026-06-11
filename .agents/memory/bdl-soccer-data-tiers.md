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

## ID scheme: two separate systems

**Regular BDL soccer** (`/epl/v2` etc.):
- `player_match_stats.match_id` is a BDL-internal round scheduling ID that CANNOT be joined to `matches.id`.
- Use sequential matching: stat_row[i] ↔ team_match[i] (both newest-first).

**FIFA WC BDL** (`/fifa/worldcup/v1`):
- `player_match_stats.match_id`, `match_lineups.match_id`, and `match_events.match_id` ALL use the **same internal ID** and CAN be directly joined.
- `matches.id` (1, 28, 53 for Mexico) is still a separate public schedule ID; `player_match_stats.match_id` (1002, 766, 1019) is internal and does NOT match `matches.id`.
- Confirmed: fetching `match_lineups` with `team_ids[]=1` returns rows with match_ids (766, 1019) that directly correspond to César Montes' `player_match_stats.match_id` values.

## WC API available endpoints (as of 2026-06-11)

Available: `/teams`, `/players`, `/matches`, `/player_match_stats`, `/match_lineups`, `/match_events`
Not available (404): `/standings`, `/match_stats`, `/player_stats`, `/season_stats`

Key `player_match_stats` fields in WC (different from regular BDL):
- `is_home` (bool) — authoritative home/away for that player in that match
- `passes_total`, `tackles`, `interceptions`, `blocked_shots`, `clearances` — Tier-2, often populated
- `yellow_cards` — NOT present in WC player_match_stats; must use `match_events` for cards
- `dribbles_attempted` (not `dribbles_attempts`), `was_fouled` (not `fouls_suffered`)

## is_home: authoritative from stat row, NOT schedule

`player_match_stats.is_home` can DISAGREE with the schedule's `home_team` designation in WC format.
Example: Mexico vs South Korea (6/19) — schedule shows Mexico as `home_team`, but `player_match_stats.is_home=False`.

**Rule**: venue and opponent determination must be INDEPENDENT:
- **Venue**: use `_is_home_raw` from `_norm()` output (preserved from `raw.get("is_home")`)
- **Opponent**: always use `bdl_team_id == home_id` from the schedule (sequential match)

## Formation + card enrichment (step 4c in get_game_logs)

**Formation/starter status** — fetched via `match_lineups?team_ids[]=bdl_team_id&per_page=100`
- Find player by accent-normalised name in lineup rows
- Produces: `{match_id → {formation, is_starter, lineup_player_id}}`
- Joined to game logs by `_bdl_match_id` (shared ID between player_match_stats and match_lineups)
- Added as `formation` and `is_starter` fields on each game log dict

**Yellow cards** — `match_events?player_ids[]=lineup_player_id&incident_types[]=card`
- `player_ids[]` on match_events returns ALL events from matches where that lineup_player_id appears
  (not just events BY that player). Must filter: `event.player.id == lineup_player_id AND incident_class == "yellow"`
- `lineup_player_id` (from match_lineups) ≠ `bdl_pid` (from player_match_stats search)
  The lineup uses a different player ID namespace. Must resolve via name-match in lineup rows first.

## Match enrichment strategy (sequential)

1. `player.team_ids[0]` from player search → reliable team ID (for regular BDL leagues)
2. WC: team ID resolved from `country_code` match against `/teams` endpoint
3. `_is_home_raw` from `player_match_stats.is_home` → always authoritative for venue
4. Sequential team schedule mapping for opponent names + dates (still reliable even when `is_home` disagrees)
5. Formation/starter from `match_lineups` joined by shared internal `match_id`

## WC / international quirks

- WC path: `/fifa/worldcup/v1`. Player objects use `name` field (not `display_name` like EPL).
- `team_ids` is `None` for WC players — team ID resolved from country_code lookup instead.
- `_find_player` checks `p.get("display_name") or p.get("name")` to handle both formats.
- `per_page` max is **100** for `match_lineups` (400 error if set higher).
- Empty caches (no results yet) use 30-min TTL; non-empty use 6h. Critical for mid-tournament starts.
- All WC group-stage games are neutral venue — Bayesian engine skips venue split in WC mode.

## predict.py BDL stage

BDL runs for ALL BDL-supported leagues regardless of whether fixture cache already has data.
Quality gate (≥3 games with target field populated) decides whether to override fixture-cache logs.
Falls through to API-Football PLAYER-DIRECT if quality gate fails (but PLAYER-DIRECT is blocked for BDL leagues).

## Game log string format (with formation)

Format: `"YYYY-MM-DD vs Team (venue, Nmin[, formation]): value"`
Example: `"2026-06-25 vs Czechia (away, 90min, 5-3-2): 57"`

Regex in predict.py (lines ~4004): `r"(\d{4}-(\d{2})-(\d{2})) vs (.+?) \((.+?), (\d+)min(?:, ([^)]+))?\): (.+)"`
Group 7 = formation (optional), Group 8 = value. Both old (no formation) and new formats parse correctly.
