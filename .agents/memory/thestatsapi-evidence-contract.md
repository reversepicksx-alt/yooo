---
name: TheStatsAPI evidence contract
description: Constraints for using TheStatsAPI as optional, identity-verified soccer analysis evidence.
---

TheStatsAPI is an analysis-only enrichment source. API-Football remains authoritative for fixture identity, prediction math, live status, settlement, and final player stats. Every provider payload must preserve explicit coverage states; an empty or missing response is unavailable evidence, never a measured zero.

**Why:** The provider has endpoint-specific response shapes, finite spatial samples, rate limits, and occasional coverage gaps. Treating missing data as measured evidence or mixing a near-match fixture would create confident but false analysis.

**How to apply:** Join by team names, opponent names, date, competition context, and verified lineup player identity before displaying data. Label touch samples as observed locations rather than continuous tracking. Cache/coalesce requests, keep 429 failures short-lived, and show legacy picks as unavailable rather than reconstructing historical enrichment.