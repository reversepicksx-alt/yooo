---
name: Positional reality shadow packet
description: Deterministic match-script, role-zone, prop-signal, and robust-history outputs added to soccer analysis without changing live Bayesian projections.
---

The intelligent-system layer uses verified fixture-oriented odds, explicit possession provenance, lineup coordinates when available, resolved position/role, and bounded score scenarios. It produces a formal match-script classification, an attacking-direction zone, a prop-specific shadow direction/multiplier, and a robust historical summary that down-weights outliers rather than deleting observations.

**Why:** The blueprint's useful tactical ideas can be made auditable immediately, but the inputs are correlated and projected lineups/nominal coordinates are not proof of in-possession behavior. Applying them directly would risk double-counting the match script and overstating confidence.

**How to apply:** Keep `matchScript`, `positionalReality`, and `tacticalIntelligence` persisted and visible on live and saved analysis. Treat `propSignal.shadowMultiplier` as display/replay data only. Promote a signal to live projection logic only after leakage-safe settled-pick replay shows better calibration or projection error without unacceptable recommendation churn.