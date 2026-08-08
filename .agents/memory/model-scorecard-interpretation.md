---
name: Model scorecard interpretation
description: How to interpret the settled-pick evaluation scorecard and its validation limits
---

The model scorecard separates binary probability quality (log loss, Brier, calibration) from numerical projection error (MAE/RMSE). Mixed-unit overall MAE/RMSE is directional only; comparable conclusions require sport/prop groups. A chronological split of already-generated picks is descriptive, not a true historical replay.

**Why:** The first broad settled-pick report showed high-confidence buckets materially underperforming their stated probabilities, while projection RMSE was much larger than MAE because of outliers. Reporting only hit rate would hide both issues.

**How to apply:** Treat calibration gaps and per-prop MAE/RMSE as model-improvement signals. Do not claim out-of-sample proof until a replay rebuilds the prediction/calibration state using only information available before each historical prediction.

The current settled soccer ledger shows a persistent direction split: OVER picks are near coin-flip or worse while UNDER picks are materially stronger, including within higher-confidence buckets.

**Why:** The production audit found roughly 52.7% on unique OVER events versus 66.4% on unique UNDER events across the deduplicated scored corpus; the account sample showed the same pattern (54.0% OVER vs 65.9% UNDER).

**How to apply:** Treat direction as a first-class calibration dimension. Do not increase overall confidence or call the model healthy from aggregate hit rate while OVER confidence remains miscalibrated; validate any directional policy with a leakage-safe walk-forward replay.