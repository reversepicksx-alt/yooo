---
name: JARVIS audit calibration reads
description: Performance and provenance constraints for owner calibration over the settled picks ledger.
---

Calibration reads over Atlas picks must use an inclusion-only projection, a bounded settledAt/timestamp window, and an indexed status/time query. Expose the inspected row count and truncation state rather than implying that a partial window is lifetime truth.

**Why:** An unbounded or mixed inclusion/exclusion Mongo projection caused a 500 first, then a 20-second request on the production-sized ledger. A bounded query keeps owner tooling responsive and makes small-sample or partial-window uncertainty visible.

**How to apply:** Keep `status=settled` and time ordering indexed, cap the default request window, and preserve explicit no-fake-precision warnings when verified probabilities or settlement provenance are absent.