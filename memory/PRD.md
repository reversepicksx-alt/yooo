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

## Multi-AI Consensus Engine (v3.1 — Feb 2026)
1. **Wave 1** (~1.1s): API-Sports data (7 parallel calls)
2. **Wave 2** (~2.5s): Parallelized fixture stats + game logs (up to 15 fixtures, 12 game logs)
3. **AI Consensus** (~40s): Grok + Gemini + GPT-4o + Claude run simultaneously with `aio.wait(timeout=40)`
4. **Merge**:
   - Averaged projectedValue across all responding AIs
   - Recommendation ENFORCED by projectedValue vs line (no contradictions)
   - Text fields prioritize Grok's output, fallback to longest
   - Tactical Breakdown always rebuilt with consensus verdict + rich multi-section format
5. **Total**: ~42-54s (under 60s proxy limit)

## Recent Fixes (Feb 2026)
- Added Grok-4-fast-reasoning as 4th AI in consensus pool ✅
- Fixed recommendation logic disconnect (projectedValue vs line enforcement) ✅
- Fixed tactical breakdown consistency (verdict always matches consensus) ✅
- Fixed Grok JSON parsing (handles trailing text) ✅
- Enhanced PREDICTION_SYSTEM prompt for 3x richer analysis (3000+ char TBs) ✅
- Added match round/stage detection (knockout/elimination awareness) ✅
- Fixed international player name matching with accent stripping ✅
- Increased game log sample size from 8 to 12 ✅
- Increased data payload to AIs (6000+ chars) ✅

## Key API Endpoints
- POST /api/scan-prop — OCR extraction + player resolution
- POST /api/predict — Multi-AI consensus prediction (~45-54s)
- POST /api/tactical/message — Follow-up chat (Grok-3-mini with web search)
- POST /api/auth/verify-whop, /api/auth/reset-password, /api/picks/save, /api/picks/list

## Prioritized Backlog
### P2: Slip correlation, Prediction feedback loop
### P3: Batch scan, SofaScore integration
