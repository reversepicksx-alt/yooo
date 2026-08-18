---
name: Prediction latency boundaries
description: Keep deterministic prediction inputs synchronous while bounding optional enrichment and cache misses.
---

The prediction path must keep verified fixture identity, current player logs, lineup status, Bayesian math, calibration, and the final factor ledger in the synchronous path. Historical possession enrichment, grounded position lookup, shadow providers, comparison-player season baselines, and other explanation-only evidence must be cache-first and independently time-bounded.

**Why:** A complete cached player sample was previously discarded because it lacked enough historical possession rows, causing a 40-fixture/provider fallback and mobile timeouts. Optional providers and position grounding could also delay a mathematically complete result.

**How to apply:** Treat missing historical enrichment as neutral/unavailable rather than as a reason to replace real player logs with synthetic averages. Never let partial live comparison calls change pair calibration based on which requests beat a timeout; use cached baselines or omit that optional adjustment.

Matchup-volume evidence follows the same boundary: home/away team SOT and pass fetches are explanation-only, run in their own bounded wave, and must never replace or erase verified player logs when provider calls time out.

**Why:** Expanding venue evidence from one selected venue to both sides multiplies provider work; putting those calls in the required wave caused a timeout to blank otherwise valid projection inputs.

**How to apply:** Keep matchup packets `shadow_only` with unavailable metrics when needed. Preserve the deterministic response and attach whichever exact-fixture venue rows completed within the independent budget.

The response clock must begin before session/current-club checks, not when the
main prediction `try` block starts. Route-level recovery, first-goal, knowledge
base, calibration, and fire-and-forget scheduling work must also be bounded or
detached; a late optional await can invalidate an otherwise correct 37-second
budget.

**Why:** A hanging provider or Atlas read in a late enrichment path could still
push the user-visible request past 40 seconds even though the main waves had
timeouts.

**How to apply:** Reuse the route's bounded-source helper for every
provider/cache await on the response path, and keep a regression test with
deliberately hanging provider/cache stubs.

Fixed per-source timeouts are not sufficient after a request has already spent
most of its budget. Wave 2, late lineup/position evidence, calibration,
persistence, and owner media must cap against the request's remaining time;
otherwise individually "bounded" awaits can still exceed the user-facing
deadline in aggregate.

**Why:** A late-entry Wave 2 source with an 8-second local cap could still
push a request beyond the 37-second server budget even though the source had a
nominal timeout.

**How to apply:** Use the shared remaining-budget helper for every response
path await after the initial request setup. Test both a hanging source at
request start and one invoked near the deadline.