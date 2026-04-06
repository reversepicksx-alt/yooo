# ReversePicks — Product Requirements Document

## Core Product
Sports player prop prediction platform. Users scan prop screenshots, AI extracts details, resolves player/team, and runs a prediction pipeline.

## Legal Compliance
- NO 3rd-party app names or player/team images
- NO explicit AI branding in UI

## Architecture

### AI Pipeline (Hybrid Engine)
- **Grok 4.20 (grok-4.1-fast)** — Primary heavy lifting: data digest, pattern mining, position resolution, primary prediction, OCR scanning
- **GPT-5.2** — Lightweight verification safety net (uses Emergent LLM Key)

### Prediction Pipeline (4 Pillars)
1. **Grok 4.20** → Primary AI prediction (data digest + reasoning)
2. **Bayesian Engine v2** → Deterministic 3-layer math (Prior + Momentum + Covariate)
3. **GPT-5.2** → Lightweight sanity verification
4. **Elite Calibration Engine** → 5 post-consensus hard corrections

### Bayesian Engine v2 (bayesian_engine.py)
- **Layer 1: PRIOR** — Season average baseline with sample-size floor precision (n^0.6)
- **Layer 2: MOMENTUM** — Exponentially-weighted recent form (decay: [1.0, 0.82, 0.67, 0.55, 0.45])
- **Layer 3: COVARIATE** — Match context (venue, opponent, dominance) — HARD CAPPED at 25% max weight
- Features: streak detection (OVER_N/UNDER_N), volatility scoring (CV-based), trend consistency bonus, reversal flags
- Guarantees: Prior + Momentum always >= 74% of total weight

### Grok Engine Phases
1. **Pre-Prediction Data Digest**: Grok crunches raw API data into focused intel brief
2. **Auto-Settlement Bot**: Background loop (every 2 min) checks live scores, auto-settles finished picks
3. **Pre-Game Auto-Scout**: Background loop (every 6 hours) pre-fetches fixture data
4. **INTEL Pattern Mining**: Daily analysis of settled picks → calibration patterns
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
- Bayesian Engine v2 with visual layer breakdown on ProjectionCard
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
- AI: Grok 4.20 (xAI user key), GPT-5.2 (Emergent LLM Key)
- Data: API-Sports (Football + Basketball)

## Key Endpoints
- `POST /api/scan-prop` — Vision extraction (Grok → GPT-4o fallback)
- `POST /api/predict` — Soccer prediction (Grok digest → Bayesian → GPT-5.2 verify → Calibration)
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
- P2: Prediction self-correction feedback loop
- P2: Batch scan predictions (multiple props from one image)
- P3: Frontend refactoring (App.js component extraction)
- P3: Backend refactoring (monolithic predict.py)
- P3: Auth migration to httpOnly cookies
