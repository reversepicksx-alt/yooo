---
name: Position resolution cache staleness
description: Why prompt improvements to player position resolution don't take effect immediately
---

Player position/role is resolved by a Gemini call in `backend/routes/predict.py`, but the result is cached for 30 days in MongoDB collection `db.player_positions`, keyed by `playerId`.

**Why this matters:** Any change to the position-resolution prompt (e.g. CDM-vs-CAM rules) does NOT affect players whose position is already cached and < 30 days old. The cache hit short-circuits the new prompt entirely. This caused a frustrating "the fix isn't working" loop: Vitinha (PSG, playerId 128384) kept resolving as CAM/Advanced Playmaker because a stale cache entry existed, even after the prompt was corrected.

**How to apply:** After improving the position prompt, you MUST invalidate affected cache entries or the change is invisible until expiry. Options:
- Targeted: `db.player_positions.delete_one({playerId, team})` for the specific player, then re-run /api/predict to force fresh resolution.
- Systemic (if many players affected): add a prompt-version field to cached docs and invalidate entries with an older version, OR bulk-delete (note: triggers a Gemini call per player on next predict — cost).

**Infra note:** The backend connects to **Atlas** (cloud MongoDB), not the local mongod the workflow starts. `backend/config.py#_resolve_mongo_url` uses Atlas SRV when DNS resolves, else falls back to localhost. Always query via the backend's own `config.db` (DB name `reversepicks`) when verifying — querying `mongodb://localhost:27017` directly will show an empty/different dataset.
