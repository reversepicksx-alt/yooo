---
name: API-Football player fixture fallback
description: The API-Football fixtures endpoint requires a team query for club history.
---

API-Football `/fixtures` does not accept a `player` parameter. For player-history recovery, query the verified club `team` with bounded completed fixtures, then match the exact player ID inside each fixture's player payload.

**Why:** The invalid player query returns `The Player field do not exist`, which silently produces no historical rows and causes the exact-minutes/possession verification gate to reject otherwise valid predictions.

**How to apply:** Keep the verified team identity from the selected competition, query `fixtures?team=...&last=...&status=FT`, fetch `fixtures/players` and fixture statistics per candidate, and retain only rows with exact minutes and both possession sides.