# ReversePicks — Product Requirements Document

## Core Product
Sports player prop prediction platform. Users scan prop screenshots, AI extracts details, resolves player/team, and runs a prediction pipeline.

## Legal Compliance
- NO 3rd-party app names or player/team images
- NO explicit AI branding in UI

## Architecture

### Prediction Pipeline — Unified Engine (6 Stages)
```
Grok Vision OCR → Grok Data Digest → BAYESIAN-AI FUSION → Guards → Elite Calibration → Final Output
```

1. **Grok 4.20 (grok-4.1-fast)** — Primary heavy lifting: data digest, pattern mining, position resolution, primary AI projection, OCR scanning
2. **Bayesian Engine v2** → Deterministic 3-layer math (Prior + Momentum + Covariate)
3. **BAYESIAN-AI FUSION** → Merges Grok's AI projection with Bayesian's mathematical posterior into ONE unified number BEFORE calibration
4. **GPT-5.2** → Lightweight verification safety net (uses Emergent LLM Key)
5. **Elite Calibration Engine v3** → 5 post-consensus hard corrections (auto-tuning market blend)
6. **Final Guards** → Edge detection, confidence normalization, recommendation lock

### Bayesian-AI Fusion (predict.py / basketball_predict.py)
- Adaptive fusion weights based on agreement + Bayesian confidence:
  - AI & Bayesian AGREE: 60% AI / 40% Bayesian
  - DISAGREE + Bayesian >70% confident: 50/50 (equal weight)
  - DISAGREE + Bayesian 55-70%: 60% AI / 40% Bayesian
  - DISAGREE + Bayesian <55%: 70% AI / 30% Bayesian
- Produces `fusionApplied` metadata for full transparency
- Eliminates AI/Math contradictions — one voice, one answer

### Bayesian Engine v2 (bayesian_engine.py)
- **Layer 1: PRIOR** — Season average baseline with sample-size floor precision (n^0.6)
- **Layer 2: MOMENTUM** — Exponentially-weighted recent form (decay: [1.0, 0.82, 0.67, 0.55, 0.45])
- **Layer 3: COVARIATE** — Match context (venue, opponent, dominance) — HARD CAPPED at 25% max weight
- Features: streak detection (OVER_N/UNDER_N), volatility scoring (CV-based), trend consistency bonus, reversal flags
- Guarantees: Prior + Momentum always >= 74% of total weight

### OCR Validation (scan.py)
- Validates Grok Vision extractions before proceeding
- Checks: player name sanity, line validity, prop type mapping
- Auto-falls to GPT-4o re-extraction if validation fails

### Elite Calibration Engine v3 (calibration.py)
1. **Historical Error Correction** — Adjusts projections based on historical over/under-projection
2. **Market Line Blending** — AUTO-TUNED from settled picks (grid search MAE minimization, 2h cache)
3. **Recommendation Flip Guard** — Flips direction if historically losing
4. **Confidence Recalibration** — Maps AI confidence to actual hit rates
5. **Edge Threshold** — STRONG/LEAN/LOW classification

### Background Tasks (server startup)
- Cache seeding (API-Football, basketball)
- Square payment sync
- Auto-backfill positions (Grok-powered)
- Auto-settlement loop (every 2 min)
- Auto-scout loop (every 6 hours)
- Pattern mining loop

## Key Features Implemented
- Image scanning with OCR validation (Grok primary → GPT-4o fallback)
- Soccer + Basketball prediction pipelines
- Bayesian-AI Fusion — one unified engine (no more contradictions)
- Bayesian Engine v2 with visual layer breakdown on ProjectionCard
- Elite Calibration Engine v3 with auto-tuning market blend
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
- `POST /api/scan-prop` — Vision extraction with OCR validation
- `POST /api/predict` — Soccer: Grok → Bayesian Fusion → Calibration
- `POST /api/basketball/predict` — Basketball: same unified pipeline
- `GET /api/intel/sheet` — INTEL spreadsheet data
- `POST /api/picks/live-update` — Live game tracking + auto-settlement

## DB Collections
- `picks` — Saved predictions (position, role, sport tracked)
- `predictions` / `basketball_predictions` — Full prediction responses
- `player_positions` — Grok-resolved position cache
- `fixture_player_cache` — MongoDB cache for fixture stats
- `calibration_insights` — Grok pattern mining results

## Backlog
- P1: Slip correlation analysis
- P2: Route prediction APIs through MongoDB cache (TTL-based)
- P2: Prediction self-correction feedback loop
- P2: Batch scan predictions (multiple props from one image)
- P3: Frontend refactoring (App.js component extraction)
- P3: Backend refactoring (monolithic predict.py)
- P3: Auth migration to httpOnly cookies
