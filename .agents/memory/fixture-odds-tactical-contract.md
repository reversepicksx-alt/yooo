---
name: Fixture odds and tactical contract
description: Durable rules for pairing moneylines and rendering grounded player-prop tactical explanations.
---

Moneyline odds are fixture-oriented: `moneyline.home` belongs to the verified `homeTeam`, and `moneyline.away` belongs to the verified `awayTeam`. Never relabel those values from the user's original venue selection or a saved player's venue.

**Why:** A stale venue override caused the sportsbook values to appear attached to the wrong teams, especially when the verified fixture assignment differed from the scan form.

**How to apply:** Normalize odds after verified fixture resolution, expose top-level `homeTeam`, `awayTeam`, and `moneyline`, and persist all three through saved-pick and analysis fallbacks.

Tactical explanations are deterministic and evidence-gated. The role/position resolver supplies a bounded role-to-prop mechanism; verified matchup possession, opponent comparable samples, lineup shape, tempo, venue split, and momentum may add context. If an input is unavailable, omit that claim rather than inventing zones, triggers, or player behavior.

**Why:** External narrative generation was disabled and generic model boilerplate did not explain why a specific player's prop should move.

**How to apply:** Keep the structured tactical context packet with the prediction and saved pick, render role-specific mechanisms for goalkeeper/defender/midfielder/wide/forward roles, and show missing evidence as absence rather than a fabricated tactical narrative.