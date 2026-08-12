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