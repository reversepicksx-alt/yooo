# ReversePicks - Multi-Sport Player Props AI Analytics

## Problem Statement
Multi-sport player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players/teams, and runs a multi-AI consensus prediction pipeline. Supports Soccer, NBA, and WNBA with full live in-game tracking.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Multi-Sport**: Sport selector (Soccer/Basketball). Basketball covers NBA+WNBA.
- **Data Cache**: All leagues, teams, players in MongoDB for instant lookups
  - Soccer: `cache.py` — leagues, teams, squads, national teams
  - Basketball: `basketball_cache.py` — NBA (31 teams, 744 players) + WNBA (15 teams, 253 players)
- **Prediction Pipeline**: 5-AI Consensus (Grok, Gemini x2, GPT-4o, Claude) first-3-wins
- **Live Tracking**: Real-time in-game stats for both sports
  - Basketball: Quarter tracking (Q1-Q4, HT, OT), player stats via `games/statistics/players`
  - Soccer: Half tracking (1H, 2H, HT), player stats via `fixtures/players`
  - Auto-settle on game finish

## Live Tracking System
### How it works
1. `/api/picks/save` — Stores pick with `trackingId` (TRK-XXXXXXXX) and `sport` field
2. `/api/picks/list` — Backfills trackingId and sport for old picks
3. `/api/picks/live-update` — Splits picks by sport, queries live game APIs:
   - Basketball: `v1.basketball.api-sports.io/games?team=ID` + `games/statistics/players?id=GAME_ID`
   - Soccer: `v3.football.api-sports.io/fixtures?team=ID` + `fixtures/players?fixture=ID`
4. Returns: `currentValue`, `pace`, `hitPct`, `quarter/period`, `matchScore`
5. Auto-settles finished games (hit/miss/push)

### Key fixes applied
- Games that started BEFORE the pick was saved are now tracked (live=always match)
- Timer edge case: "0" timer at halftime no longer breaks pace calculation
- propType case-insensitive matching ("Points" == "points")
- Old picks backfilled with sport field via player name cache lookup
- Every card has a tracking ID for user peace of mind

## Key Files
- `/app/backend/routes/picks.py` — Unified live tracking (soccer + basketball)
- `/app/backend/basketball_cache.py` — NBA+WNBA data cache
- `/app/backend/routes/basketball_predict.py` — Basketball prediction engine
- `/app/backend/routes/predict.py` — Soccer prediction engine
- `/app/backend/routes/scan.py` — Sport-aware OCR scan
- `/app/frontend/src/App.js` — UI with sport selector, tracking tab, live cards

## Completed Work
- Multi-sport support (Soccer + Basketball)
- 5-AI consensus + synthesis engine (~20-30s)
- Basketball data cache (46 teams, 997 players)
- **Live in-game tracking for basketball** (quarter, stats, pace, hit%)
- **Live in-game tracking for soccer** (half, stats, pace, hit%)
- **Tracking ID on every card** (TRK-XXXXXXXX format)
- **Sport label** on every card (NBA/Soccer)
- Games tracked regardless of when pick was made
- Auto-settle on game finish
- Sport-aware OCR scan
- Player ID lookup via cache (instant) with live API fallback

## Prioritized Backlog
### P2: Slip correlation, Prediction feedback loop, Batch scan
### P3: SofaScore integration
