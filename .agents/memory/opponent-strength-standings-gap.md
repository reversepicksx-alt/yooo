---
name: Opponent-strength tier depends on a single standings table
description: Why "OPP STRENGTH" tier/dots (current-match badge and per-game-log dots) can silently disappear for national-team and cross-competition matches, and the fallback chain used to fix it.
---

Both the current-match "vs {opponent} [TIER]" badge and the per-historical-game-log tier dots were derived from ONE standings table fetched for the *current* prediction's `league_id`/season. A team's actual game log (last ~40 fixtures) commonly spans multiple different competitions (domestic league, cup, continental competition, qualifying groups, friendlies/playoffs) that never share a standings table — so any opponent not in that one table silently got `oppRank=None` → no tier, no dot. This was most visible for national teams, whose game logs mix confederations/qualifying groups/friendlies almost by definition.

**Why:** the standings fetch only requests `{"league": league_id, "season": s}` once, then reuses that single rank_map to look up every opponent by fuzzy name match — there's no per-fixture competition awareness.

**How to apply:** when a real standings-based rank is unavailable, fall back in order: (1) a curated static tier table by opponent name (works with zero extra API calls; only needed for national teams since domestic club standings are usually already correct), (2) odds-implied win probability (normalized decimal odds, remove vig, map to opponent-win-prob buckets) — works for any match with a betting market regardless of competition, but only for the *current* match (historical odds for old fixtures aren't stored/fetched). Never let unlisted/unknown opponents get a fabricated tier — leave them at `None` (no dot) rather than guessing, to avoid a worse bug (confidently wrong tier).
