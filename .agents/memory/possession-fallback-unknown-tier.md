---
name: Possession-data-missing must mean "unknown", not "close"
description: match_dominance always initializes expectedPoss/teamSeasonAvg to a hardcoded 50.0 float even when zero real data exists; downstream code checking "is not None" can't tell a genuine even game from total data absence.
---

`compute_match_dominance()` in `backend/routes/predict.py` seeds its result dict
with `expectedPoss: 50.0` / `oppExpectedPoss: 50.0` before it even tries to find
real data (possession stats → standings rank-gap → odds-implied win prob).
For fixtures where ALL THREE of those sources are unavailable (e.g. an
international friendly vs a minnow with no cached lineup/possession stats,
no league standings, and no pre-match odds), the dict silently stays at pure
50/50 with an empty `notes` list — indistinguishable, to any code just reading
`expectedPoss`, from a genuinely even matchup.

This caused a real miss: Argentina (huge favorite) vs Cape Verde Islands got
classified `oddsTier="close"` purely because possession data was missing, which
then (a) applied a small wrong-direction cut via odds-tier-priors, and (b)
prevented the opponent-allowed-avg "elite leak" signal (which WAS correctly
computed and showed Cape Verde's defense leaking 34-40% more passes than
baseline to opposing mids) from ever unlocking its full weight, because the
weight-boost logic required possession data to "confirm" the signal.

**Fix pattern applied:** added an explicit `match_dominance["hasRealPossData"]`
flag, set only when a real source (stats/rank-gap/odds/H2H) populated the
value (i.e. `bool(notes)`). Any downstream consumer of `expectedPoss` that
needs to distinguish "confirmed even game" from "no data at all" must check
this flag — checking `is not None` never works since the field is never None.
Also added a smaller independent-signal weight boost for strong (≥30%)
opponent-allowed-avg signals even when possession data is absent, so a real,
independently-measured signal isn't capped at a token weight just because an
unrelated data source is missing.

**Why this matters generally:** any feature in this codebase that seeds a
dict with a "safe default" value before searching for real data creates the
same trap — always add a companion boolean/sample-count field so downstream
tiering/classification logic can tell "confirmed neutral" apart from "unknown".
