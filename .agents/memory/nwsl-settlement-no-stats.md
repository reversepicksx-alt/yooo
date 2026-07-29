---
name: NWSL settlement endpoint
description: API-Football NWSL player-stat endpoint and the required league/season/fixture lookup contract.
---

API-Football provides NWSL player-level statistics through `GET fixtures/players?fixture=<fixtureId>` when given a valid 2026 NWSL fixture ID. A prior zero-row result came from an invalid or mismatched fixture lookup, not a provider limitation.

NWSL uses season=2026 (calendar year), while CURRENT_SEASON=2025 is used by other soccer leagues. Fixture discovery and recovery must carry league=254 and season=2026, then stats must be fetched by exact fixture ID.

The explicit NWSL season constants are used by player search, manual search, team season-stat cache sync, automatic settlement, live tracking, and orphan/date-recovery fixture lookup. The API `/players?league=254&season=2026` returns valid NWSL player IDs and names.

**How to apply:**
- Resolve NWSL fixtures with league 254 and season 2026.
- Fetch match stats only with `fixtures/players?fixture=<fixtureId>`.
- Match players by player ID first, then robust name fallback; preserve DNP/defer guards for genuinely missing or delayed rows.

**Why:** A confirmed 2026 NWSL fixture returned two teams and 38 player rows, including minutes and passes. The earlier conclusion that NWSL had no player stats was caused by testing the wrong fixture/lookup path.
