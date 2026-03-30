# ReversePicks - Sports Player Props AI Analytics

## Problem Statement
A web app for soccer player prop analysis. Users upload screenshots of player props (pass attempts, shots, saves), AI Vision extracts details, resolves the player via cache-first MongoDB lookup, and runs a Gemini 2.5 Flash prediction pipeline to deliver statistical predictions and deep tactical analysis.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **AI Pipeline**: Gemini 2.5 Flash (unified prediction + tactical analysis) — single AI call architecture
- **Auth**: Whop membership verification + email/password login
- **Data**: API-Sports (cached in MongoDB, parallelized fixture fetching)

## 3-Tab Navigation
1. **Scan** — Upload prop screenshot → AI Vision extraction → Player resolution → Unified prediction + tactical breakdown + follow-up chat
2. **Tracking** — Live/History pick tracking with Record Tracker (HIT/MISS/PUSH/WIN%/STREAK)
3. **Profile** — Account info (email, access level, total picks), password reset, app version, logout

## Key Features Implemented
- [x] Cache-first player resolution (MongoDB)
- [x] Combo prop support (multi-stat detection)
- [x] International context awareness (National Team vs Club stats)
- [x] Unified Scan + Tactical pipeline (single API response)
- [x] Push status tracking (amber borders for ties)
- [x] User Record Tracker (HIT/MISS/PUSH ratio, WIN%, streak)
- [x] Profile tab with password reset
- [x] 3-tab iOS-like navigation
- [x] Follow-up chat under predictions (Grok-powered in tactical.py)
- [x] VIP/Lifetime hardcoded emails
- [x] Elite iOS-like UI (Manrope font, glassmorphism, premium dark theme)
- [x] Parallelized API-Sports fixture fetching (5x faster data loading)
- [x] Single Gemini call architecture (replaced Grok from predict pipeline)

## VIP Emails (Lifetime Access)
- josselj001@gmail.com (Owner)
- letwins04@gmail.com
- Quon.qg@gmail.com
- Jesselopezj@hotmail.com
- jaredlee0414@gmail.com
- michael1069_6910@yahoo.com

## AI Pipeline (v2.2 - Gemini-Only Architecture)
### Prediction Pipeline (predict.py)
- **Single AI call**: Gemini 2.5 Flash handles ALL prediction + analysis
- **Data pipeline**: Wave 1 (player/team/opponent stats, H2H, standings, odds) → Wave 2 (parallelized fixture stats + game logs) → Gemini synthesis
- **Parallelized fetching**: All fixture stat and game log API calls run concurrently (was sequential → 5x speedup)
- **Bayesian calibration**: priorMean (recency-weighted), momentumEffect, covariateAdjustment, reversalFlag
- **Position-calibrated ceilings**: GK saves capped by opponent SoT, position-specific limits
- **Confidence bands**: coin flip (<0.3 diff) max 52%, low edge (<0.8) max 62%, strong edge (>=1.5) 70-85%
- **Tactical breakdown**: Assembled from prediction response fields (no additional AI call)
- **Timeouts**: Wave 2 data: 15s, Gemini prediction: 25s

### Follow-up Chat (tactical.py)
- **Grok-4.20-reasoning** with web search for follow-up questions
- Uses prediction context for continuity
- Separate user-initiated call (not subject to predict timeout)

## Key API Endpoints
- POST /api/scan-prop — OCR extraction + player resolution
- POST /api/predict — Unified stats + tactical generation (~40-45s)
- POST /api/tactical/message — Follow-up chat (Grok)
- POST /api/auth/verify-whop — Membership verification
- POST /api/auth/reset-password — Password reset
- POST /api/picks/save — Save to tracking
- POST /api/picks/list — Get user picks

## Prioritized Backlog
### P2 (Medium)
- Slip correlation analysis - Analyze multiple picks for conflicting/boosting patterns
- Prediction self-correction feedback loop - Store outcomes, feed calibration back

### P3 (Low)
- Batch scan predictions - Multiple props from one image
- RapidAPI SofaScore integration (NWSL data)
