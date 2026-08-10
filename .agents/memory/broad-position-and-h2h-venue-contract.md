---
name: Broad position and H2H venue contract
description: Broad provider categories must not become tactical roles, and H2H rows must preserve fixture venue.
---

Provider categories such as `FWD`, `MID`, `DEF`, and `GK` are broad evidence only. They cannot justify an exact position or customer-facing tactical role from stat fingerprints. Exact roles require exact fixture, grounded profile, manual, or trusted lineup-history evidence.

H2H venue is fixture identity: derive it from the player's team matching the fixture home/away team, preserve it on player-appearance rows, normalize it at the mobile boundary, and render `H` or `A`. Unknown venue must remain unavailable rather than defaulting to either side.

**Why:** Broad attacker data repeatedly surfaced labels such as “Complete Forward” even when exact position evidence was unavailable, and H2H cards had venue data but omitted the H/A marker in the visible bar renderer.

**How to apply:** Enforce the broad-category boundary in every resolver and at the saved-payload UI boundary. Test backend row provenance, mobile normalization, and each H2H renderer whenever the evidence contract changes.