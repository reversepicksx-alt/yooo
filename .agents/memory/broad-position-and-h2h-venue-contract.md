---
name: Broad position and H2H venue contract
description: Broad provider categories must not become tactical roles, and H2H rows must preserve fixture venue.
---

Provider categories such as `FWD`, `MID`, `DEF`, and `GK` are broad evidence only. They cannot justify an exact position or customer-facing tactical role from stat fingerprints. Exact roles require exact fixture, grounded profile, manual, or trusted lineup-history evidence. A generic current row is incomplete detail, not contradictory evidence: it must not erase a verified exact position from player-ID history.

H2H venue is fixture identity: derive it from the player's team matching the fixture home/away team, preserve it on player-appearance rows, normalize it at the mobile boundary, and render `H` or `A`. H2H player rows should also fetch the exact fixture lineup grid so broad `FWD`/`F` stats can be upgraded to `ST`, `CF`, `LW`, or `RW` where the formation supports it. Unknown venue must remain unavailable rather than defaulting to either side.

**Why:** Broad attacker data repeatedly surfaced labels such as “Complete Forward” even when exact position evidence was unavailable. The first safety fix overcorrected by suppressing legitimate exact-position cohorts, even though lineup-grid and H2H evidence could resolve them. H2H cards also had venue data but omitted the H/A marker in the visible bar renderer.

**How to apply:** Enforce the broad-category boundary in every resolver and at the saved-payload UI boundary, but preserve trusted exact positions when current provider rows are generic. Use fixture-scoped lineup grids to resolve common formations before declaring exact-position evidence unavailable. Test backend row provenance, mobile normalization, and each H2H renderer whenever the evidence contract changes.