---
name: Season-stale team fixture archive
description: A large, recently written team fixture cache can still omit the current competition season after a schedule rollover.
---

Do not treat team fixture archive size or cache age as proof that the archive covers the current season. Merge the independently freshness-checked recent fixture wave into the archive before selecting player-history hydration rows.

**Why:** A multi-season archive can contain hundreds of valid older fixtures while missing the newest season, so an apparently healthy 8–11-row player cache can remain the final visible history even when newer verified appearances are available.

**How to apply:** Keep the compact recent schedule and raw archive mergeable, deduplicate by fixture ID with fresh rows taking precedence, persist the repaired pool when possible, and sort before every bounded history/H2H/comparison slice.