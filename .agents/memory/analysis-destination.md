---
name: Full analysis destination
description: Product decision for where prediction narratives and supporting evidence should appear.
---

The saved-pick/player analysis view is the canonical destination for the complete prediction explanation. Pick/result cards should remain concise and should not duplicate the full evidence feed.

**Why:** Showing the same narrative, game logs, matchup cards, and manager context in multiple places made the result screen noisy and hid the intended analysis destination behind an expandable control.

**How to apply:** Render the full Tactical AI narrative by default in the analysis view, show recent game data as its own labeled section, and label real Gemini output separately from deterministic math fallback text. Do not reintroduce a “FULL BREAKDOWN” toggle for the main explanation.