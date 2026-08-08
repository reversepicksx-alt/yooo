---
name: Bzzoiro enrichment boundary
description: Bzzoiro supplies optional football lineup, average-position, player-stat, and defensive-actions evidence; it is not a true PPDA source.
---

Bzzoiro is a secondary evidence provider. Bridge identity by verified team/player names because its numeric IDs differ from API-Football. Use exact date/opponent matching, treat empty coverage as unavailable, and keep its one-match defensive-actions pressure proxy shadow-only.

**Why:** Bzzoiro provides rich observed-position and match-stat context but does not expose universal pressure events, passes-under-pressure, or defensive-third passes needed for true PPDA. API-Football remains authoritative for fixtures, projections, and settlement.

**How to apply:** Attach Bzzoiro data to tactical evidence and audit snapshots only. Require settled-pick replay and commercial-use confirmation before using any Bzzoiro signal to alter projections or production decisions.