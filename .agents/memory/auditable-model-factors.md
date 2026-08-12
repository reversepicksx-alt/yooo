---
name: Auditable model factors
description: Durable rule for preserving and presenting prediction inputs and evidence quality.
---

The Analysis experience must explain the exact final prediction, not reconstruct an approximation later. Capture factor evidence after all projection, calibration, confidence, and matchup overrides have completed; persist it with the saved pick; and expose sample counts, impact, and unavailable inputs explicitly. Keep an ordered projection ledger separate from evidence/context cards so every numeric transformation can be audited without implying that all displayed evidence was multiplied into the projection.

**Why:** Prediction caches rotate and older picks can outlive the source prediction document. Reconstructing from current data can silently mix fixtures or imply that unavailable evidence influenced the original number. A factor card can be useful context without being a numeric model input, so the UI must distinguish those cases.

**How to apply:** Add new model inputs to the final factor snapshot and model-input snapshot, carry both through the save and analysis endpoints, and render every factor with an honest status (`applied`, `measured`, `warning`, or `unavailable`). Numeric projection changes belong in the ordered ledger with before/after values, multiplier, sample size, inputs, and reason. Legacy picks should show unavailable cards rather than hiding the section.