---
name: NWSL player search
description: How NWSL/women's soccer players are found via search when not in the global profiles index
---

## The problem
API-Football's `players/profiles?search=name` endpoint omits many NWSL/WSL players who
aren't globally prominent. The `players?search=name&league=X` combo is an API error
("League field cannot be used with Search field"). So name-based search can't be scoped to women's leagues.

## The fix
When profiles + surname fallbacks return no quality match, `search_players` in
`backend/routes/players.py` runs an **NWSL squad fallback** (triggered after the surname
fallback block, ~line 1299):

1. Fetches `players/squads?team=X` for all 16 NWSL 2026 teams in parallel
2. For teams with empty squad data (e.g., expansion team Denver Summit W, team_id=27378),
   falls back to `fixtures?team=X&season=2026&last=3` → `fixtures/players?fixture=Y`
3. Matches fetched players by name (including abbreviated form "Y. Ryan" matching "Yazmeen Ryan")
4. Writes all fetched players to Atlas `COL_PLAYERS` cache as background task
5. Returns matching players immediately

**Why:** API-Football squad endpoint returns no data for brand-new expansion teams.
Denver Summit W (2026 expansion) has no squad, but their finished fixtures have full lineups.

**How to apply:** This only runs when the profiles + surname fallbacks produce 0 quality results.
On first-ever search for an NWSL player, takes ~3-6s. Subsequent searches are instant (<0.5s) from cache.

## NWSL 2026 team IDs (hardcoded in players.py fallback)
```
2997: Chicago Red Stars W   2998: Houston Dash W
2999: North Carolina Courage W  3000: Orlando Pride W
3001: Portland Thorns W    3002: Seattle Reign FC W
3003: NJ/NY Gotham FC W    3004: Utah Royals W
3005: Washington Spirit W  16487: Kansas City W
16488: Racing Louisville W 18450: Angel City W
18451: San Diego Wave W    22943: Bay FC W
27377: Boston Legacy W     27378: Denver Summit W (expansion, squad API returns empty)
```

## Known player
- Yazmeen Ryan id=317408, team=Denver Summit W (27378), leagueId=254

## Frontend fix (FuzzySearchInput.tsx)
`searchType='all_players'` searches soccer + MLB + NFL in parallel. When query is 2+ words,
MLB/NFL results are filtered to only those matching ALL query words — prevents "Ryan Borucki"
appearing when user types "Yazmeen Ryan". Soccer results are backend-quality-filtered and never dropped.
