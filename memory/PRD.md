# ReversePicks — Product Requirements Document

## Core Product
Sports player prop prediction platform. Users scan prop screenshots, AI extracts details, resolves player/team, and runs a prediction pipeline.

## Legal Compliance
- NO 3rd-party app names or player/team images
- NO explicit AI branding in UI

## Architecture

### Prediction Pipeline — True Unified Engine (6 Stages)
```
Grok OCR → Data Fetch → BAYESIAN MATH (anchor) → Grok AI (math-informed) → FUSION → Guards → Calibration → Final
```

1. **Grok Vision OCR** — Extracts player/prop/line from screenshot (with validation gate)
2. **Data Fetch** — Player game logs, match dominance, opponent stats, odds
3. **Bayesian Engine v2** — Computes 3-layer math projection FIRST
4. **Bayesian Anchor Injection** — Math result injected INTO Grok's prompt so AI reasoning aligns with data
5. **Grok 4.20 (primary) + GPT-5.2 (verify)** — AI projection now MATH-INFORMED
6. **Bayesian-AI Fusion** — Merges AI + Math into one number (adaptive weights based on agreement/confidence)
7. **Elite Calibration Engine v3** — 5 post-fusion corrections (auto-tuning market blend)
8. **Final Guards** — Edge detection, confidence normalization

### Why This Architecture
- **Before**: Grok generated projections BLIND to the math → AI reasoning could contradict its own data (e.g., "season avg is 41.91, under-rate 63.6%" but projects 51.3 OVER)
- **Now**: Bayesian math computes FIRST and the result is injected into Grok's prompt as `[MATHEMATICAL ENGINE — DO NOT IGNORE]`. Grok's reasoning naturally aligns with the data because it HAS the data. The fusion after Grok adds a safety net, and calibration fine-tunes the final number.

### Bayesian-AI Fusion Weights
- AI & Bayesian AGREE: 60% AI / 40% Bayesian
- DISAGREE + Bayesian >70% confident: 50/50
- DISAGREE + Bayesian 55-70%: 60% AI / 40% Bayesian
- DISAGREE + Bayesian <55%: 70% AI / 30% Bayesian
- Produces `fusionApplied` metadata for transparency

### Bayesian Engine v2 (bayesian_engine.py)
- Layer 1: PRIOR — Season baseline (n^0.6 floor precision)
- Layer 2: MOMENTUM — Exponentially-weighted recent form
- Layer 3: COVARIATE — Match context (CAPPED at 25%)
- Features: streak detection, volatility scoring, trend bonus, reversal flags

### OCR Validation (scan.py)
- Validates extractions before proceeding
- Catches: misread names, impossible lines, unknown prop types

### Elite Calibration Engine v3 (calibration.py)
1. Historical Error Correction
2. Market Line Blending (AUTO-TUNED from settled picks)
3. Recommendation Flip Guard
4. Confidence Recalibration
5. Edge Threshold Classification

## Key Features
- Image scanning with OCR validation
- Soccer + Basketball unified prediction pipelines
- True unified engine (Bayesian anchors AI, no contradictions)
- Visual Bayesian Engine breakdown on ProjectionCard
- INTEL Tab, Tracking Tab, Live game tracking
- MongoDB fixture caching, Position tracking
- Square/Whop subscription management

## Tech Stack
- Frontend: React.js, Shadcn/UI
- Backend: FastAPI, Python asyncio, MongoDB
- AI: Grok 4.20 (xAI key), GPT-5.2 (Emergent LLM Key)
- Data: API-Sports (Football + Basketball)

## Key Endpoints
- `POST /api/scan-prop` — Vision extraction with validation
- `POST /api/predict` — Soccer unified pipeline
- `POST /api/basketball/predict` — Basketball unified pipeline
- `GET /api/intel/sheet` — INTEL spreadsheet

## Backlog
- P1: Slip correlation analysis
- P2: Route APIs through MongoDB cache (TTL)
- P2: Prediction self-correction feedback loop
- P2: Batch scan predictions
- P3: Frontend/Backend refactoring
- P3: Auth migration to httpOnly cookies
