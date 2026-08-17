---
name: Press Intensity
description: Active API-Football-based soccer pressure signal and its conservative passing-projection contract.
---

Press Intensity is the active replacement for Understat/PPDA enrichment. It combines same-fixture opponent pass volume with weighted defensive actions from API-Football. Possession is context only, not pressure evidence.

Missing or incomplete defensive-action inputs must produce an explicit unavailable state; do not infer a measured pressure score from fallback possession or odds. Available packets must carry sample size, coverage, source, and the applied multiplier.

**Why:** API-Football does not provide defensive-third coordinates, recoveries, or timestamped pressure locations, so the metric is a transparent synthetic proxy rather than literal PPDA.

**How to apply:** Emit the packet on every soccer Bayesian path. Apply only to `pass_attempts`/`passes`, use the verified selection position/role, keep the direction role-aware, and cap the multiplier to a modest bounded range. Keep legacy Understat code dormant and out of prediction-time/background flows.

Stable pressure evidence requires at least seven valid defensive-action rows. Fewer rows remain usable only as an explicitly limited sample; opponent-pass volume cannot inflate the sample count.

**Why:** A smaller action sample can still provide useful context, but labeling it stable overstates reliability and makes provider coverage look better than it is.

**How to apply:** Sort fixture and history rows newest-first before applying any lookback limit. Keep the actual valid action count in the response and show limited/stable status in the UI. For wide players, broad provider-category comparison rows may be shown as context only when exact rows are unavailable; they must not influence projection or calibration.