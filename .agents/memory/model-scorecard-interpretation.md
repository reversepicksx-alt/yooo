---
name: Model scorecard interpretation
description: How to interpret the settled-pick evaluation scorecard and its validation limits
---

The model scorecard separates binary probability quality (log loss, Brier, calibration) from numerical projection error (MAE/RMSE). Mixed-unit overall MAE/RMSE is directional only; comparable conclusions require sport/prop groups. A chronological split of already-generated picks is descriptive, not a true historical replay.

**Why:** The first broad settled-pick report showed high-confidence buckets materially underperforming their stated probabilities, while projection RMSE was much larger than MAE because of outliers. Reporting only hit rate would hide both issues.

**How to apply:** Treat calibration gaps and per-prop MAE/RMSE as model-improvement signals. Do not claim out-of-sample proof until a replay rebuilds the prediction/calibration state using only information available before each historical prediction.