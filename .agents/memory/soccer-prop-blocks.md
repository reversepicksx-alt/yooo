---
name: Soccer prop hard blocks
description: Post-Bayesian-Truth hard overrides for soccer props with empirically terrible OVER hit rates.
---

## Rule
Hard blocks run in `routes/predict.py` AFTER the `[BAYESIAN TRUTH]` override block
(i.e. after line ~5235). They cannot be overridden by anything upstream.

### clearances OVER — hard-blocked
- 0% hit rate on 13 settled picks (0W/13L)
- Any `propType == "clearances"` with `recommendation == "over"` is force-flipped to UNDER 60% Medium.
- projectedValue is mirrored below the line if it was above.
- A tacticalAlert is added explaining the data override.

### shots OVER — soft-blocked via prop safety
- 19–22% hit rate on 38–41 settled picks → prop safety label `AVOID`
- The prop safety check (earlier in predict.py) warns and applies a 45% confidence cap.
- No hard post-BT flip yet for shots OVER — monitor; add hard block if hit rate stays below 30%.

## Why
Bayesian Truth overrides all upstream confidence guards because it sets
recommendation from P(OVER)/P(UNDER). If the prior is miscalibrated for a prop type
(clearances — defenders clear, forwards almost never), it will still output OVER
with high confidence. The hard block is the final safety net after all upstream logic.

## How to apply
- Add new hard blocks of this form for any prop+direction with n≥10 and hitRate<25%.
- Always place AFTER the `[BAYESIAN TRUTH]` print statement block.
- Add a tacticalAlert explaining the override so users see the data rationale.
