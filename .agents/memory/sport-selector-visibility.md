---
name: Sport selector visibility
description: User preference for where the sport selector appears in the player prediction flow.
---

Keep the sport selector available while setting up a player search, with results restricted to the selected sport. Do not render the interactive selector on the player prediction analysis or saved-result screen; the selected sport may remain as read-only context.

**Why:** The selector is useful before search, but appearing after a player prediction is open is confusing and can invite accidental taps that disrupt the analysis.

**How to apply:** Gate the interactive selector on the pre-analysis state (`phase` not result/saved). Keep the player search sport-specific rather than universal.