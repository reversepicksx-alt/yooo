---
name: Provider team identity normalization
description: Exact player-stat settlement can fail when providers punctuate the same club abbreviation differently.
---

Normalize provider team labels beyond accent/case folding: compact-token equality is needed for variants such as “D.C. United” and “DC United.”

**Why:** Exact player matching already tolerated canonical name variants, but punctuation-only team differences caused valid finished-match rows to be treated as unavailable and left picks in review.

**How to apply:** Use normalized team identity at provider boundaries before deferring settlement; keep player ID and exact fixture checks authoritative.