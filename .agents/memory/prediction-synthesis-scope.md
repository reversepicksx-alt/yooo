---
name: Prediction synthesis scope
description: Evidence packets are assembled before the prediction dict exists; final-direction metadata must be computed only after synthesis.
---

Evidence collection runs before deterministic synthesis creates `prediction`. Any verdict or metadata that reads `prediction` must be deferred to the final response stage after late recommendation guards.

**Why:** A same-role cohort verdict was evaluated during packet assembly and caused every `/api/predict` request to fail with an `UnboundLocalError`.

**How to apply:** Keep pre-synthesis cohort assembly independent of `prediction`; add source-order regression tests whenever metadata is moved between pipeline stages.