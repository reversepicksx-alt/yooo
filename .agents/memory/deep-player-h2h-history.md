---
name: Deep player H2H history
description: Rules for retrieving and counting historical player-vs-opponent soccer appearances.
---

Search H2H fixtures across multiple provider seasons instead of relying on one current-season response. Deduplicate and retain only finished meetings, then inspect the fixture player records and count a game only when the target player logged positive minutes.

**Why:** A season-scoped H2H response and a five-fixture cap made valid 4–5+ meeting histories appear inconsistently. API-Football can also return bench or DNP rows, which would falsely inflate the sample if counted.

**How to apply:** Keep the broader fixture search separate from the bounded model sample. Use the deeper meetings to find real player appearances, expose the searched depth/sample metadata, and preserve venue filtering for model weighting.