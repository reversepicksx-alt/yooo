---
name: Universal search contract
description: The universal player picker combines independent soccer, MLB, and NFL responses and must preserve each provider's existing payload contract.
---

The universal search UI must not require newly introduced soccer-only verification flags unless the backend guarantees those fields on every soccer search path. Filtering soccer rows on absent flags silently removes soccer results while unrelated MLB rows remain visible.

**Why:** A confirmed-team/position experiment filtered out all soccer results from the combined picker because its frontend gate ran before the existing soccer response was enriched with those fields.

**How to apply:** Test the exact combined search path, not only the soccer endpoint, whenever changing player identity fields. Keep provider-specific enrichment additive and backward-compatible, or update the combined contract and its tests in the same change.