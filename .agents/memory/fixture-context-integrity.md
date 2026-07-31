---
name: Fixture context integrity
description: Soccer predictions must derive opponent and venue from the verified fixture.
---

For soccer predictions, the opponent, opponent ID, venue, fixture ID, odds,
possession context, and narrative must come from the same verified fixture.
Never keep a user-requested opponent name when fixture lookup falls back to a
different upcoming match. The prediction pipeline is authoritative; save-time
fixture verification logs anomalies but must not block a freshly returned result
when an API lookup or team-ID comparison is unavailable.

**Why:** A stale opponent request can survive a next-fixture fallback and
produce a card like Corinthians vs Bahia while the actual match is Corinthians
vs Athletico. This makes otherwise coherent possession and projection math
meaningless. Conversely, a second save-time lookup can be rate-limited or
represent the IDs differently and falsely reject a correct prediction.

**How to apply:** Canonicalize the matchup before opponent-dependent work,
return the verified fixture identity to the client, persist the canonical team
IDs, opponent ID, venue, and fixture ID with the pick, and log—but do not
recreate a hard save failure from—a secondary verification mismatch.

Player IDs can have both club and national-team cache rows. For a domestic
league request, resolve the player's team context from the selected league
before fixture lookup; never let an international cache row such as Mexico
replace the player's domestic club.

**Why:** API-Football reuses the same player ID across club and national-team
statistics. Search results or stale request payloads can therefore point
Analyze at the wrong team's fixtures even when the correct club fixture exists.

**How to apply:** Treat the selected competition as authoritative for club
context. Preserve international context for international competitions, but
choose the matching domestic club row before deriving opponent, venue, odds, or
fixture ID.