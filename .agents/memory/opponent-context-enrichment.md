---
name: Opponent context enrichment
description: Player Profile and pick cards should surface opponent possession and per-90 advanced stats via cached backend endpoints, not inline in the pick list.
---

## Rule

For any pick or player profile, enrich the visible context with:

1. **Opponent season-average possession** (`oppAvgPoss`) — computed from the opponent’s last 15 finished fixtures in the same league/season, cached ~6 h, and persisted on the pick during live updates and settlement.
2. **Per-90 advanced stats** for the player — xG, xA, shots, shots on target, key passes, passes, tackles, minutes per game — fetched from API-Football `/players` and cached.

## Why

Users asked for “far more context per pick” and “new factors that improve predictions.” Research identifies xG, xA, shot volume, key passes, and opponent possession as the most predictive soccer prop signals. Exposing them only in the AI pipeline is not enough; they must be visible in the Player Profile and per-pick rows so users can see *why* the model leans a direction.

## How to apply

- Backend: compute `oppAvgPoss` in the same place where live possession is fetched (`_build_soccer_update`, `_settle_soccer_pick`) and persist it to the pick. Provide public endpoints `/api/teams/{id}/season-possession` and `/api/players/{id}/advanced-stats`.
- Frontend: show `oppAvgPoss` in pick rows and a dedicated “Per-90 Advanced Stats” grid in the Player Profile card. Fetch stats by `playerId` (not name) when a searched or owned player is selected.
- Cache: both endpoints use a 6 h MongoDB cache; avoid recomputing on every profile open or live poll.
