---
name: DNP settlement false-positive — minutes=None
description: minutes=None from API-Football gets coerced to 0, tripping the DNP guard even when the player clearly played (has a non-zero stat).
---

**The bug:** In `_build_soccer_update` (picks.py) and `_try_settle_soccer` (ai_engine.py), the DNP guard is `if minutes < 30: void as push`. API-Football frequently returns `minutes=None` for players who played the full game in certain leagues (NWSL, Copa Lib, occasionally Bundesliga). The `or 0` coercion turns None into 0, which satisfies `0 < 30` and fires the DNP guard — even when the player had 37 pass attempts.

**The fix:**
- In both settlement paths, check `if current_value > 0` (or `_has_stat_evidence_bg`) BEFORE the DNP block.
- A non-zero stat is conclusive proof the player participated — the minutes field is irrelevant.
- The DNP block now only fires when BOTH minutes < 30 AND no stat evidence exists.

**How to apply:**
- Whenever modifying settlement logic, preserve the `stat > 0 → skip DNP guard` check. It must come BEFORE any `minutes < 30` comparison.
- If adding new prop types to settlement, they inherit this guard automatically since it's on the general stat value, not prop-type specific.
- To backfill wrongly-voided picks: use `POST /api/admin/regrade-dnp-picks?dry_run=false` — re-grades picks where voidReason contains "min (min" and actualValue > 0.

**Why:** The bug caused paying customers' correct UNDER hits to be settled as push/DNP, generating complaints. The root fix is stat-evidence beats minutes-field.
