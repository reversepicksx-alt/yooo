---
name: MLB settlement architecture
description: Stats API vs BDL ID mismatch causes broken settlement; Stats API game logs are the correct source.
---

## Rule
`_try_settle_mlb` (and the live loop) must use `mlb_client.get_player_game_logs()` (Stats API) — NOT `get_game_player_stats()` (BDL) — because picks store Stats API player IDs (≥100,000) and BDL uses a separate 1–30 team ID space.

## Why
- `search_players` returns Stats API IDs (≥100k, e.g. Gausman=592332)
- These are stored as `playerId` and `teamId` on picks
- BDL `get_game_player_stats(player_id, game_id)` also uses BDL-format game IDs which are never reliably written to picks
- The old code required `expected_game_id` (a BDL ID) on the pick; without it the settlement returned `False` forever → 0%/0.9% settled hit rates

## How to apply
- `_try_settle_mlb`: call `get_player_game_logs(player_id, current_year)` — returns transformed logs with `p_k`, `ip`, etc. Match by date proximity (pick creation date ± 2 days).
- Live loop BDL game detection: call `get_bdl_team_id_for_statsapi(statsapi_team_id)` to resolve the BDL 1-30 ID before calling `get_today_and_live_games()`.
- Stats API game logs from `_statsapi_game_logs` use `game_id = gamePk` (Stats API IDs), not BDL game IDs.
- Verified: `get_player_game_logs(592332, 2026)` returns `p_k=8, ip=6.2` — correct field names for settlement.
