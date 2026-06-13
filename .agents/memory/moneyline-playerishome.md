---
name: Moneyline normalization — playerIsHome
description: API-Football americanOdds.home/away keys are always the fixture's home/away team, not the prediction's player perspective. Must normalize using playerIsHome.
---

## Rule
`match_odds["americanOdds"]["home"]` is **always** the fixture's home team's odds in API-Football — not the player's team's odds. Never use `homeWin if player_venue == "home" else awayWin` to get the player's team odds; use `playerIsHome` instead.

**Why:** For neutral-venue tournaments (World Cup, Copa América, etc.) API-Football assigns home/away to teams arbitrarily. The user's `player_venue` (home/away/neutral) is chosen by the frontend and may not match API-Football's fixture designation. When they differ, moneylines and Bayesian odds adjustments are swapped — e.g. Scotland shown as underdog +460 when they are actually the heavy favourite -182 (Haiti vs Scotland, WC 2026 Group C).

**How to apply:**

1. In `get_match_odds()` (routes/predict.py), tag the result:
   ```python
   fixture_home_id = fixture_match.get("teams", {}).get("home", {}).get("id")
   result["playerIsHome"] = (fixture_home_id == actual_team_id)
   ```

2. In moneyline assembly, compute orientation match:
   ```python
   _player_is_fixture_home = match_odds.get("playerIsHome", player_venue == "home")
   _pred_home_matches_fixture_home = (player_venue == "home") == _player_is_fixture_home
   # If False: swap ao["home"]↔ao["away"] and flip "favorite"
   ```

3. For any internal Bayesian odds selection (`homeWin` → player, `awayWin` → opponent):
   ```python
   _pifh = match_odds.get("playerIsHome", player_venue == "home")
   team_odds = home_odds if _pifh else away_odds   # NOT: home_odds if player_venue=="home"
   ```

**Affected lines (routes/predict.py):**
- `get_match_odds()`: adds `playerIsHome` to result
- Moneyline assembly (~line 5804): normalization + swap + favorite flip
- Heavy-favorite dampening (~line 2204): `_pifh_damp`
- GK blowout warning (~line 3627): `_pifh_gk`
