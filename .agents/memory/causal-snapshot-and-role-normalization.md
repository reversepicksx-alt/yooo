---
name: Causal snapshot and role normalization
description: Preserve assembled causal evidence and normalize provider goalkeeper role labels.
---

Evaluate prediction-attached API-Football history and cohort rows before replay-status or optional cache enrichment. A slow replay-status/cache lookup must never replace usable snapshot evidence with an empty deadline packet. Normalize provider goalkeeper roles such as `Shot-Stopper` into the GK bucket before exact-role cohort filtering.

**Why:** A replay-status database check and a slow baseline cache join previously consumed the causal deadline; separately, unnormalized goalkeeper roles silently excluded every otherwise valid GK comparison row.

**How to apply:** Keep optional cache baselines tightly bounded and fail open to the existing snapshot. Treat role labels as provider vocabulary that needs a canonical position bucket before applying exact-role, venue, formation, and opponent filters.