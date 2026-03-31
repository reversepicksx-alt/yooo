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
- **Streak detection**: Fixed double-counting bug in streak logic

## Prediction Quality v2 — Advanced Analytics Engine (Feb 2026)
- **Per-Minute Rate Analysis**: Computes production rate per minute, projects stat total from rate x avg minutes
- **Role Classification**: Categorizes players as STAR (32+ min), STARTER (26-32), ROTATION (16-26), BENCH (<16)
- **Line Proximity Z-Score**: Measures edge strength — COIN FLIP (<0.3σ), SLIGHT LEAN, MODERATE EDGE, STRONG EDGE, VERY STRONG EDGE
- **Over/Under Rate**: Computes exact % of games player went OVER vs UNDER the specific line (season + L10)
- **Statistical Lean**: Data-driven OVER/UNDER recommendation based on hit rates
- **Consistency Score**: VERY CONSISTENT (70%+), MODERATE, or BOOM-BUST based on variance
- **Streak Detection**: Tracks current consecutive OVER or UNDER streak
- **Blowout Risk**: Estimates minute reduction risk from lopsided matchups
- **Data-Driven Overrides**: If over-rate >= 70% and AI says UNDER, force OVER (and vice versa)
- **Confidence Capping**: COIN FLIP scenarios capped at 52% confidence
- **Projection Clamping**: AI projections clamped within 30% of rate-based projection

## Key Files
- `/app/backend/routes/picks.py` — Unified live tracking (soccer + basketball), propType normalization
- `/app/backend/routes/basketball_predict.py` — Basketball prediction v2, advanced analytics engine
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
- propType normalization (display labels -> internal keys)
- **Advanced analytics engine v2** (per-minute rates, role classification, z-scores, overrides, consistency, streaks)
- Games tracked regardless of when pick was made
- Auto-settle on game finish

## Prioritized Backlog
### P1: Slip correlation analysis (multi-pick same game)
### P2: Prediction feedback loop, Batch scan predictions
### P3: SofaScore integration for NWSL
