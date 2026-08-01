---
name: Auditable model factors
description: Durable rule for preserving and presenting prediction inputs and evidence quality.
---

The Analysis experience must explain the exact final prediction, not reconstruct an approximation later. Capture factor evidence after all projection, calibration, confidence, and matchup overrides have completed; persist it with the saved pick; and expose sample counts, impact, and unavailable inputs explicitly.

**Why:** Prediction caches rotate and older picks can outlive the source prediction document. Reconstructing from current data can silently mix fixtures or imply that unavailable evidence influenced the original number.

**How to apply:** Add new model inputs to the final factor snapshot and model-input snapshot, carry both through the save and analysis endpoints, and render every factor with an honest status (`applied`, `measured`, `warning`, or `unavailable`). Legacy picks should show unavailable cards rather than hiding the section.