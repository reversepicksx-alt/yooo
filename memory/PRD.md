# ReversePicks - Multi-Sport Player Props AI Analytics

## Problem Statement
Multi-sport player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players/teams, and runs a multi-AI consensus prediction pipeline. Supports Soccer and Basketball (NBA).

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Multi-Sport**: Sport selector in header (Soccer/Basketball). Completely separate logic per sport.
- **Prediction Pipeline**: 5-AI Consensus + Synthesis Engine (same pattern for both sports)
  - AIs: Grok-4-fast-reasoning, Gemini 2.0 Flash, Gemini 2.5 Flash, GPT-4o, Claude Sonnet 4
  - Uses `litellm.acompletion` for TRUE async parallelism
  - First-3-wins pattern with 48s absolute deadline
  - Gemini synthesis step for tactical breakdown
- **Scan OCR**: Sport-aware — detects basketball/soccer props from screenshots
- **Auth**: Whop membership + email/password login

## Sport-Specific Details
### Soccer
- Data: API-Sports Football (player + team level)
- Routes: `/api/predict`, `/api/scan-prop`
- Props: pass_attempts, shots, shots_on_target, tackles, key_passes, saves, interceptions, blocks, dribbles, fouls_drawn

### Basketball (NBA)
- Data: API-Sports Basketball v1 (individual player stats via `games/statistics/players`)
- Routes: `/api/basketball/predict`, `/api/basketball/search-teams`
- Props: points, rebounds, assists, pts_reb_ast, three_pointers, steals, blocks, turnovers, fgm, ftm
- Player analysis: Real game logs fetched per-game (Points, Rebounds, Assists, 3PM, Steals, Blocks, etc.)
- Venue-filtered stats with home/away splits

## Key Files
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
- Basketball prediction pipeline (5-AI consensus, ~29s avg)
- Basketball team search + resolution
- 5-AI consensus + synthesis engine for both sports
- Venue-prioritized game logs (15-20 venue-matched samples for Soccer)
- Profile tab with password reset
- User record tracker
- Lifetime VIP system
- Baseball integration built and removed (API lacked individual stats)

## Prioritized Backlog
### P2: Slip correlation, Prediction feedback loop, Batch scan
### P3: SofaScore integration
