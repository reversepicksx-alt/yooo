---
name: Player search abbreviated names
description: How API-Football abbreviated names (J. David) interact with the cache search logic, and the fixes applied.
---

# Player search — abbreviated first names

## The rule
API-Football stores many players with abbreviated first names: "Jonathan David" → "J. David", "Raul Jimenez" → "R. Jiménez". The `cache_players` Atlas collection stores these as `name="J. David"` with `nameClean="j. david"`.

## Why it was failing
The all-words AND query `nameClean contains "jonathan" AND "david"` never matched "j. david" because "jonathan" isn't in "j. david". A reversed-name false positive ("David Jonathan", Irish defender id=467689) DID match since it contains both literal words — so it was returned as the only result.

The last-word surname fallback only ran when `not docs`, but "David Jonathan" filled `docs`, blocking it.

Even when Pass B (targeted abbrev search) was added, a `seen_pids` guard prevented the Canada (leagueId=10) entry from being added after the Juventus (leagueId=667) entry was already in the set — so dedup couldn't pick the better entry.

## Fixes applied (backend/routes/players.py)

### sort_key: abbreviated-name rescue
If `all_match=1` (AND filter missed) AND first stored token is an initial (≤2 chars, e.g. "j.") AND initial letter == query[0][0] AND all remaining query words ARE in name → set `all_match=0`, `abbrev_rescued=True`.

### sort_key: reversed-name penalty
If stored name has all query words but first token == last query part (reversed order) → `reversed_penalty=1`. Prevents "David Jonathan" from ranking above "J. David".

### Pass A + Pass B always-merge
For 2+ word queries, ALWAYS run Pass A (surname `$regex: last_part`, limit 100) and Pass B (abbreviated pattern `^{initial}\..+{last_part}$`, limit 50) and `docs.extend()` without a seen_pids guard. Dedup handles everything after.

### Dedup: leagueId=667 (Friendlies) → rank 99
Friendly entries (leagueId=667) are often "opponent team" artefacts from fixture caching. Ranking them lowest ensures "J. David from Canada" (leagueId=10, rank=50) beats "J. David from Juventus" (leagueId=667, rank=99).

**Why:** Fixtures are cached per fixture; when Canada plays Juventus in a friendly, Jonathan David's stats appear filed under the opponent's team entry.

## How to apply
Any future changes to player search ranking or cache lookup must preserve these three pillars:
1. Pass B targeted abbrev search always runs for 2+ word queries
2. `docs.extend()` without seen_pids guard (dedup handles it)
3. leagueId=667 gets rank=99 in `_doc_rank`
