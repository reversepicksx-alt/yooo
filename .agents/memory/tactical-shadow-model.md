---
name: Tactical shadow model
description: Evidence-gated tactical context is visible in explanations and saved picks before it is allowed to change projections.
---

Tactical lineup, role, opponent-shape, moneyline, and possession signals must remain explanation/evidence context until they are replayed against settled picks and validated out of sample. Moneyline and possession are one combined game-script interpretation, not two independent full-strength adjustments. Projected lineups, nominal formations, and role comparisons must never be presented as confirmed marking assignments or average positions.

**Why:** These inputs are correlated and provider lineups are often projected. Activating them immediately can double-count the same game script and create confident but unvalidated projection movement.

**How to apply:** Keep tactical packets auditable with source/status/sample/limitation fields, persist them with saved picks, render them in live and saved analysis, and only enable bounded numeric adjustments after leakage-safe settled-pick replay demonstrates improvement. Same-role cohort weighting remains evidence/shadow-only until replay validates it.