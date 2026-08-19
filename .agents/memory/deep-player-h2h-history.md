---
name: Deep player H2H history
description: Rules for retrieving and counting historical player-vs-opponent soccer appearances.
---

Use the provider's bounded direct team-pairing history without a season filter. Deduplicate and retain only finished meetings, then inspect fixture player records and count a game only when the exact target player ID logged positive minutes.

**Why:** A rolling recent-season search reported zero for valid older pairings and spent the request budget on redundant calls. API-Football can also return bench or DNP rows, which would falsely inflate the player sample.

**How to apply:** Keep team-meeting coverage separate from verified player appearances. Fetch player packets before optional lineup enrichment, retain completed fixture rows when the fan-out deadline cancels pending work, expose the provider limit/date range/truncation and searched/verified counts, and preserve venue filtering for model weighting.