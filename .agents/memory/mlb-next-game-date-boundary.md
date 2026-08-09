---
name: MLB next-game date boundary
description: MLB auto-fill must reject stale completed games before they reach the prediction form.
---

MLB next-game responses must be validated against the current UTC calendar date at both the API response boundary and the mobile display boundary. A cached or provider-returned game from an earlier date must become `found: false`, never “NEXT GAME.”

**Why:** A stale Aug 8 response was shown as Ohtani’s next game on Aug 9 even though the live schedule had an Aug 9 matchup.

**How to apply:** Normalize MLB schedule dates to `YYYY-MM-DD`, reject missing or invalid dates, and compare calendar dates in UTC rather than relying only on provider status or cache TTL.