---
name: Legacy settlement repair routing
description: Provider routing and safety rules for repairing historical soccer settlements
---

Explicit historical settlement repairs must bypass legacy BDL soccer routes and use the API-Football exact-fixture/player path. BDL mappings may still exist for other runtime flows, but they are not a safe source for repair operations when endpoints are unauthorized or rate-limited.

**Why:** Historical repair batches encountered repeated BDL 401/429 responses and could time out or stall without producing verified results. Exact API-Football validation successfully repaired records while preserving unresolved mismatches as deferred.

**How to apply:** Mark maintenance repair calls explicitly, route them around BDL, require verified provenance plus exact fixture/player identity before writing, and leave missing or mismatched provider data deferred with an audit reason.