---
name: Stale pick auto-void
description: Multi-layer stale-void system preventing picks from hanging as pending forever.
---

## Layers (innermost to outermost)

### 1. Inline orphan-discard (soccer loop, WTA loop)
If a pick has NO opponentId AND NO opponentName it can never match a fixture.
After 48h it is immediately deleted as a DNP discard; it is not stored as a
settled result and does not generate a settlement notification.
- Soccer: checked after each `_try_settle_soccer` call returns False
- WTA: checked at the top of the WTA per-pick loop (before the 90-min guard)
Both parse the timestamp inline (not relying on `pick_ts` from outer scope).

### 2. Per-sport stale-discard rules in each sport loop
- CS2: 7-day discard when `get_cs2_completed_match_result` returns None
- WTA: 14-day discard when `get_wta_completed_match_result` returns None

### 3. Global backstop (end of _run_auto_settlement)
Catches anything the per-sport loops missed. Cutoff: **4 days** (tightened from 7d).
Excludes MLB. Uses `timestamp < cutoff_4d` ISO string comparison. Deletes the
pick instead of creating a stored DNP/void row.

## Why
Without this: picks accumulate as perpetually pending, distorting pick history UI
and misleading the audit endpoint. DNP rows are now discarded rather than
retained as settled cards. Root cause of the original stale pick build-up:
- Soccer picks without opponentName/opponentId had no fixture match path
- WTA picks with opponentId=0 were silently skipped with `continue` (now voided)
- CS2 old settlement code had a bug that wrote push instead of hit/miss (settledBy=None)
- Global void was 7d, now 4d (soccer matches resolve in hours, not days)

## How to apply
- Non-DNP settlement writes must include `settledBy` so audits can distinguish
  old-code pushes (settledBy=None) from current code (auto_soccer/auto_cs2).
- Any new sport loop: add per-pick orphan void at the top when opponent info missing.
- `picks-audit` endpoint excludes picks with `voidReason` from wrong-push count.
- MLB is excluded from global void (`sport: {$nin: ["mlb"]}`): live-loop handles those.
- Timestamps on picks are ISO strings. MongoDB ISO string comparison is correct for UTC.
