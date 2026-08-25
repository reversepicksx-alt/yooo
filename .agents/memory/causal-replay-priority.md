---
name: Causal replay priority
description: How audited historical causal replays reserve provider capacity and prove no result leakage.
---

The three audited causal replay cases must run before ordinary causal enrichment after API-Football's UTC budget reset. They are cache-first, use a permanent request-keyed provider cache, hydrate only the minimal pre-kickoff samples needed for a verdict or justified PASS, and persist the final evidence ledger.

**Why:** Broad historical fan-out can consume the shared provider budget before a required replay completes. Historical target fixture endpoints now contain final results, so a replay must not read them.

**How to apply:** Keep normal causal enrichment deferred while a priority replay packet is unfinished. Only query fixtures strictly before the replay cutoff, never query the target fixture in replay mode, and retain `targetResultFieldsRead: false` plus the exact fixture IDs and cohort metrics used by the gate.