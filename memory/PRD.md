# ReversePicks - Sports Player Props AI Analytics

## Problem Statement
Soccer player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players via MongoDB cache, and runs a multi-AI consensus prediction pipeline.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Prediction Pipeline**: 5-AI Consensus Engine (Grok-4-fast-reasoning + Gemini 2.0 Flash + Gemini 2.5 Flash + GPT-4o + Claude Sonnet 4) — first 3 valid responses win
- **Key Fix**: Uses `litellm.acompletion` for TRUE async parallelism (LlmChat.send_message uses sync litellm.completion which blocks event loop)
- **Follow-up Chat**: Grok-3-mini with web search via tactical.py
- **Auth**: Whop membership verification + email/password login
- **Data**: API-Sports (cached in MongoDB, parallelized fixture fetching)

## 5-AI Consensus Engine (v4.0 — Feb 2026)
1. **Wave 1** (~1.1s): API-Sports parallel calls
2. **Wave 2** (~2.5s): Fixture stats + game logs (12 fixtures, 10 logs max)
3. **AI Consensus** (~25-28s): 5 AIs run TRULY parallel via litellm.acompletion. First 3 valid responses accepted, stragglers cancelled.
4. **Total**: ~28-32s (50% under 60s proxy limit)

## Key API Endpoints
- POST /api/scan-prop — OCR extraction + player resolution
- POST /api/predict — 5-AI consensus prediction (~30s)
- POST /api/tactical/message — Follow-up chat (Grok-3-mini)
- POST /api/auth/verify-whop, /api/auth/reset-password, /api/picks/save, /api/picks/list

## Completed Work
- iOS-like elite UI overhaul with 3-tab nav (Scan | Tracking | Profile) ✅
- Profile tab with password reset ✅
- User record tracker (HITS/MISS/PUSH/STREAK) ✅
- Lifetime VIP for michael1069_6910@yahoo.com ✅
- 5-AI consensus engine with first-3-wins pattern (Feb 2026) ✅
- TRUE async parallelism via litellm.acompletion ✅
- Recommendation logic enforced (projectedValue vs line) ✅
- Tactical breakdown consistency (verdict matches consensus) ✅
- Match round/stage detection (knockout awareness) ✅
- International player name matching (accent stripping) ✅

## Prioritized Backlog
### P2: Slip correlation, Prediction feedback loop
### P3: Batch scan, SofaScore integration
