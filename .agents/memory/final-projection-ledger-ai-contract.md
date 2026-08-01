---
name: Final projection ledger AI contract
description: Gemini explanations must be generated from the exact post-calibration projection snapshot, never from preflight math.
---

The displayed projection, recommendation, probabilities, confidence controls, edge, and safety state must be captured in one ordered final ledger before Gemini synthesis or AI-cache lookup. Cache identity must include a fingerprint of that ledger.

**Why:** H2H, opponent-profile, calibration, Bayesian Truth, and late safety controls can materially change both the projection and direction after the initial AI prompt. Explaining an earlier estimate produces narratives that contradict the badge and displayed numbers.

**How to apply:** Preserve evidence-quality summaries separately, record every numeric projection mutation in sequence order, bind AI cache entries to the final-ledger fingerprint, and keep math-only output functional when Gemini is unavailable.