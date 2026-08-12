---
name: Matchup-volume transport
description: Preserve venue evidence through backend responses, mobile normalization, and versioned fixture caches.
---

Evidence can be correctly computed server-side and still be invisible if the mobile response normalizer drops the new field. Venue packets also need a versioned cache key whenever their shape or coverage contract changes.

**Why:** The matchup-volume work initially reached the backend and renderer, but an omitted mobile mapping plus stale empty packets made the subscriber screen appear unchanged.

**How to apply:** For every new prediction payload field, update the backend response, raw mobile type, normalized mobile result, and rendered component together. Bump cache identity when packet coverage or semantics change.

Market evidence should be rendered by prop type, not as a generic all-sports/all-props card: goalkeeper saves need opponent SOT and the player’s save rate; pass attempts need team volume, opponent allowed volume, and player share; possession is not useful for goalkeeper saves.

**Why:** A generic shadow card duplicated inputs, used team names as if they were players/keepers, and made the analysis much taller without improving the selected prediction.

**How to apply:** Keep the packet projection-neutral, but place only market-relevant fields inside the existing analysis card and keep verified H/A H2H markers visible.