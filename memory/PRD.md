# ReversePicks - Sports Player Props AI Analytics

## Problem Statement
Soccer player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players via MongoDB cache, and runs a multi-AI consensus prediction pipeline.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Prediction Pipeline**: Multi-AI Consensus (Gemini 2.0 Flash + GPT-4o + Claude Sonnet 4) — all 3 run in parallel
- **Follow-up Chat**: Grok-4.20-reasoning with web search (user-initiated, separate flow)
- **Auth**: Whop membership verification + email/password login
- **Data**: API-Sports (cached in MongoDB, parallelized fixture fetching)

## Multi-AI Consensus Engine (v2.3)
1. **Wave 1** (~1.5s): API-Sports data (7 parallel calls)
2. **Wave 2** (~3s): Parallelized fixture stats + game logs (all concurrent)
3. **AI Consensus** (~30-40s): Gemini + GPT-4o + Claude run simultaneously with `aio.wait(timeout=45)`
4. **Merge**: Weighted avg of projections, majority vote on recommendation, longest analysis text wins
5. **Total**: ~35-45s (well under 60s proxy limit)

## Key API Endpoints
- POST /api/scan-prop — OCR extraction + player resolution
- POST /api/predict — Multi-AI consensus prediction (~40s)
- POST /api/tactical/message — Follow-up chat (Grok-4.20 with web search)
- POST /api/auth/verify-whop, /api/auth/reset-password, /api/picks/save, /api/picks/list

## Prioritized Backlog
### P2: Slip correlation, Prediction feedback loop
### P3: Batch scan, SofaScore integration
