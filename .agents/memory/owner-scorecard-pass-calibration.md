---
name: Owner scorecard and PASS calibration
description: Rules for account-scoped soccer scorecards, event deduplication, and non-actionable PASS records.
---

The owner model-health scorecard must authenticate the owner session, then report ReversePicks' complete all-user settled soccer ledger, including pushes, DNPs, and calibration-only PASS records. PASS observations are stored but excluded from normal hit/miss probability metrics and ROI; their original lean is evaluated separately as `passOutcome`.

**Why:** ReversePicks is the calibration owner, not an individual bettor. A personal-email filter made the visible report disagree with system performance, while duplicate saved copies inflated the totals.

**How to apply:** Authenticate with the owner session but query all users. Deduplicate by shared fixture/player/market/direction identity; PASS uses `passLeaning` as its direction. Never merge separate fixtures solely because player, prop, and line match. Keep duplicate removal, result counts, calibration gaps, projection error, and replay/holdout status visible in both the dedicated owner dashboard and the legacy Pick Insights path.