---
name: Soccer player-history TP contract
description: Soccer player-history and exact-position comparison rows require provider-fetched possession and exact minutes.
---

Soccer player-history rows must include exact player minutes plus provider-fetched team and opponent possession; expose the team value as TP. Missing or partial possession is unavailable evidence, not an estimate.

**Why:** Rendering a stat row without verified TP made the compact history look complete while silently removing the tactical context needed to interpret possession-sensitive props.

**How to apply:** Keep cache-first reads, rehydrate missing fixture possession from the exact fixture-statistics endpoint, and reject/retry incomplete soccer predictions rather than using synthetic or derived possession. Apply the same contract to exact-position comparison rows and preserve last-10 home/away TP aggregates separately from player-stat splits.