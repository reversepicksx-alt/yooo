---
name: Player prop line history
description: How to interpret and preserve PrizePicks player-prop line movement.
---

The PrizePicks market reference currently stores only the latest board row and refresh metadata. It does not retain a timestamped history of a player prop's line, flash line, or tier. A stored line can therefore be a late pre-match, live, or post-kickoff value.

**Why:** A soccer pass prop moved from 53.5 to 68.5 around a match where the player's team later trailed while dominating possession. Without snapshots, the movement cannot be attributed to pre-match information, live score state, lineup news, or provider correction.

**How to apply:** Require a timestamped market snapshot before using player-prop movement as model evidence. Label an un-timestamped line as current-market context only, and keep it out of pre-match projection math, calibration, and causal explanations.