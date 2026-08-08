---
name: Free event position metrics
description: StatsBomb Open Data can provide exact-match position-aware pass evidence, but not universal current or league-baseline coverage.
---

The free event-data path uses StatsBomb Open Data only when the exact competition, date, opponent, event stream, and lineup positions are verified. The current evidence metric counts completed/attempted passes received by the target team's normalized lineup positions and exposes it as exact-match evidence with `shadowOnly` semantics. It must not be described as a league baseline or used to change the projection.

**Why:** API-Football does not expose enough event detail to prove recipient-position claims, while StatsBomb Open Data has restricted historical coverage. Missing recipient IDs, lineup positions, or exact match coverage must remain unavailable rather than becoming zero or an AI-generated claim.

**How to apply:** Keep fixture identity and settlement on API-Football. Use StatsBomb only as a cached, provider-labeled enrichment. Promote a position/stat metric only after a separate multi-match baseline and walk-forward validation establish coverage, shrinkage, and predictive value.