# ReversePicks - Soccer Prediction Platform PRD

## Original Problem Statement
Remake of ReversePicks (originally Gemini version) - a soccer player prop prediction webapp using API-Sports for data gathering and AI for elite predictions. Dark mode UI. Whop-based auth for subscription management.

## Architecture
- **Backend**: Python FastAPI (port 8001)
- **Frontend**: React (port 3000) with custom dark CSS
- **Database**: MongoDB (local)
- **AI Pipeline**: Dual AI — Grok-4.20-reasoning (tactical analysis + deep web search) → Gemini 2.5 Flash (final JSON calibration). Code-built data digest replaces GPT-4o-mini.
- **Vision AI**: GPT-4o via Emergent LLM Key (Scan tab image extraction)
- **Data Source**: API-Sports v3 (api-sports.io)
- **Auth**: Whop API (subscription verification) + bcrypt password hashing + session tokens

## What's Been Implemented
- [x] Full backend with FastAPI + Dual AI integration (Grok-4.20-reasoning → Gemini 2.5 Flash)
- [x] API-Sports proxy with rate limiter (semaphore + retry logic)
- [x] Player search: multi-season parallel merge (newest season wins for transfers)
- [x] Combo/Stack prediction: async background job + polling, 2-player combined analysis
- [x] Matchup Overview locked to real API data (no AI fluctuation)
- [x] Women's league pronoun detection (NWSL)
- [x] GK Saves Formula: last 5-7 games only for save rate
- [x] Manual pick correction feature (pencil icon on settled cards)
- [x] Redesigned tracking cards: NOW/LINE/PACE/HIT% grid, progress bar, HIT/MISS badges
- [x] Interactive Guide tab: 8-step walkthrough + FAQ accordion
- [x] Data quality indicator: warns when API data has gaps
- [x] Live tracking with 2-min auto-refresh
- [x] Push notifications (toast + bell)
- [x] Pick of the Day
- [x] Whop auth with owner bypass, lifetime subs, premium subs
- [x] Grok deep web search for stat verification
- [x] Fixed Guide tab layout bug (was rendered outside <main> container) — 2026-03-29
- [x] Fixed NoneType division error in prediction pipeline (API returns null for minutes) — 2026-03-29
- [x] **NEW: Scan Tab** — Image-to-Prediction via GPT-4o vision — 2026-03-29
  - Upload PrizePicks screenshots
  - AI extracts player name, prop type, line, opponent, league
  - Auto-resolves players via API-Sports (with last-name fallback search)
  - Resolves opponent teams
  - Inline prediction results with confidence, recommendation, sharp summary
  - "View Full Analysis" bridges to Predict tab

## Lifetime VIP Emails
- josselj001@gmail.com (Owner)
- faron2allen@gmail.com, jossel0701@gmail.com, brayanfgaleas@icloud.com
- odr310@gmail.com, joseharo197@gmail.com, rijulgauchan1@gmail.com
- gordo0210@icloud.com, brianavina23@gmail.com, andrewfitz97@yahoo.com
- jose108798@gmail.com

## API Keys (in .env)
- API-Sports key, Emergent LLM Key (Gemini + GPT-4o Vision), Whop API key + Company ID, xAI (Grok) API key

## Prioritized Backlog
### P2 (Medium)
- Slip correlation analysis
- User Record Tracker (HIT/MISS ratio, ROI, streak)
- Prediction self-correction feedback loop

### Future
- Codebase refactor (server.py 2700+ lines, App.js 2500+ lines)
- SofaScore RapidAPI integration (if user subscribes to SofaSport API)
- Combo pick saving to tracking tab
- Video tutorial embed option for Guide tab
- Scan tab: camera capture (mobile), batch predictions, save scanned picks to tracking
