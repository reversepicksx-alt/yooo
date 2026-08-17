---
name: Understat opponent pressure cache
description: Rules for retrieving and persisting team-level Understat PPDA when a fixture crosses competition coverage boundaries.
---

Understat opponent PPDA does not require the player's club to be present in the
same league-season dataset. Select the newest completed covered season that has
the opponent's history, invert the fixture venue for the opponent, and cache a
compact opponent packet separately from the raw league payload.

Provider requests belong only to a scheduled background refresh. Prediction
requests read the saved league snapshot and return an explicit unavailable or
stale state when the snapshot is missing; they never fetch Understat inline.

**Why:** A Segunda club can face an opponent with recent La Liga history. Requiring
both clubs in one Understat league payload incorrectly reported the opponent as
unavailable, even though the opponent's venue-specific PPDA was present.

**How to apply:** Keep this enrichment team-level and explanation-only. Refresh
covered seasons periodically, fall back across completed seasons in the local
snapshot, label the target club's missing coverage explicitly, and never turn
missing target history into an estimated zero.