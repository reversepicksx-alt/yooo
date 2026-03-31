# ReversePicks - Multi-Sport Player Props AI Analytics

## Problem Statement
Multi-sport player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players/teams, and runs a multi-AI consensus prediction pipeline. Supports Soccer and Baseball (MLB).

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Multi-Sport**: Sport selector in header (Soccer/Baseball). Completely separate logic per sport.
- **Prediction Pipeline**: 5-AI Consensus + Synthesis Engine (same pattern for both sports)
  - AIs: Grok-4-fast-reasoning, Gemini 2.0 Flash, Gemini 2.5 Flash, GPT-4o, Claude Sonnet 4
  - Uses `litellm.acompletion` for TRUE async parallelism
  - First-3-wins pattern
  - Gemini synthesis step
- **Scan OCR**: Sport-aware — detects baseball/soccer props from screenshots
- **Auth**: Whop membership + email/password login

## Sport-Specific Details
### Soccer
- Data: API-Sports Football (player + team level)
- Routes: `/api/predict`, `/api/scan-prop`
- Props: pass_attempts, shots, shots_on_target, tackles, key_passes, saves, interceptions, blocks, dribbles, fouls_drawn

### Baseball (MLB)
- Data: API-Sports Baseball v1 (team-level only — no individual player stats API)
- Routes: `/api/baseball/predict`, `/api/baseball/search-teams`
- Props: hits, home_runs, rbis, runs, strikeouts, stolen_bases, walks, total_bases, singles, doubles, triples, pitcher_strikeouts, earned_runs, hits_allowed, outs_recorded
- Player analysis: AI knowledge + team context from API

## Key Files
- `/app/backend/routes/predict.py` — Soccer prediction
- `/app/backend/routes/baseball_predict.py` — Baseball prediction
- `/app/backend/baseball_utils.py` — Baseball API helpers
- `/app/backend/routes/scan.py` — Sport-aware OCR scan
- `/app/frontend/src/App.js` — UI with sport selector

## Completed Work
- iOS-like elite UI with 3-tab nav (Scan | Tracking | Profile) ✅
- Multi-sport support (Soccer + Baseball) with header toggle ✅
- Sport-aware scan OCR ✅
- Baseball prediction pipeline (5-AI consensus) ✅
- Baseball team search + resolution ✅
- 5-AI consensus + synthesis engine for both sports ✅
- Venue-prioritized game logs (15-20 venue-matched samples) ✅
- Profile tab with password reset ✅
- User record tracker ✅
- Lifetime VIP for michael1069_6910@yahoo.com ✅

## Prioritized Backlog
### P2: Slip correlation, Prediction feedback loop
### P3: Batch scan, SofaScore integration
