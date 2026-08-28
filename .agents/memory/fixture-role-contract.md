---
name: Fixture and role evidence contract
description: Canonical fixture identity and role-first evidence must remain separate from the deterministic projection.
---

Canonical fixture team IDs determine home/away, venue, opponent, odds orientation, and possession orientation before downstream calculations. A generic provider category is incomplete evidence, not proof of a specific tactical role or field zone. Broad D/M/F cohorts must not be shown as subscriber decision evidence; only exact-position rows or observed provider coordinates can support positional claims. Role-specific questions, opportunity evidence, and same-role/same-venue samples should be captured for replay; missing or contradictory role evidence may cap confidence but must not fabricate data or silently change the saved pre-match projection.

**Why:** A stale venue or broad position can produce a numerically polished prediction for the wrong match context or unsupported role.

**How to apply:** Validate fixture identity at the prediction boundary, fail broad-only role/zone evidence closed, persist exact role packets with saved picks, and keep live pace changes in a separate live-confidence/projection layer.