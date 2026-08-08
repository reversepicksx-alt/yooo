---
name: Picks list Atlas write guards
description: All Atlas writes in the picks/list consistency-correction section must be guarded — unguarded writes 500 the entire request when Atlas is at storage quota.
---

## Rule

Every `await db.picks.update_one()` (and any other write) inside the picks/list route handler **must** be wrapped in `try/except` with a print log and a continue/pass. There are no exceptions — the list endpoint must always return 200 even when Atlas writes are blocked.

**Why:** Atlas at 512 MB storage quota blocks all writes with code 8000 (`AtlasError`). An unhandled write exception propagates straight to FastAPI → 500 response. The native app's React Query error path preserves stale PENDING cache and never shows live data. The symptom looks like "live tracking broken on native app" but the root cause is a crash in the consistency-correction pass.

**How to apply:** When adding any new write inside the list_picks() route (settlement corrections, sport repair, DNP guard, projection backfill), always wrap with:
```python
try:
    await db.picks.update_one(...)
except Exception as _e:
    print(f"[PICKS-LIST WRITE FAIL] <context> {player_name}: {_e}")
```

The 5 locations that were fixed (all in `backend/routes/picks.py`):
1. trackingId / sport / recommendation repair block
2. DNP correction (result → dnp) 
3. False DNP repair (positive stat overrides stale minutes)
4. Legacy-source reconciliation (numeric-final → settled)
5. Consistency correction (result mismatch → correct direction)

Also fixed: `backend/routes/notifications.py` mark-read — same pattern.

## Relationship to native app live tracking

The native app hits the production VM (`https://reversepicks.com`, hardcoded in `mobile/eas.json`). When picks/list 500s, React Query preserves stale PENDING cache. The fix must be **deployed** to take effect on the native app — local backend restart is not sufficient.
