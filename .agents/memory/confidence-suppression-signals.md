---
name: Confidence suppression must aggregate all risk signals, not just one
description: Two independent confidence-suppression systems in predict.py can disagree; a bet can show misleadingly high confidence if only one signal fires a hard cap.
---

## The problem

`backend/routes/predict.py` has (at least) two independent systems that assess
whether a pick is historically risky and should have its `confidenceScore`
suppressed:

1. **Prop-safety cache** (`_get_prop_safety`) — empirical hit rate for a
   `(propType, direction)` pair across ALL settled picks, regardless of how
   far the line deviated from the model's projection. Drives `edgeRating` /
   `safetyRating` (AVOID/RISKY/MODERATE/SAFE) and a hard confidence cap when
   `safetyRating == AVOID` (hit rate ≤44%, n≥5).
2. **Line-deviation band** (`calibration.py#get_line_deviation_intel`) —
   empirical hit rate specifically for "book line vs our projection deviates
   by X% and we went against the book," independent of overall prop hit
   rate. Historically had only a *damped proportional nudge* (~-3 to -8 pts),
   never a hard cap.

**Why this matters:** when the prop-safety cache has no data for a specific
combo (e.g. a rare propType+direction pairing — `_er_hit_rate` comes back
`None`), its hard-cap logic silently skips entirely. If the line-deviation
band *does* have data showing a sub-50% (even ~44%) historical hit rate for
this exact "extreme against-book" scenario, the pick can still surface as
e.g. "72% HIGH confidence" — a worse-than-coin-flip bet displayed as a
strong play. This is exactly the kind of contradiction a user will spot
(e.g. badges reading "MARGINAL edge" + "RISKY hist" right next to a "72%
HIGH" confidence pill).

## The fix / the rule

Any independent empirical-hit-rate signal that can indicate a losing
proposition must apply the SAME hard-cap treatment as the primary
(prop-safety) system — `max(50, round(hitRate))` cap + confidenceLevel
downgrade for hit rate ≤44%, softer -5pt nudge for 45-49% — not just a
damped proportional nudge. Read the signal off values already persisted on
the `prediction` dict (`lineDeviationBand`, `lineDeviationHitRate`) rather
than local variables, since suppression code may run in a different
scope/section of the same function than where the signal was computed.

**How to apply:** whenever adding a new empirical/historical risk signal to
the prediction pipeline, ask "if this fires strongly negative (sub-50% hit
rate) while other signals are silent/absent, does the final displayed
confidence still reflect that?" If not, wire in an equivalent hard cap next
to the existing AVOID/RISKY suppression block, don't just add another
independent nudge.
