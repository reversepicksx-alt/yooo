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

**Correction:** this file's diagnosis for the Enzo Fernández/Cape Verde miss
was made against the wrong league context (international friendly, league_id
10) before the user corrected it to World Cup Round of 32 (league_id 1).
Re-investigated under the correct league: the actual reason "all real data
was missing" for that fixture was a separate, more fundamental bug — see
[silent-asyncio-alias-bug.md](silent-asyncio-alias-bug.md). Once that was
fixed, real odds/possession data (72%/28%, heavy favorite, knockout match)
flowed through normally and `hasRealPossData` correctly went true. The
`hasRealPossData` flag and opponent-independent-signal boost documented above
are still valid, legitimate hardening for cases where data is genuinely
absent — they just weren't the root cause of this particular case.

**Second gap found later — `hasRealPossData` does NOT cover `teamSeasonAvg`:**
`hasRealPossData` is `bool(notes)`, and the odds-only fallback branch (used
whenever a team has no cached fixture stats/standings, e.g. national teams)
itself appends a note, so `hasRealPossData` reads `True` for odds-only
matches — but that same branch also hardcodes `teamSeasonAvg`/`oppSeasonAvg`
to a flat `50.0`/`50.0` with zero real season-average signal behind it. Any
downstream ratio calc of `expectedPoss / teamSeasonAvg` (e.g. GK "inverted
possession" boost/penalty, outfield possession squeeze, CDM inversion) was
comparing a real odds-derived expectedPoss against a fake denominator,
producing large false-positive multipliers (Colombia GK Camilo Vargas:
67% real expected poss vs fake 50% "season avg" → wrongly flagged as
extreme dominance → -20% GK penalty → 20-pass under-projection vs his
actual halftime pace). Fix: added a second, narrower flag
`seasonAvgIsReal` (true only when teamSeasonAvg came from actual cached
team fixture stats, not the odds-only branch) and gated all
`teamSeasonAvg`-ratio multiplier logic in `bayesian_engine.py` on it — do
not reuse `hasRealPossData` for this since it means something different
("expectedPoss has some grounding") than "teamSeasonAvg is a real average".
Also stopped exposing fake `teamSeasonAvg`/`oppSeasonAvg` to the API
response when `seasonAvgIsReal` is false, since the mobile "OPP POSS"
season-avg badge was rendering the fake 50% as if it were a real signal.
