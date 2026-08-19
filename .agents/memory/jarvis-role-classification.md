---
name: JARVIS role classification
description: How role-profile and role-opponent-cohort endpoints work, key bugs fixed, and player ID reference for Arsenal Aug 2026.
---

## Rule
API-Football's `statistics[].games.position` field is long-form ("Defender", "Midfielder", "Attacker", "Goalkeeper") — not the short code ("D", "M", "F", "G") that the original fallback dicts expected. Always normalize via `_prov_long` dict before any downstream stat fingerprint or fallback, or `_classify_jarvis_role` returns "Role unavailable" for every player with only season stats.

**Why:** Without normalization, `{"D": "Stopper CB", ...}.get("DEFENDER", ...)` misses, so the classification chain silently falls through to the last-resort "Role unavailable" label even when passes/g and clearances/g data is fully available.

**How to apply:** In `_classify_jarvis_role` step 4 (`backend/routes/jarvis.py`), `_prov_long = {"goalkeeper":"G","defender":"D","midfielder":"M","attacker":"F","forward":"F"}` maps the long-form to a 1-char code; use `prov_short = _prov_long.get(provider_pos.lower(), provider_pos[:1].upper())` before any lookup. Then sub-classify "D" into CB vs LB using clearances/g ≥ 1.0 or passes/g ≥ 40; "M" into CDM/CAM/CM using tackles/g and passes/g; "F" → ST; "G" → GK.

## Grid zone availability
Grid classification (most accurate) is only available on **completed fixtures** where the lineup has been confirmed in API-Football. For future/NS fixtures, the grid field is absent and the system falls back to season-stat fingerprinting + normalized provider position. This is expected and documented in the evidence chain.

## Teammate id=None bug
`_build_teammate_context` at line 1633 built per-player dicts without the `id` field. Fix: add `"id": p.get("id")` to the comprehension inside `by_zone[lbl] = sorted([...])`.

## JARVIS save-pick endpoint
`POST /api/jarvis/save-pick/soccer` — predict + save in one call. The body has only prediction inputs; JARVIS bearer auth resolves the private owner session server-side. Returns `saved.pick_id`, `saved.tracking_id`, `correlation_warnings`, and `summary` with `p_over`, `p_under`, `prop_historical_rate`, `prop_historical_n` always present. 409 = duplicate; 507 = Atlas storage full.

## OpenAPI character limits
ChatGPT GPT Action builder rejects operation `description` fields over 300 chars. All 21 endpoints verified ≤300 chars. Both role endpoints: getRoleProfile=270ch, getRoleOpponentCohort=227ch.

## Arsenal fixture 1582365 player ID reference (Aug 16 2026, Arsenal vs Man City, 4-2-3-1, FT)
| ID     | Name            | Pos | Grid | JARVIS role               | Cohort group |
|--------|-----------------|-----|------|---------------------------|--------------|
| 22224  | Gabriel         | D   | 2:2  | Ball-Playing CB           | CB           |
| 157052 | R. Calafiori    | D   | 2:1  | (LB/WB)                   | LB           |
| 333682 | C. Mosquera     | D   | 2:3  | Ball-Playing CB           | CB           |
| 19959  | B. White        | D   | 2:4  | (RB)                      | LB           |
| 313245 | M. Lewis-Skelly | M   | 3:1  | Double-Pivot Distributor  | CDM          |
| 10135  | Bruno Guimaraes | M   | 3:2  | Double-Pivot Distributor  | CDM          |
| 161800 | C. Tzolis       | M   | 4:1  | Touchline Winger          | W            |
| 37127  | M. Odegaard     | M   | 4:2  | Attacking Midfielder      | CAM          |
| 136723 | N. Madueke      | M   | 4:3  | Touchline Winger          | W            |
| 978    | K. Havertz      | F   | 5:1  | Second Striker            | SS           |
| 19465  | D. Raya         | G   | 1:1  | Shot-Stopper              | GK           |

## Fixture for upcoming Coventry match
Fixture 1557367 = Arsenal vs Coventry, Aug 21 2026, PL, NS. Lineup not released until matchday.
Coventry City last-6 fixture IDs (for cohort): 1598608, 1585071, 1546688, 1387092, 1387083, 1387069.
