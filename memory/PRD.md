# ReversePicks — Product Requirements Document

## Core Product
Sports player prop prediction platform. Users scan prop screenshots, AI extracts details, resolves player/team, and runs a prediction pipeline.

## Legal Compliance
- NO 3rd-party app names or player/team images
- NO explicit AI branding in UI

## Architecture

### Prediction Pipeline — True Unified Engine
```
Grok OCR → Data Fetch → BAYESIAN MATH (anchor) → Grok AI (math-informed) → FUSION → Possession Scaling → Guards → Final
```

1. **Grok Vision OCR** — Extracts player/prop/line (with validation gate)
2. **Data Fetch** — Player game logs, match dominance, opponent stats, odds
3. **Bayesian Engine v2** — 3-layer math projection FIRST (anchor for AI)
4. **Grok 4.20 + GPT-5.2** — AI projection (math-informed via anchor injection)
5. **Bayesian-AI Fusion** — Merges AI + Math (adaptive weights)
6. **Post-Fusion Possession Scaling** — For pass props, scales by expected possession
7. **Guards** — Coin-flip zone detection, tight edge caps
8. **Slip Correlation** — Same-game pick warnings at save time

### Key Safety Systems
- **Coin Flip Guard**: projection within ±3 of line + Bayesian <60% → cap confidence at 52%, badge
- **Possession Scaling**: pass props scaled by match dominance multiplier post-fusion
- **Slip Correlation**: CORRELATED_RISK, OPPOSING_TEAMS_SAME_DIR, CONFLICTING, BOOSTING warnings

### Calibration System
- **REMOVED** per user request. Was dragging projections toward the line.

## Key Features
- Image scanning with OCR validation
- Soccer + Basketball unified prediction pipelines
- True unified engine (Bayesian anchors AI)
- Post-fusion possession scaling for pass-related props
- Coin-flip zone detection with badge
- Slip correlation warnings for same-game picks
- INTEL Tab (aggregate dashboard: hit rates by prop/position/direction)
- Tracking Tab with expandable analysis
- Live game tracking, auto-settlement
- MongoDB fixture caching, Position tracking
- Square/Whop subscription management

## Tech Stack
- Frontend: React.js, Shadcn/UI
- Backend: FastAPI, Python asyncio, MongoDB
- AI: Grok 4.20 (xAI key), GPT-5.2 (Emergent LLM Key)
- Data: API-Sports (Football + Basketball)

## Backlog
- P2: Prediction self-correction feedback loop
- P2: Batch scan predictions
- P3: Frontend/Backend refactoring
- P3: Auth migration to httpOnly cookies
