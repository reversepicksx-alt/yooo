# ReversePicks - Multi-Sport Player Props AI Analytics

## Problem Statement
Multi-sport player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players/teams, and runs a multi-AI consensus prediction pipeline. Supports Soccer and Basketball (NBA).

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Multi-Sport**: Sport selector in header (Soccer/Basketball). Separate pipelines per sport.
- **Prediction Pipeline**: 5-AI Consensus + Synthesis Engine
  - AIs: Grok-4-fast-reasoning, Gemini 2.0 Flash, Gemini 2.5 Flash, GPT-4o, Claude Sonnet 4
  - Uses `litellm.acompletion` for TRUE async parallelism
  - First-3-wins pattern with 48s absolute deadline
  - Gemini synthesis step for tactical breakdown

## Sport-Specific Details
### Soccer
- Data: API-Sports Football (player + team level)
- Routes: `/api/predict`, `/api/scan-prop`
- Props: pass_attempts, shots, shots_on_target, tackles, key_passes, saves, etc.

### Basketball (NBA)
- Data: API-Sports Basketball v1
- Routes: `/api/basketball/predict`, `/api/basketball/search-teams`
- Team Search: Filtered by NBA league (league=12) — no more youth/foreign team matches
- Player Lookup: `/players?search=NAME&team=TEAM_ID&season=SEASON` → finds player ID
- Game Stats: `/games/statistics/players?player=ID&season=SEASON` → ALL game stats in ONE API call
- Available Stats: points, rebounds (total), assists, fgm/fga, tpm/tpa, ftm/fta, minutes
- NOT available per-player: steals, blocks, turnovers (only at team level)
- Props: points, rebounds, assists, pts_reb_ast, three_pointers, fgm, ftm

## Key Files
- `/app/backend/routes/predict.py` — Soccer prediction
- `/app/backend/routes/basketball_predict.py` — Basketball prediction
- `/app/backend/basketball_utils.py` — Basketball API helpers (NBA-filtered)
- `/app/backend/routes/scan.py` — Sport-aware OCR scan
- `/app/frontend/src/App.js` — UI with sport selector
- `/app/frontend/src/api.js` — API service wrappers

## Completed Work
- iOS-like elite UI with 3-tab nav (Scan | Tracking | Profile)
- Multi-sport support (Soccer + Basketball) with header toggle
- Sport-aware scan OCR
- **Basketball prediction pipeline v2** (fixed):
  - NBA-only team search (league=12 filter) — fixes "Helios Suns mladi" bug
  - Player ID lookup via /players endpoint
  - Single-call season stats (91+ games in ONE call)
  - Fixed rebounds parsing (dict.total)
  - Removed unavailable steals/blocks/turnovers props
  - 27-62 game logs per player, ~30s execution
- 5-AI consensus + synthesis engine for both sports
- Venue-prioritized game logs
- Profile tab, password reset, user record tracker
- Lifetime VIP system, Whop auth

## Prioritized Backlog
### P2: Slip correlation, Prediction feedback loop, Batch scan
### P3: SofaScore integration
