---
name: Picks tab terminal status
description: My Picks must classify saved records from terminal status as well as result fields.
---

The Live/History split must trust normalized persisted status first. A record with `status=settled` belongs in History even if a legacy row has no result or actual value; provider live/final markers should be compared case-insensitively.

**Why:** Legacy and partially repaired records can have a terminal database status without the newer result metadata. Requiring a derived outcome silently makes History appear empty while the picks exist.

**How to apply:** Normalize status and match status before filtering, make terminal states win over stale live markers, and keep scheduled/active saved records visible in the active tab.