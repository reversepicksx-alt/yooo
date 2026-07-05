---
name: isHome has two incompatible meanings across consumers
description: Why match-script moneyline mapping must never trust an upstream "effective venue" isHome flag, and must resolve home/away from its own odds fixture's team IDs instead.
---

There are two different, mutually incompatible meanings of "isHome" in this codebase:

1. **Effective venue framing** (used by `routes/predict.py`'s neutral-venue resolution and by `routes/misc.py`'s `/teams/{id}/next-match`): "does this team play like the home/favorite side in this context" — resolved via betting-favorite priority for international/neutral-site fixtures. This is what should drive which historical game logs get pulled for a prediction.
2. **Raw fixture designation**: "is this team literally API-Football's `fixture.teams.home`" — this is what's needed to correctly index odds payloads, since bookmaker "Home"/"Away" moneylines are always keyed to the raw fixture, not to who the market favors.

`match_script.py#get_match_script` needs meaning #2 (to map `home_ml`/`away_ml` to the right team), but was receiving an `is_home` param that upstream callers populate with meaning #1. When a neutral-site fixture's raw home/away designation disagreed with the betting favorite, this silently swapped the two teams' moneylines, producing a favorite mislabeled as a "Heavy Underdog".

**Why:** the two call sites (`/api/predict`'s venue param and `/api/match-script`'s isHome param) both trace back to the same frontend `venueOverride` state in `scan.tsx`, so making the shared upstream value effective-framing (correct for predict.py) silently broke match_script.py's raw-indexing need — fixing one consumer's semantics can invisibly break a different consumer of the same field.

**How to apply:** any function that maps odds/moneylines to a specific team must resolve home/away using that same odds payload's own team IDs (added `homeId`/`awayId` to `get_soccer_odds()`'s return), never by trusting a passed-in `is_home`/`isHome` boolean whose upstream meaning you can't fully control. Treat "is this team the home team" as ambiguous across the codebase until you've confirmed which of the two meanings the specific call site needs.
