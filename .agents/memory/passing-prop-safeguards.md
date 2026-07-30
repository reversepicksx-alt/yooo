---
name: Passing-prop safeguards
description: Durable rules for recent passing-prop suppression and possession-data quality gates.
---

Recent passing-prop protection must be conservative: deduplicate settled events by player, prop, line, direction, and calendar date; use a rolling window; require a meaningful sample; and return PASS rather than automatically recommending the opposite side. Enforce the PASS decision in both the client and save API.

**Why:** A blanket pass-direction block overfit screenshots and mixed all-time history with current league/role performance. A narrow recent bucket guard protects against clustered misses without suppressing healthy buckets.

**How to apply:** Keep rolling suppression limited to soccer passing props and expose the sample, rate, window, and machine-readable reason so the UI and audit tools can explain skipped recommendations.

Possession-dependent Bayesian layers must use a separate boolean indicating genuine team possession observations. Rank-gap and odds-only estimates may produce expected possession values, but they must not activate season-average possession adjustments or be treated as real season averages.

**Why:** Synthetic fallback values commonly use 50% season baselines. Treating those numbers as real silently activates contextual layers with invalid denominators and can create systematic role-specific passing errors.

**How to apply:** Initialize the flag false, set it only after both teams have real possession observations, and preserve false through every fallback path.