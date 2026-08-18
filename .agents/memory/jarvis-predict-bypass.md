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
When `req.teamId` doesn't match the player's current API-Football club, `predict()` raises HTTP 422 with detail `"Current club changed to X. Please reselect the player before predicting."` — this is the club-transfer guard at ~line 1050. The JARVIS endpoint propagates this as-is; Task #280 will add auto-retry.
