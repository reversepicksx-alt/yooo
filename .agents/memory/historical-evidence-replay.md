---
name: Historical evidence replay limits
description: Historical missed-pick replay must separate prediction reruns from evidence coverage and account for provider date coverage and rate limits.
---

Historical missed-pick replays are two different measurements: rerunning `/api/predict` can test recommendation/projection changes, while StatsBomb scans test explanation evidence. StatsBomb Open Data may legitimately cover zero rows when the ledger dates fall outside its published competition seasons, so coverage counts need provider-status breakdowns and must not treat unavailable rows as unavailable forever.

**Why:** A combined “improvement” number would incorrectly attribute calibration changes to event evidence and would make restricted or rate-limited provider coverage look like a model failure.

**How to apply:** Report rerun flips/hits separately from exact-fixture evidence coverage; preserve unavailable reasons, use bounded concurrency, and keep external evidence packets projection-neutral until leakage-safe validation supports otherwise.