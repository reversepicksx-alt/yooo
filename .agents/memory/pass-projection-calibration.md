---
name: Pass projection calibration
description: Walk-forward residual calibration rules for soccer passing projections.
---

The pass-projection residual layer must be evaluated walk-forward before it is
enabled live. Training rows must be settled before the prediction snapshot,
deduplicated by player/event/prop/line/direction, and exclude DNP, push, void,
unresolved, and manually corrected records. Corrections are hierarchical
(league+role+direction → league+direction → role+direction → global+direction),
shrunk, recent-weighted, and capped at ±5%.

**Why:** Soccer pass projections already have several contextual adjustments.
Applying another empirical nudge without out-of-sample evidence can compound
the same possession or role signal and make clustered misses worse.

**How to apply:** Keep `PASS_PROJECTION_CALIBRATION_MODE` at `shadow` by
default. Accept live mode only after the walk-forward report shows no leakage
and improves out-of-sample MAE or signed bias without materially degrading
directional hit rate.