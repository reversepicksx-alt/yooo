---
name: Player search performance contract
description: Interactive player search must stay bounded while preserving cached identity and club metadata.
---

Interactive player search must return from a targeted token-prefix cache query or one bounded provider lookup within roughly three seconds. Keep broad league/season/profile fallback chains and full collection scans off the typing path; use exact cached player contexts and background enrichment for metadata. A small process-local hot identity cache may serve recent queries immediately, but it is only an accelerator, not the source of truth.

**Why:** Atlas latency and provider rate limits made the old sequential fallback chain leave users stuck on a spinner for 7–40 seconds. A bounded path restored sub-second to low-second results without sacrificing club identity, while loading all cached identities at startup was still too slow and competed with live searches.

**How to apply:** Preserve stale-request invalidation in the mobile search control, a strict client timeout, a short player-search trigger, priority only for interactive provider calls, indexed token-prefix cache filters, league-aware cache ranking, and a bounded exact playerId context lookup. Debounce rate-limited sports more generously and reuse recent client results while the provider is cooling down. Do not query a future season merely because a league was selected.

Universal search is progressive: a timeout from one slow or rate-limited sport must not hide rows already returned by another sport. Show the retry state only when the result set is still empty.

**Why:** Parallel MLB/NFL requests could time out after soccer had already returned a valid player, replacing the usable dropdown with “Search unavailable.”

**How to apply:** Keep provider result accumulation independent and make timeout/error rendering conditional on there being no accumulated rows.

Legacy native universal-search clients may await soccer, MLB, and NFL requests together. Optional sport search routes must have their own short timeout and return an empty list on provider stalls, or a valid soccer identity can still be discarded by the old client's total timeout.

**Why:** The installed iOS binary waited for a hanging NFL/MLB provider even after soccer returned the requested player; the current progressive source fix was not embedded in that binary.

**How to apply:** Keep this fail-closed compatibility behavior on auxiliary MLB/NFL search endpoints until all supported clients are progressive, and never let an optional provider return a slow 5xx on the typing path.

Single-word cache prefixes must not suppress the bounded provider lookup when no cached row matches the literal token. A near-prefix hit such as "Cristian" is not an exact result for "Cristiano".

**Why:** A cache-first partial match could return misleading names before the provider supplied the canonical player identity.

**How to apply:** Require an exact single-word cache match before returning early; otherwise continue through the bounded provider path and keep the exact result ranked first.