---
name: Stale pick auto-void
description: Global 7-day stale-void runs at end of _run_auto_settlement to clear orphaned pending/live picks.
---

## Rule
At the END of `_run_auto_settlement` (grok_engine.py), a global stale-void block
queries for all non-MLB pending/live picks with `timestamp < (now - 7d)` and
settles them as PUSH with `settledBy: "stale_void"` and a `voidReason` string.

## Why
Soccer: API-Football fixture data expires; past match stats unreachable after ~2 weeks.
WTA: 14-day per-pick limit exists in the WTA loop but picks missing opponentId are
skipped — the global stale-void catches those.
CS2: 7-day per-pick limit exists but may miss edge cases.
Without this, picks accumulate as perpetually pending, inflating the pending count
and distorting the pick history UI.

## How to apply
- MLB is excluded (`sport: {$nin: ["mlb"]}`): the live-loop's stale-final escape handles those.
- The cutoff is 7 days for all sports. WTA picks with a known opponentId are already
  handled at 14 days in the WTA loop — the global void is a backstop at 7d.
- Timestamps on picks are ISO strings (e.g. "2026-05-20T21:32:40.241308+00:00").
  MongoDB ISO string comparison is lexicographically correct for UTC timestamps.
