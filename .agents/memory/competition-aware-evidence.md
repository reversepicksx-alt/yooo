---
name: Competition-aware evidence
description: Competition and stage history is hierarchical, auditable, and shadow-only until replay validation promotes it.
---

Competition-aware evidence must back off from competition + stage + venue to broader competition, venue, and all-history buckets when samples are thin or absent. The packet must expose both the selected source and every bucket's sample size; missing competition history is unavailable, not a fabricated zero.

**Why:** A current competition can have no player history at all, while venue and broader competition samples still provide useful descriptive context. Letting a tiny tournament bucket drive the Reverse Formula would overfit.

**How to apply:** Preserve verified competition ID/name and provider round on each historical fixture log. Keep the evidence projection-neutral until leakage-safe settled-pick replay demonstrates out-of-sample value.