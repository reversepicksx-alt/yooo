---
name: Deterministic evidence-quality controls
description: How prediction evidence quality should affect confidence without distorting projection math.
---

Prediction evidence quality is a separate control stream from projection math. Count real player history, verified fixture identity, tactical context, possession, lineup/role, opponent samples, and market context independently; missing optional feeds remain neutral, not negative evidence.

**Why:** API-Football coverage varies by league and match. Penalizing unavailable optional fields made ordinary well-supported predictions appear weak, while ignoring sparse history allowed false precision.

**How to apply:** Cap confidence only downward when evidence is genuinely thin. Convert to PASS only when the final edge is also thin; never boost projections, invent missing evidence, or replace a strong structural signal merely because an optional feed is unavailable. Persist the quality score and reasons with the final ledger.