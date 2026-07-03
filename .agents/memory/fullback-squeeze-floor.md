---
name: Fullback possession-squeeze floor bug
description: Why fullback/wing-back pass-attempt projections were being over-suppressed against strong opponents, and the fix precedent to follow for similar position-bucketing logic.
---

## The bug

`backend/bayesian_engine.py`'s POSSESSION SQUEEZE step (ball-control props: pass_attempts, passes, key_passes, crosses, dribbles) applies a position-aware floor multiplier when a team's expected possession drops below their season norm. CBs get a gentle floor (max 20% cut) because they're recognized as high-volume passers who keep recycling the ball under any game state. Fullbacks/wing-backs (LB/RB/WB/LWB/RWB) were bucketed into the generic `_is_def` group instead, which gets a much harsher floor (max 30% cut) — even though the codebase's own "CB managing-lead boost" logic elsewhere already treats LB/RB/WB as CB-equivalent high-volume passers.

Real-world trigger: an Algeria (neutral-venue AFCON qualifier) LB facing a "strong/elite" opponent had a season avg of ~74 pass attempts, got squeezed to a 54.0 projection (implying a UNDER pick), but was on pace for ~85-90 by full time (42 attempts by the 43rd minute) — a fullback recycling possession from the back exactly like a CB would.

**Why:** whenever a new position-bucketing distinction is added to one part of the Bayesian engine (squeeze floors, boosts, penalties, inversions), it must be cross-checked against the *other* position buckets already established elsewhere in the same file — they often disagree on where fullbacks/wing-backs belong, and that disagreement produces systematically wrong projections for exactly the players/positions the newer logic was designed to help.

**How to apply:** Fixed by moving LB/RB/WB/LWB/RWB out of the generic `_is_def` set and into the same `_is_cb` bucket (floor 0.80) used for CB/DC/RCB/LCB. `_is_def` is now reserved for truly generic/unresolved defender labels (`DEF`, `D`). Before shipping any new position-conditional adjustment (squeeze, boost, inversion, penalty) in `bayesian_engine.py`, grep for all existing `_is_*` position-set definitions in the file and make sure the new logic's groupings match established ones for the same position — don't redefine fullback/CB/CDM buckets ad hoc per feature.
