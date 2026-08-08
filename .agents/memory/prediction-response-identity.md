---
name: Prediction response identity normalization
description: Soccer prediction responses can contain explicit null identity fields even when fixture resolution succeeded.
---

The final prediction boundary must normalize explicit nulls, not only missing keys. Preserve verified fixture teams, IDs, venue, and fixture date; fill request-shaped player, league, and venue fields only when the resolved response is absent.

**Why:** A live Kannemann prediction had correct nested fixture and player data but null top-level playerName, leagueId, venue, and playerIsHome, which could break mobile cards and saved-pick consumers.

**How to apply:** Assert the complete top-level identity contract in end-to-end prediction tests after JSON-safe serialization, including player, team/opponent, league, fixture, venue, line, and sport fields.