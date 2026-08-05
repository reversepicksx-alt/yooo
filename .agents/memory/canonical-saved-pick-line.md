---
name: Canonical saved-pick line
description: The invariant that keeps displayed lines and Line Intel aligned across prediction, async analysis, and saved-pick history.
---

The request or saved pick's `line` is the canonical book line everywhere. A player's baseline or prior mean may be compared against it, but must never replace it in the header, Line Intel, async AI payload, saved-analysis lookup, factor ledger, or final snapshot.

**Why:** A player can have multiple predictions for the same prop at different lines. Matching saved analysis only by player and prop can attach an older alert to the newer pick, producing visibly contradictory values such as a 68.5 header with 53.5 Line Intel.

**How to apply:** Match saved analysis by player/prop plus line, and fixture when available. When merging cached or pending analysis, reassert the saved pick line as authoritative before returning it.