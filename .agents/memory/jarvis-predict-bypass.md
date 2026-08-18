---
name: JARVIS predict pipeline bypass
description: Internal service bypass for POST /api/jarvis/predict — how to call predict() without a real subscriber session, and the correct response field names.
---

# JARVIS predict pipeline bypass

## The bypass pattern
`predict()` in `backend/routes/predict.py` normally requires a MongoDB-backed subscriber session (`email` + `token` verified via `verify_session`). The JARVIS bypass skips this by checking:

```python
req.email == "_jarvis_service_"  and  req.token == os.environ.get("JARVIS_API_KEY", "")
```

If both match, `sess` is synthesised as `{"valid": True, "access_type": "subscriber", "email": "_jarvis_service_"}` and session lookup is skipped entirely. All subsequent pipeline logic (history, Bayesian, calibration, AI) runs unchanged.

**Why:** Storing a stable session token isn't feasible (tokens rotate). Using JARVIS_API_KEY as the bypass credential ties bypass security to the same secret already governing all JARVIS endpoints.

**How to apply:** Only add this pattern inside `predict()` (and any future pipeline entry points that also call `verify_session`). The bypass grants subscriber-level access only — never owner-level.

## Real predict() response field names (verified 2026-08-18)

These differed from what I guessed; always use these:

| What you want | Correct field |
|---|---|
| Edge z-score (float) | `edgeZ` |
| Edge label (str) | `edgeRating` |
| Direction probability (int 0–100) | `bayesianComponent` |
| Factor ledger | `factorLedger` (top-level, NOT inside `bayesianMetrics`) |
| Calibration metadata | `fusionApplied` (NOT `calibrationApplied`) |
| Landing bands | `bayesianMetrics.landingBands` |
| Prior sample count | `bayesianMetrics.priorSamples` |
| Evidence quality level | `evidenceQuality.level` |
| Game situation | `gameSituation` |
| Warnings | `tacticalAlerts` |
| Safety rating | `safetyRating` |
| Line deviation info | `lineDeviationBand`, `lineDeviationHitRate` |

## Club-transfer guard
When `req.teamId` doesn't match the player's current API-Football club, `predict()` raises **HTTP 409** (not 422) with detail `"Current club changed to X. Please reselect the player before predicting."` — the guard is at ~line 1088, compares `req.teamId` vs `verified_club["teamId"]` from `_resolve_verified_club()`. The guard updates the player cache before raising, so a single retry uses the fresh cache. The JARVIS `/predict/soccer` endpoint catches 409+"Current club changed" and retries once automatically.

## Player resolution — international vs club stats
`/players?id=&season=` can return national-team stats before club stats (e.g. Magalhães returns Brazil id=6 before Arsenal id=42). Resolution strategy in `_resolve_soccer_context`:
1. **Fixture-team match first**: prefer the stats entry whose `team.id` appears in the fixture's home or away team IDs.
2. **Non-international fallback**: skip leagues in `_INTL_LEAGUE_IDS = {1,2,10,17,18,20,29,30,31,34}`.
3. **Any entry**: last resort.
Always use `fixture_id` → `_resolve_fixture()` for home/away IDs before querying player stats.
