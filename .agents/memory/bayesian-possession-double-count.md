---
name: Possession-dominance double-counting bug
description: Same expectedPoss/dom_mult signal was applied twice to ball-control props (pass_attempts, passes, key_passes, crosses, dribbles), understating projections and inflating false confidence.
---

## The bug
`backend/bayesian_engine.py` had two independent mechanisms that both react to the
same "team expected to have less possession than their season norm" signal for
ball-control props (pass_attempts, passes, key_passes, crosses, dribbles):

1. **LAYER 3b covariate** (`match_dominance` → `dom_mult`): additive adjustment
   capped at ±20% of `prior_mean`, blended into `posterior_mean` via
   precision-weighting.
2. **POSSESSION SQUEEZE**: a separate multiplicative squeeze applied directly to
   `posterior_mean` *after* it already included the 3b adjustment, using the same
   `expectedPoss`/`teamSeasonAvg` inputs.

Both fired for the same prop set, so one low-possession signal got punished twice —
producing artificially low projections and overconfident UNDER recommendations for
players (especially fullbacks/wide players) facing a tough pre-match possession
projection, even before accounting for the separate, architectural issue that the
possession estimate itself is pre-match-only and can't see in-game shifts.

**Why this mattered for a real incident:** a fullback's pass-attempts projection was
squeezed by both mechanisms simultaneously, producing a projection far below what a
single, correctly-applied possession-disadvantage signal would justify — compounding
with the separate live in-game shift (opponent parked the bus, actual possession
flipped) to make the miss much worse than either factor alone.

## The fix
Excluded the squeeze-handled prop set (`pass_attempts`, `passes`, `key_passes`,
`crosses`, `dribbles`) from the LAYER 3b covariate's `dom_adj` for outfield players —
they now get ONLY the direct multiplicative squeeze. `shots`/`shots_on_target` (no
squeeze step exists for them) still go through 3b as before. Both prop lists are
derived from a single shared set (`_SQUEEZE_HANDLED_PROPS`) so they cannot drift
apart again silently.

**How to apply:** Whenever adding a new adjustment layer that reacts to
`match_dominance`/`expectedPoss`, check whether an existing layer (squeeze, GK
inverted model, CDM inversion, home CDM deep-block boost) already consumes the same
signal for that prop type — pick exactly one mechanism per prop, never let two
different code paths both react to the identical input.
