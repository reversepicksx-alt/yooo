---
name: Matchup-volume transport
description: Preserve venue evidence through backend responses, mobile normalization, and versioned fixture caches.
---

Evidence can be correctly computed server-side and still be invisible if the mobile response normalizer drops the new field. Venue packets also need a versioned cache key whenever their shape or coverage contract changes.

**Why:** The matchup-volume work initially reached the backend and renderer, but an omitted mobile mapping plus stale empty packets made the subscriber screen appear unchanged.

**How to apply:** For every new prediction payload field, update the backend response, raw mobile type, normalized mobile result, and rendered component together. Bump cache identity when packet coverage or semantics change.