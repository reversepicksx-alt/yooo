---
name: Settlement push notifications must cover every settlement code path
description: Pick-settlement pushes were missing because only the live-polling path sent them, not the background auto-settle bot
---

This app has multiple independent code paths that can write a "settled" result to a pick: the
live-polling paths (triggered when a user has the app open, in `backend/routes/picks.py`) and a
separate background auto-settlement bot (`backend/ai_engine.py#_run_auto_settlement`, runs on a
timer regardless of whether any client is open) across every sport (MLB, NBA/NFL/NHL/WNBA, soccer,
CS2, WTA) plus a global stale-void loop.

**Why:** The live-polling path had push notification calls; the timer-driven background bot did
not. Since most settlements happen while users aren't actively in the app, this meant push
notifications were silently missing for the majority of real-world settlements — a bug that only
shows up in production usage patterns, not in foreground testing.

**How to apply:** Whenever a settlement/void/status-transition write is added or changed, grep for
*every* place a pick's `status`/`result` field gets set to a terminal value (not just the one path
you're editing) and confirm each one either calls the push helper or is a deliberate
correction-only pass (e.g. a "final stat refresh" that only fixes `actualValue` on an already-settled,
already-notified pick — those should NOT re-fire push, or users get duplicate/noisy notifications).
A settlement endpoint that computes a result but never persists it to the DB (dead/legacy code) is
not a real gap and doesn't need push wiring.
