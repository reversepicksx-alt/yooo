---
name: Lineup grid positions
description: The authoritative fixture-level position source and its conservative inference boundary.
---

API-Football's confirmed fixture lineup includes a broad `pos` category plus a `grid` coordinate and team `formation`. The grid is the preferred fixture-level evidence for exact defensive positions: in a four-defender shape, row 2 columns map to LB/CB/CB/RB; five-defender shapes map to LWB/CB/CB/CB/RWB. Generic D/DEF remains generic when the grid or formation is missing or ambiguous.

**Why:** Broad provider categories and stale player-profile roles were repeatedly producing misleading Fullback/CB labels. The lineup grid is already available from the existing API source and avoids adding another provider or manual maintenance.

**How to apply:** Use grid-derived positions only when the fixture lineup is confirmed. Predicted lineups can provide tactical context but must not overwrite the player's exact current position. Keep the grid as evidence provenance so the UI can distinguish fixture-observed positions from profile fallbacks.