---
name: Competition-aware evidence
description: Competition and stage history is hierarchical, auditable, and shadow-only until replay validation promotes it.
---

Competition-aware evidence must back off from competition + stage + venue to equivalent high-stakes stage + venue, equivalent stage, broader competition, venue, and all-history buckets when samples are thin or absent. A Super Cup final belongs with elite European knockout evidence for this purpose, while the raw competition bucket remains visible. The packet must expose both the selected source and every bucket's sample size; missing competition history is unavailable, not a fabricated zero.

**Why:** A current competition can have no player history at all even when the player has relevant Champions League knockout history. Treating a Super Cup final as an isolated competition loses the high-stakes match context, while letting a tiny tournament bucket drive the Reverse Formula would overfit.

**How to apply:** Preserve verified competition ID/name and provider round on each historical fixture log. Map elite European knockout stages to a shared stage class, keep raw competition buckets auditable, and keep all evidence projection-neutral until leakage-safe settled-pick replay demonstrates out-of-sample value.