---
name: First-goal audit response
description: How full-audit exposes first-goal evidence without changing Reverse Picks math.
---

First-goal evidence is explanatory, shadow-only response metadata. The full-audit route must retain or fetch it after the single immutable Reverse Picks prediction when the core request’s response budget skips late enrichment. Expose the availability state explicitly in `game_state`, `first_goal_market`, and `first_goal_regime_change`; never let it alter projection, probability, recommendation, or save behavior.

**Why:** The core prediction optimizes for a bounded subscriber response time, so late optional enrichment can be skipped even though an audit caller explicitly needs the evidence. The shared API client returns response rows directly while some direct clients retain the provider envelope; treating one as the other silently converted valid profiles into unavailable results. Caching a temporary unavailable profile then prolonged the false result.

**How to apply:** Normalize provider data at the module boundary, cache only successfully built evidence profiles, and make any audit-specific retrieval an after-prediction fallback with clear provenance and shadow-only labeling.