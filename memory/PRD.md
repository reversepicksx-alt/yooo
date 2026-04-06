# ReversePicks — Product Requirements Document

## Core Product
Sports player prop prediction platform. Users scan prop screenshots, AI extracts details, resolves player/team, and runs a prediction pipeline.

## Legal Compliance
- NO 3rd-party app names or player/team images
- NO explicit AI branding in UI

## Architecture

### AI Pipeline (Dual-Engine)
- **Grok (grok-3-mini-fast)** — Data backbone: position resolution, pre-prediction digest, auto-settlement, auto-scout, pattern mining, smart scan (primary)
- **GPT-5.2** — Prediction brain: single-AI projection + Elite Calibration Engine v3

### Grok Engine Phases
1. **Pre-Prediction Data Digest**: Grok crunches raw API data into focused intel brief → feeds GPT shorter/smarter prompt
2. **Auto-Settlement Bot**: Background loop (every 2 min) checks live scores, auto-settles finished picks
3. **Pre-Game Auto-Scout**: Background loop (every 6 hours) pre-fetches fixture data for upcoming games
4. **INTEL Pattern Mining**: Daily analysis of settled picks → finds calibration patterns → stored in `calibration_insights`
5. **Smart Scan Processing**: Grok Vision handles OCR first, GPT-4o fallback

### Background Tasks (server startup)
- Cache seeding (API-Football, basketball)
- Square payment sync
- Auto-backfill positions (Grok-powered)
- Auto-settlement loop
- Auto-scout loop
- Pattern mining loop

## Key Features Implemented
- Image scanning (Grok primary + GPT-4o fallback)
- Soccer + Basketball prediction pipelines
- Elite Calibration Engine v3 (5 mathematical rules)
- INTEL Tab (spreadsheet + calibration breakdown + smart filters)
- Tracking Tab (expandable analysis cards)
- American odds display
- MongoDB fixture stat caching
- Position tracking (Grok-powered auto-backfill)
- Sport cross-contamination guard
- Live game tracking (30s polling)
- Square/Whop subscription management

## Tech Stack
- Frontend: React.js, Shadcn/UI
- Backend: FastAPI, Python asyncio, MongoDB
- AI: Grok (xAI), GPT-5.2 (OpenAI via Emergent)
- Data: API-Sports (Football + Basketball)

## Key Endpoints
- `POST /api/scan-prop` — Vision extraction (Grok → GPT-4o fallback)
- `POST /api/predict` — Soccer prediction (Grok digest + GPT-5.2 + calibration)
- `POST /api/basketball/predict` — Basketball prediction
- `GET /api/intel/sheet` — INTEL spreadsheet data
- `POST /api/picks/live-update` — Live game tracking + auto-settlement

## DB Collections
- `picks` — Saved predictions (position, role, sport tracked)
- `predictions` / `basketball_predictions` — Full prediction responses
- `player_positions` — Grok-resolved position cache
- `fixture_player_cache` — MongoDB cache for fixture stats
- `calibration_insights` — Grok pattern mining results
- `sessions`, `subscriptions`, `settings`

## Backlog
- P1: Slip correlation analysis
- P2: Route prediction APIs through MongoDB cache (TTL-based)
- P3: Frontend refactoring (App.js component extraction)
- P3: Backend refactoring (monolithic predict.py)
- P3: Auth migration to httpOnly cookies
