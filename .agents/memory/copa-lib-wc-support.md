---
name: Copa Libertadores / World Cup competition support
description: What was added to support Copa Lib, Copa Sud, WC 2026, and all SA domestic leagues.
---

## Rule
Player search, scan detection, and predictions all need explicit handling for South American competitions and World Cup (league_id=1).

## Changes made

### scan.py
- `TEAM_LEAGUE_MAP` expanded: all Copa Lib/Sud clubs from Colombia (239), Bolivia (21), Peru (281), Chile (265), Uruguay (270), Paraguay (250), Venezuela (299), plus Argentina (128), Brazil (71), Ecuador (242).
- `SOUTH_AMERICAN_LEAGUES = {13, 11, 128, 71, 242, 239, 265, 270, 281, 299, 250}` (was `{71, 128, 242}`)
- Copa Sudamericana (league_id=11) detection added — trusts `ai_league_id==11` before defaulting to 13
- `_SA_DOMESTIC` in `_validate_player_league` expanded to all 10 SA domestic leagues (not just 3)

### predict.py
- `_is_wc = (league_id == 1)` — World Cup mode
- WC skips national-team fixture filter and venue split (neutral venue)
- `isWorldCup: True` injected into `match_stakes`; response tagged `wcMode: True`

### bayesian_engine.py
- WC HIGH_STAKES +5% volume multiplier (triggers when `match_stakes.isWorldCup`)

### grok_engine.py
- `_try_settle_wc_via_grok()` — Grok web search settlement fallback for WC/no-API-stats picks

### players.py
- `major_leagues` fallback expanded to include Copa Lib (13), Copa Sud (11), all SA domestic leagues

## How to apply
- Player search URL: `/api/players/search` (prefix is `/api`) — test failure at `/players/search` returns 404
- Copa Lib player search works via Strategy 1 (API Football direct) since SA leagues are not pre-cached
- WC 2026: API-Football has `statistics_players=False` → settlement uses Grok web search fallback
