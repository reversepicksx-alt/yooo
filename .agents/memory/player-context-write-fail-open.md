---
name: Player context write fail-open
description: Verified player identity and current-club context must survive analytics/cache storage outages
---

Current-club verification is provider evidence; writing that evidence to Atlas is only persistence. If Atlas is write-blocked or unavailable, return the verified context and let the prediction flow continue rather than converting it into a 500 or manual-match state.

**Why:** An Atlas storage quota outage caused a valid verified player selection to appear as “current club unavailable,” even though the provider had resolved the player’s club.

**How to apply:** Wrap context/player cache writes independently, log the skipped write, preserve the search result’s verified team as a client fallback, and only clear identity when provider evidence itself is unavailable.