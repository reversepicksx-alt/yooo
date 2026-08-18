---
name: User settlement confirmation
description: Safety boundary for subscriber-triggered rechecks of saved soccer settlement results.
---

User-triggered settlement confirmation must be a distinct operation from a generic analysis refresh and from an owner/admin correction. It should force a new exact-fixture and exact-player provider read, validate the finished fixture/player/stat identity, and persist the provider provenance with the recalculated result.

**Why:** A cached or incrementally populated fixture/player response can make an already-settled player prop look final with the wrong value. Reusing a repair flag for a fresh read is unsafe because repair paths may intentionally bypass zero-stat or incomplete-data deferrals.

**How to apply:** Add a protected per-pick confirmation path that owns the result calculation and persistence. Force-refresh the exact provider calls, keep ordinary data-quality guards active, reject unverified/missing rows without changing the saved pick, and retain a bounded before/after audit record.