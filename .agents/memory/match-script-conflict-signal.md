---
name: Odds-derived possession vs hand-tuned tier ranges
description: Why cross-checking two signals both derived from the same odds source produces false conflict flags
---

When building a classifier that has a primary signal (e.g. moneyline) and a secondary
"expected possession" estimate mathematically derived from that *same* moneyline via a
slope formula, don't gate a "conflicting signals" flag on whether the derived estimate
falls inside a separately hand-tuned possession range for that tier. The two numbers are
correlated by construction but not calibrated to the same scale, so the derived estimate
will systematically run hotter/colder than the hand-picked range and trip false conflicts
on completely ordinary cases (e.g. -167 moneyline favorite got flagged as "no clean
script" because the odds-implied possession estimate landed just outside the tier's
possession band).

**Why:** A conflict/warning flag is only meaningful when the two signals are
independently sourced. If one is a deterministic function of the other, treat it as
supporting context in the explanation text, not as a gate.

**How to apply:** When adding a secondary derived metric alongside a primary
classifier, ask whether it's truly independent data (different API/source) before using
it to suppress or downgrade confidence. If it's derived from the primary signal, reserve
the "no clean data" flag for actual missing-data conditions instead.
