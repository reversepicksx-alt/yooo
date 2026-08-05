---
name: Player prop line history
description: How to interpret and preserve PrizePicks player-prop line movement.
---

The PrizePicks player-prop lines under investigation are pregame lines. The market reference currently stores only the latest board row and refresh metadata; it does not retain the earlier timestamped line, flash line, or tier that explains movement.

**Why:** A soccer pass prop moved from 53.5 to 68.5 before kickoff. The later 0–1 score cannot explain that move, so the model must investigate pregame role, lineup, minutes, team-pass expectations, and market information rather than classify the line as live context.

**How to apply:** Treat the pregame line as meaningful market evidence, but do not claim to know why it moved without snapshots. Preserve opening/current lines with capture timestamps for future validation and keep market movement separate from postgame/live state.