---
name: Unified engine contract
description: Rules for adding new sports so every UI feature works automatically without frontend sport gates.
---

## The Rule
The mobile UI (`scan.tsx`) must render features based on **data presence**, never `prediction.sport === 'x'`.
The only remaining soccer-specific gates are: PrizePicks market line card, pitch lineup diagram (both inherently soccer concepts).

## Standard Fields Every Sport Must Return
See `backend/engine_base.py` STANDARD_FIELDS for the full list. Key required ones:

| Field | Shape | Used for |
|---|---|---|
| `gameLogs` | `[{value, opponent, venue:'home'/'away'/'neutral', score, date}]` | Game log tile grid (already sport-agnostic at scan.tsx ~3591) |
| `matchupOverview` | `{homeTeam, awayTeam, playerIsHome, expectedGameType, keyMatchupFactor, moneyline?}` | Matchup card (non-soccer) |
| `sharpSummary` | string | AI Analysis card |
| `reasoning` | string | AI Analysis card |
| `tacticalBreakdown` | string | Tactical AI deep analysis card |
| `riskSignals` | `{redCardRisk?, note?}` | Risk/congestion card |

## How To Add a New Sport — Checklist
1. **Backend route**: return all STANDARD_FIELDS. Call `normalize_response(response)` at the end (from `engine_base.py`) to auto-fill any gaps.
2. **`gameLogs`**: ensure each log has `venue` ('home'/'away'/'neutral'), `score` (display string), `opponent` (name), `value` (the stat).
3. **`matchupOverview`**: populate homeTeam/awayTeam/playerIsHome/expectedGameType/keyMatchupFactor.
4. **AI analysis**: every sport already calls the AI engine — make sure `sharpSummary`, `reasoning`, `tacticalBreakdown` are passed through.
5. **`mobile/lib/api.ts`**: add a new `<sport>Predict()` function. Map `raw.matchupOverview` → `matchupOverview` in the return object. Map `raw.tacticalBreakdown` → `tacticalBreakdown`.
6. **`mobile/app/(tabs)/scan.tsx`**: wire up the new sport's input form and call the new predict function. NO sport gates needed — all features render automatically.

## What Was Fixed (2026-07-23)
- MLB: `_enrich_game_logs` now adds `venue`/`score` fields. `mlb_predict` adds `matchupOverview`. `mlbPredict()` in api.ts now passes `matchupOverview` + `tacticalBreakdown`.
- WTA: `wta_predict` adds `matchupOverview` + normalizes gameLogs with `score`/`venue`/`opponent`. `wtaPredict()` in api.ts now passes `matchupOverview` + `tacticalBreakdown`. Fixed WTA gameLogs `venue` (was wrongly derived from `wonMatch`; now uses `venue` field = 'neutral').
- scan.tsx: Removed `prediction.sport === 'soccer'` gates from AI Analysis card, Tactical AI card, Risk/Congestion card. `setTacticalAnalysis` now falls back to `result.tacticalBreakdown` (so non-soccer AI text shows immediately).
- scan.tsx: Added sport-agnostic MATCHUP OVERVIEW card (before game log grid) for non-soccer sports. Game log tiles were already sport-agnostic.

**Why:** Without this contract, every new sport required manually hunting down ~8 feature gaps across backend + frontend and wiring each one individually — the same rework every single time.
