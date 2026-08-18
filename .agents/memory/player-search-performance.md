---
name: Player search performance contract
description: Interactive player search must stay bounded while preserving cached identity and club metadata.
---

Interactive player search must return from cache or one targeted provider lookup within roughly three seconds. Keep broad league/season/profile fallback chains off the typing path; use exact cached player contexts and background enrichment for metadata. When an upstream provider rate-limits, fail fast and serve previously resolved identities from a durable local index rather than sleeping and retrying every keystroke.

**Why:** Atlas latency and provider rate limits made the old sequential fallback chain leave users stuck on a spinner for 7–40 seconds. A bounded path restored sub-second to low-second results without sacrificing club identity.

**How to apply:** Preserve stale-request invalidation in the mobile search control, a strict client timeout, priority only for interactive provider calls, league-aware cache ranking, and a bounded exact playerId context lookup. Debounce rate-limited sports more generously and reuse recent client results while the provider is cooling down. Do not query a future season merely because a league was selected.

Universal search is progressive: a timeout from one slow or rate-limited sport must not hide rows already returned by another sport. Show the retry state only when the result set is still empty.

**Why:** Parallel MLB/NFL requests could time out after soccer had already returned a valid player, replacing the usable dropdown with “Search unavailable.”

**How to apply:** Keep provider result accumulation independent and make timeout/error rendering conditional on there being no accumulated rows.