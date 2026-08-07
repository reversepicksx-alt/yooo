---
name: Pressure miss replay provenance
description: Historical missed-pick pressure testing requires the original fixture context, not the player's current or next fixture.
---

Pressure-response miss analysis is only valid when the replay uses the original fixture ID and prediction-time evidence. Older backtest fixtures without fixture IDs cause the prediction endpoint to resolve a current or nearest future fixture, which can produce a valid live response but cannot explain the historical miss.

**Why:** Opponent pressure, possession, lineups, and defensive actions are fixture-specific. Substituting the next fixture makes causal attribution look precise while evaluating the wrong match.

**How to apply:** Preserve fixtureId, prediction timestamp, measured possession, opponent defensive actions, and the pressure-profile evidence in each saved prediction. Reject or label legacy replays as context-mismatched instead of calling them pressure explanations.