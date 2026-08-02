---
name: Owner scorecard and PASS calibration
description: Rules for account-scoped soccer scorecards, event deduplication, and non-actionable PASS records.
---

The owner model-health scorecard must authenticate the owner session, then report ReversePicks' complete all-user settled soccer ledger, including pushes and DNPs. PASS may be a prediction-time skip label, but it is never a settled history result.

**Why:** ReversePicks is the calibration owner, not an individual bettor. A personal-email filter made the visible report disagree with system performance, while duplicate saved copies inflated the totals.

**How to apply:** Authenticate with the owner session but query all users. Deduplicate by shared fixture/player/market/direction identity. Never merge separate fixtures solely because player, prop, and line match. Keep duplicate removal, result counts, calibration gaps, projection error, and replay/holdout status visible in both the dedicated owner dashboard and the legacy Pick Insights path. Once a verified final is available, normalize any legacy PASS row to its real OVER/UNDER direction and HIT/MISS/PUSH result.

Settled history cards must display only `HIT`, `MISS`, `PUSH`, or `DNP`. A legacy PASS direction may be inferred only from an explicit lean or a strict projection-versus-line comparison; ties remain unresolved rather than being shown as a settled PASS.

**Why:** Users expect every verified final to affect the normal record as a hit, miss, or push. Showing “PASS · UNDER HIT” creates a second settlement vocabulary and makes the record counts disagree with the cards.

**How to apply:** Persist a PASS only while a prediction is unsettled. At exact verified settlement, use the original direction to write normal `recommendation` and `result` fields, then clear `passLeaning`, `passOutcome`, `passReason`, and `isCalibrationOnly`.

The Insights period controls must filter the system ledger on the server before deduplication and calculation. The selected period belongs in both the API payload and the client query key; otherwise the UI can highlight a different button while continuing to display the all-time chart.

**Why:** The period buttons originally changed only local state, so All Time, Last 30 Days, and Last 7 Days rendered the same all-user chart and totals.

**How to apply:** Accept only `all`, `30d`, or `7d`; apply the cutoff to settledAt/timestamp/createdAt before event deduplication, and refetch when the period changes.