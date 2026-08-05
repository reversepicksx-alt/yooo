---
name: Deterministic provider removal
description: Durable maintenance rule for removing external generation while preserving prediction response compatibility.
---

When external generation is retired, compatibility-shaped fields must still be initialized explicitly and derived from deterministic ledger/evidence data. Remove provider calls and prompt scaffolding, but do not remove shared response aggregates that downstream math, persistence, or UI code still reads.

**Why:** Removing provider-era blocks can also remove a variable initializer that looks narrative-only but is still consumed by numeric adjustments or response assembly. A real authenticated prediction smoke test catches this class of regression.

**How to apply:** Before deleting a generation block, search every variable it created across the whole route. Replace required values with deterministic aggregates or explicit empty compatibility values, then run an authenticated end-to-end prediction and inspect the final metadata.