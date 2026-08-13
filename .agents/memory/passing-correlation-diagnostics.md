---
name: Passing correlation diagnostics
description: Same-fixture passing-prop diagnostics must distinguish correlated evidence from independent fixtures without false leakage alarms.
---

The owner passing-prop report treats two or more unique picks sharing an exact fixture as correlated evidence; singleton fixtures are the independent comparison group. Exact settlement-time ties are ordered deterministically by canonical event identity for the diagnostic replay, because equal timestamps do not establish that one event had access to the other's outcome.

**Why:** Settlement batches commonly write multiple picks at the same instant. Counting equal timestamps as future leakage obscures the actual correlation question and makes a healthy replay look invalid.

**How to apply:** Keep this report descriptive and owner-only. Use verified fixture/source metadata when available, preserve explicit unknown buckets when it is absent, and never apply the grouped result as a live calibration adjustment without leakage-safe validation.