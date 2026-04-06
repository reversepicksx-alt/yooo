# ReversePicks — Product Requirements Document

## Core Product
Sports player prop prediction platform. Users scan prop screenshots, AI extracts details, resolves player/team, and runs a prediction pipeline.

## Legal Compliance
- NO 3rd-party app names or player/team images
- NO explicit AI branding in UI

## Architecture

### Prediction Pipeline — True Unified Engine
```
Grok OCR → Data Fetch → BAYESIAN MATH (anchor) → Grok AI (math-informed) → FUSION → Guards → Final
```

1. **Grok Vision OCR** — Extracts player/prop/line from screenshot (with validation gate)
2. **Data Fetch** — Player game logs, match dominance, opponent stats, odds
3. **Bayesian Engine v2** — Computes 3-layer math projection FIRST
4. **Bayesian Anchor** — Math result injected INTO Grok's prompt so AI reasoning aligns
5. **Grok 4.20 + GPT-5.2** — AI projection now math-informed
6. **Bayesian-AI Fusion** — Merges AI + Math into one number
7. **Post-Fusion Guards** — Coin-flip zone detection, tight edge guards

### Bayesian-AI Fusion Weights
- AGREE: 60% AI / 40% Bayesian
- DISAGREE + strong (>70%): 50/50
- DISAGREE + moderate (55-70%): 60/40
- DISAGREE + weak (<55%): 70/30

### Post-Fusion Guards
- **Tight Edge**: projection within ±1 of line → cap confidence at 58%
- **Coin Flip Zone**: projection within ±3 of line AND Bayesian confidence <60% → cap at 52%, badge "COIN FLIP"

### Slip Correlation System (picks.py)
- Detects same-game picks when saving
- Warning types:
  - CORRELATED_RISK (HIGH): All picks same direction in same game
  - OPPOSING_TEAMS_SAME_DIR (HIGH): Both teams' players same direction on pass props
  - CONFLICTING (MEDIUM): Same team, opposite directions
  - BOOSTING (INFO): Same team, same direction

### Bayesian Engine v2 (bayesian_engine.py)
- Layer 1: PRIOR — Season baseline (n^0.6 floor precision)
- Layer 2: MOMENTUM — Exponentially-weighted recent form
- Layer 3: COVARIATE — Match context (CAPPED at 25%)
- Features: streak detection, volatility scoring, trend bonus, reversal flags

### OCR Validation (scan.py)
- Validates extractions before proceeding
- Catches: misread names, impossible lines, unknown prop types

### Calibration System
- **REMOVED** per user request. Market blend was dragging projections too close to the line.

## Key Features
- Image scanning with OCR validation
- Soccer + Basketball unified prediction pipelines
- True unified engine (Bayesian anchors AI, no contradictions)
- Coin-flip zone detection with visual badge
- Slip correlation warnings for same-game picks
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
- `POST /api/picks/save` — Save pick + correlation analysis
- `GET /api/intel/sheet` — INTEL spreadsheet

## Backlog
- P2: Prediction self-correction feedback loop
- P2: Batch scan predictions
- P3: Frontend/Backend refactoring
- P3: Auth migration to httpOnly cookies
