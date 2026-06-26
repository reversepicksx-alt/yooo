---
name: WC live tracking — BDL routing removed
description: World Cup picks must not route through BDL; API-Football is the correct source for WC player stats
---

## Rule
World Cup (leagueId=1) must NOT appear in `LEAGUE_TO_BDL` in `backend/soccer_bdl_client.py`.

**Why:** BDL's `/fifa/worldcup/v1` endpoint returns Tier-2 stats (`passes_total`, `tackles`, etc.) as `None`, which the stat-mapper converts to `0`. The settlement logic then fires with `actualValue=0`, marking legitimate HITs as MISS. Tchouaméni had 69 passes (HIT over 66.5) but settled MISS=0 because BDL returned None.

**How to apply:** If anyone re-adds WC to the BDL map for any reason, they must also verify that Tier-2 stats are actually populated (run a live test mid-match). API-Football's `fixtures/players` endpoint provides fully-populated WC player stats and is the correct source for all WC settlement and live tracking.

## Zero-value guard (added 2026-06-26)
Three settlement points now have a guard: if `actualValue == 0` AND `propType` is a count stat AND `minutes_played >= 30`, settlement is deferred (returns None / matchStatus=final). The three locations:
1. `_build_bdl_soccer_update` — BDL live settlement path (line ~2125)
2. `_build_soccer_update` — API-Football live settlement path (line ~2381)  
3. `_settle_soccer_pick` — background auto-settlement path (line ~2693)

Count props covered: `pass_attempts, passes, crosses, tackles, key_passes, shots, shots_on_target, interceptions, blocks, dribbles, dribbles_success, fouls_drawn, fouls_committed, clearances, duels_won`

## Admin fix endpoints
Two admin endpoints added to `backend/server.py` for correcting already-wrong picks:
- `POST /api/admin/bulk-resettle-zero-picks` — finds all settled picks with `actualValue=0` and a count propType, re-settles each via API-Football. Accepts `{ secret, dryRun: true/false }`.
- `POST /api/admin/force-resettle-pick` — re-settles a single pick by `pickId` regardless of current status. Accepts `{ secret, pickId }`.
