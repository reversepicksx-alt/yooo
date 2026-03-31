# ReversePicks - Multi-Sport Player Props AI Analytics

## Problem Statement
Multi-sport player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players/teams, and runs a multi-AI consensus prediction pipeline. Supports Soccer, NBA, and WNBA with full live in-game tracking.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Multi-Sport**: Sport selector (Soccer/Basketball). Basketball covers NBA+WNBA.
- **Data Cache**: All leagues, teams, players in MongoDB
  - Soccer: `cache.py` — leagues, teams, squads, national teams
  - Basketball: `basketball_cache.py` — NBA (31 teams, 744 players) + WNBA (15 teams, 253 players)
- **Prediction Pipeline**: 5-AI Consensus (Grok, Gemini x2, GPT-4o, Claude) first-3-wins
- **Live Tracking**: Real-time in-game stats (basketball Q1-Q4/OT, soccer 1H/2H)

## Key Bug Fixes Applied
- **propType normalization**: Picks stored as display labels ("Pts+Reb+Ast") now normalized to internal keys ("pts_reb_ast") on save and in stat extraction
- **PRA/compound props**: get_bball_stat_value normalizes "+" to "_" before lookup
- **Timer handling**: "0" at halftime no longer breaks pace calculation
- **Team resolution**: NBA league=12 filter prevents wrong team matches
- **Game matching**: Live games always match regardless of pick timestamp

## Prediction Quality Improvements
- Full season game logs (60+ games vs previous 20)
- Last-5 and Last-10 game averages for momentum
- Over/under rate vs the specific line
- Opponent defensive rating (points allowed per game)
- Game pace context (high/mid/low pace)

## Key Files
- `/app/backend/routes/picks.py` — Unified live tracking (soccer + basketball), propType normalization
- `/app/backend/routes/basketball_predict.py` — Basketball prediction, improved data digest
- `/app/backend/basketball_cache.py` — NBA+WNBA data cache
- `/app/backend/basketball_utils.py` — Basketball API helpers
- `/app/backend/routes/predict.py` — Soccer prediction
- `/app/backend/routes/scan.py` — Sport-aware OCR scan
- `/app/frontend/src/App.js` — UI with sport selector, tracking tab, live cards

## Completed Work
- Multi-sport (Soccer + Basketball/NBA/WNBA)
- 5-AI consensus + synthesis engine (~20-30s)
- Basketball data cache (46 teams, 997 players)
- Live in-game tracking (quarter, stats, pace, hit%)
- Tracking ID (TRK-XXXXXXXX) on every card
- Sport label on every card
- propType normalization (display labels → internal keys)
- Improved prediction data quality (full season, momentum, opponent defense)
- Games tracked regardless of when pick was made
- Auto-settle on game finish

## Prioritized Backlog
### P2: Slip correlation, Prediction feedback loop, Batch scan
### P3: SofaScore integration
