# ReversePicks - Sports Player Props AI Analytics

## Problem Statement
Soccer player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players via MongoDB cache, and runs a multi-AI consensus prediction pipeline.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Prediction Pipeline**: Multi-AI Consensus (Grok-4-fast-reasoning + Gemini 2.0 Flash + GPT-4o + Claude Sonnet 4) — all 4 run in parallel
- **Follow-up Chat**: Grok-3-mini with web search via tactical.py (user-initiated, separate flow)
- **Auth**: Whop membership verification + email/password login
- **Data**: API-Sports (cached in MongoDB, parallelized fixture fetching)

## Multi-AI Consensus Engine (v3.0 — Feb 2026)
1. **Wave 1** (~1.5s): API-Sports data (7 parallel calls)
2. **Wave 2** (~3s): Parallelized fixture stats + game logs (all concurrent)
3. **AI Consensus** (~35-42s): Grok + Gemini + GPT-4o + Claude run simultaneously with `aio.wait(timeout=40)`
4. **Merge**: 
   - Averaged projectedValue across all responding AIs
   - Recommendation ENFORCED by projectedValue vs line (eliminates contradictions)
   - Text fields (tacticalBreakdown, reasoning, etc.) prioritize Grok, fallback to longest
   - Tactical Breakdown always rebuilt with consensus verdict for consistency
5. **Total**: ~41-48s (well under 60s proxy limit)

## Key API Endpoints
- POST /api/scan-prop — OCR extraction + player resolution
- POST /api/predict — Multi-AI consensus prediction (~42s)
- POST /api/tactical/message — Follow-up chat (Grok-3-mini with web search)
- POST /api/auth/verify-whop, /api/auth/reset-password, /api/picks/save, /api/picks/list

## Completed Work
- iOS-like elite UI overhaul with 3-tab nav (Scan | Tracking | Profile) ✅
- Profile tab with password reset ✅
- User record tracker (HITS/MISS/PUSH/STREAK) ✅
- Lifetime VIP for michael1069_6910@yahoo.com ✅
- Parallelized API-Sports data fetching ✅
- Multi-AI consensus engine with Grok-4-fast-reasoning (Feb 2026) ✅
- Fixed recommendation logic disconnect (projectedValue vs line enforcement) ✅
- Fixed tactical breakdown consistency (always matches consensus verdict) ✅
- Fixed Grok JSON parsing (handles extra trailing text) ✅

## Prioritized Backlog
### P2: Slip correlation, Prediction feedback loop
### P3: Batch scan, SofaScore integration
