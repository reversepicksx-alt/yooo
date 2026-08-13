---
name: Picks list latency
description: My Picks must return the durable snapshot before settlement and provider refresh work.
---

The My Picks list response must be cache/database-first; live tracking, settlement repair, fixture refresh, and media enrichment belong in a single deduplicated background refresh.

**Why:** Provider calls can take long enough to leave the History tab on its spinner even though the saved picks already exist.

**How to apply:** Keep one per-subscriber refresh in flight, return visible stored picks immediately, and let the existing polling cycle show settlement updates after the background work finishes.

The list response must also use a compact card projection. Full historical analysis blobs and provider payloads do not belong in the list response; fetch those only when a user opens analysis.

**Why:** A valid 200 response can still be unusable when thousands of saved picks make the serialized payload exceed the proxy/browser budget, leaving the UI spinner active after the backend has completed.

**How to apply:** Keep identity, settlement, live-value, venue, and card-display fields in `/picks/list`; omit logs, ledgers, tactical history, and cached provider documents.