---
name: Owner scorecard and PASS calibration
description: Rules for account-scoped soccer scorecards, event deduplication, and non-actionable PASS records.
---

The owner model-health scorecard must authenticate the owner session, scope records to that account and soccer, and keep the complete settled ledger visible, including pushes, DNPs, and calibration-only PASS records. PASS observations are stored but excluded from normal hit/miss probability metrics and ROI; their original lean is evaluated separately as `passOutcome`.

**Why:** A global hit/miss query and a legacy local-picks modal made the visible 501-pick report disagree with the owner's 473 settled soccer records. Rejecting PASS also discarded useful evidence about whether avoided sides would have hit.

**How to apply:** Use trackingId as the primary prediction-event identity. For legacy rows, use fixture + player/market fields when fixtureId exists; never merge separate fixtures solely because player, prop, and line match. Keep duplicate removal, result counts, calibration gaps, projection error, and replay/holdout status visible in both the dedicated owner dashboard and the legacy Pick Insights path.