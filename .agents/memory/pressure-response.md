---
name: Player pressure-response profiles
description: API-Football passing profiles classify player response to low-possession environments before any projection adjustment is enabled.
---

Player pressure-response profiles must remain shadow-only until leakage-safe walk-forward validation shows that the player-specific interaction improves pass projection accuracy. API-Football's team possession is a pressure proxy, not a direct measurement of passes under pressure; use that provenance explicitly and require both high- and low-possession samples.

**Why:** API-Football does not provide a universal player-level passes-under-pressure field, and low possession can represent a deep block or counterattacking plan rather than an active press. An unvalidated multiplier would confuse team game script with player resilience.

**How to apply:** Keep the profile descriptive in prediction evidence and deterministic explanations. Require at least six qualifying full-game appearances in each bucket, shrink small effects toward neutral, and only enable a bounded live adjustment after out-of-sample settled-match replay.