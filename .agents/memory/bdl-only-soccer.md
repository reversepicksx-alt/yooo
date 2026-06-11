---
name: BDL-only soccer pipeline
description: API-Football is permanently removed from the soccer prediction path; BDL is the sole data source.
---

## Rule
`_is_bdl_league` is hardcoded `True` for ALL soccer predictions in `routes/predict.py` (line ~359). This gates every API-Football call via existing `not _is_bdl_league` guards or the new ones added.

**Why:** API-Football account was suspended. BDL covers all leagues the product supports (EPL, La Liga, Serie A, Bundesliga, Ligue 1, UCL, MLS, World Cup). User explicitly demanded zero API-Football calls.

**How to apply:**
- Never set `_is_bdl_league = _bdl_soc.is_bdl_league(league_id)` again — that conditional is gone.
- If a new league needs support, add it to `LEAGUE_TO_BDL` in `soccer_bdl_client.py`.
- Player search: `search_bdl_players(query)` in `soccer_bdl_client.py` searches all BDL leagues in parallel and is the live fallback in `routes/players.py` when the API-Football circuit breaker is tripped.
- `get_player_data()` in `predict.py` returns `None` immediately when `_is_bdl_league` is True (closure reads the outer scope variable at call time).
- First-goal profile (`get_first_goal_profile`) is gated with `not _is_bdl_league`.
