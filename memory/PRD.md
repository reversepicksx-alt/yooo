# ReversePicks - Multi-Sport Player Props AI Analytics

## Problem Statement
Multi-sport player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players/teams, and runs a multi-AI consensus prediction pipeline. Supports Soccer, NBA, and WNBA.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Multi-Sport**: Sport selector in header (Soccer/Basketball). Basketball covers both NBA and WNBA.
- **Data Cache**: All leagues, teams, players stored in MongoDB for instant lookups
  - Soccer: via `cache.py` — all supported leagues, teams, squads, national teams
  - Basketball: via `basketball_cache.py` — NBA (31 teams, 744 players) + WNBA (15 teams, 253 players)
  - Auto-refreshes every 24 hours
- **Prediction Pipeline**: 5-AI Consensus + Synthesis Engine
  - AIs: Grok-4-fast-reasoning, Gemini 2.0 Flash, Gemini 2.5 Flash, GPT-4o, Claude Sonnet 4
  - `litellm.acompletion` for TRUE async parallelism, first-3-wins with 48s deadline
  - Gemini synthesis step for tactical breakdown

## Data Cache System
### Soccer Cache (cache.py)
- Collections: `cache_leagues`, `cache_teams`, `cache_players`, `cache_national`, `cache_transfers`, `cache_meta`
- Leagues synced: Premier League, La Liga, Serie A, Bundesliga, Ligue 1, MLS, etc.
- Includes transfer detection

### Basketball Cache (basketball_cache.py)
- Collections: `bball_cache_leagues`, `bball_cache_teams`, `bball_cache_players`, `bball_cache_meta`
- NBA (league=12): 31 teams, 744 players
- WNBA (league=13): 15 teams, 253 players
- Handles season format differences: NBA "2025-2026" vs WNBA "2025"
- WNBA squad sync falls back to previous season when current hasn't started
- Lookup functions: `get_bball_team_by_name()`, `get_bball_player_by_name()`, `search_bball_teams()`
- Player names stored as both "LastName FirstName" (API format) and reversed for matching

## Sport-Specific Details
### Soccer
- Data: API-Sports Football v3
- Routes: `/api/predict`, `/api/scan-prop`
- Props: pass_attempts, shots, shots_on_target, tackles, key_passes, saves, etc.

### Basketball (NBA + WNBA)
- Data: API-Sports Basketball v1
- Routes: `/api/basketball/predict`, `/api/basketball/search-teams`
- Player lookup: Cache (instant) → live API fallback
- Game stats: `/games/statistics/players?player=ID&season=SEASON` (single call, 60+ game logs)
- Available stats per player: points, rebounds, assists, fgm/fga, tpm/tpa, ftm/fta, minutes
- Props: points, rebounds, assists, pts_reb_ast, three_pointers, fgm, ftm

## Key Files
- `/app/backend/cache.py` — Soccer data cache
- `/app/backend/basketball_cache.py` — Basketball (NBA+WNBA) data cache
- `/app/backend/routes/predict.py` — Soccer prediction
- `/app/backend/routes/basketball_predict.py` — Basketball prediction
- `/app/backend/basketball_utils.py` — Basketball API helpers
- `/app/backend/routes/scan.py` — Sport-aware OCR scan
- `/app/frontend/src/App.js` — UI with sport selector
- `/app/frontend/src/api.js` — API service wrappers

## Completed Work
- iOS-like elite UI with 3-tab nav (Scan | Tracking | Profile)
- Multi-sport support (Soccer + Basketball) with header toggle
- Sport-aware scan OCR
- Basketball prediction pipeline with cached player lookups (~20s execution)
- **Basketball data cache**: 46 teams, 997 players (NBA + WNBA) in MongoDB
- NBA-only team search (league=12 filter)
- Single-call season stats (60+ game logs per player)
- Fixed rebounds parsing, removed unavailable props
- 5-AI consensus + synthesis engine for both sports
- Venue-prioritized game logs
- Profile tab, password reset, user record tracker
- Lifetime VIP system, Whop auth

## Prioritized Backlog
### P2: Slip correlation, Prediction feedback loop, Batch scan
### P3: SofaScore integration
