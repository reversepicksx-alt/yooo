---
name: Live projection display
description: Live pick cards must preserve the saved model projection while showing current value and pace as separate in-game context.
---

The saved projection is a permanent pregame model value. Live `NOW` and `PACE` values are additional context and must never replace or relabel `PROJ` in compact or shared pick cards.

**Why:** Reusing the third stat slot for pace made the Live tab appear to lose the projection on iOS, even though the backend still returned it.

**How to apply:** Keep `LINE` and `PROJ` visible in every pick-card state; append `NOW`/`PACE` only when live data exists, and keep the native and generated share-card layouts consistent.