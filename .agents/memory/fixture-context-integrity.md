---
name: Fixture context integrity
description: Soccer predictions must derive opponent and venue from the verified fixture.
---

For soccer predictions, the opponent, opponent ID, venue, fixture ID, odds,
possession context, and narrative must come from the same verified fixture.
Never keep a user-requested opponent name when fixture lookup falls back to a
different upcoming match. If a saved fixture ID does not contain the stored
team pair, reject the save instead of tracking or settling it.

**Why:** A stale opponent request can survive a next-fixture fallback and
produce a card like Corinthians vs Bahia while the actual match is Corinthians
vs Athletico. This makes otherwise coherent possession and projection math
meaningless.

**How to apply:** Canonicalize the matchup before opponent-dependent work,
return the verified fixture identity to the client, persist it with the pick,
and fail closed when the fixture identity and team pair disagree.