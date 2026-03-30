# ReversePicks - Sports Player Props AI Analytics

## Problem Statement
Soccer player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players via MongoDB cache, and runs a multi-AI consensus prediction pipeline.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Prediction Pipeline**: 5-AI Consensus + Synthesis Engine
  - AIs: Grok-4-fast-reasoning, Gemini 2.0 Flash, Gemini 2.5 Flash, GPT-4o, Claude Sonnet 4
  - Uses `litellm.acompletion` for TRUE async parallelism (not blocking LlmChat)
  - First-3-wins pattern: takes first 3 valid responses, cancels stragglers
  - Gemini synthesis step: combines all AI analyses into one rich 1500-char breakdown
- **Follow-up Chat**: Grok-3-mini with web search via tactical.py
- **Auth**: Whop membership verification + email/password login
- **Data**: API-Sports (cached in MongoDB, parallelized fixture fetching)

## Performance (v4.1 — Feb 2026)
- Wave 1 (API-Sports): ~1.0s
- Wave 2 (Fixture stats + 25-game logs): ~4s
- AI Consensus (first 3 of 5): ~15-19s
- Synthesis (Gemini): ~3s
- **Total: 22-30s** (63% under 60s proxy limit)
- Game samples: 14-21 total (7+ venue-filtered)

## Key API Endpoints
- POST /api/scan-prop — OCR extraction + player resolution
- POST /api/predict — 5-AI consensus prediction (~25s)
- POST /api/tactical/message — Follow-up chat (Grok-3-mini)
- POST /api/auth/verify-whop, /api/auth/reset-password, /api/picks/save, /api/picks/list

## Completed Work
- iOS-like elite UI overhaul with 3-tab nav (Scan | Tracking | Profile) ✅
- Profile tab with password reset ✅
- User record tracker (HITS/MISS/PUSH/STREAK) ✅
- Lifetime VIP for michael1069_6910@yahoo.com ✅
- 5-AI consensus + synthesis engine ✅
- TRUE async parallelism via litellm.acompletion ✅
- Deep game log lookback (25 fixtures, 7+ venue-filtered samples) ✅
- Grok+Gemini synthesis for rich tactical breakdowns ✅
- International player accent-stripped name matching ✅
- Match round/stage detection (knockout awareness) ✅

## Prioritized Backlog
### P2: Slip correlation, Prediction feedback loop
### P3: Batch scan, SofaScore integration
