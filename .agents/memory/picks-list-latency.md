---
name: Picks list latency
description: My Picks must return the durable snapshot before settlement and provider refresh work.
---

The My Picks list response must be cache/database-first; live tracking, settlement repair, fixture refresh, and media enrichment belong in a single deduplicated background refresh.

**Why:** Provider calls can take long enough to leave the History tab on its spinner even though the saved picks already exist.

**How to apply:** Keep one per-subscriber refresh in flight, return visible stored picks immediately, and let the existing polling cycle show settlement updates after the background work finishes.