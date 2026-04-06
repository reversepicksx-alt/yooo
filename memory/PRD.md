# ReversePicks — Product Requirements Document

## Core Product
Soccer-only player prop prediction platform. Users scan prop screenshots, AI extracts details, resolves player/team, and runs a prediction pipeline.

## Legal Compliance
- NO 3rd-party app names or player/team images
- NO explicit AI branding in UI

## Architecture

### Prediction Pipeline — True Unified Engine
```
Grok OCR → Data Fetch → BAYESIAN MATH (anchor) → Grok AI (math-informed) → FUSION → Tempo Scaling → Possession Scaling → Favorite Dampening → Guards → Final
```

1. **Grok Vision OCR** — Extracts player/prop/line (with validation gate)
2. **Data Fetch** — Player game logs, match dominance, opponent stats, odds
3. **Game Tempo Estimation** — Calculates expected total goals, classifies high/normal/low tempo
4. **Heavy Favorite Detection** — Flags teams with odds < 1.60 for pass dampening
5. **Bayesian Engine v2** — 3-layer math projection FIRST (anchor for AI)
6. **Grok 4.20 + GPT-5.2** — AI projection (math-informed via anchor injection, tempo-aware, favorite-aware)
7. **Bayesian-AI Fusion** — Merges AI + Math (adaptive weights)
8. **Post-Fusion Tempo Scaling** — High-tempo games boost ALL players' pass volumes
9. **Post-Fusion Possession Scaling** — For pass props, scales by expected possession
10. **Post-Fusion Favorite Dampening** — Reduces OVER pass projections for heavy favorites
11. **Guards** — Coin-flip zone detection, tight edge caps, UNDER skew penalty
12. **Slip Correlation** — Same-game pick warnings + POSSESSION_CONTRADICTION detection

### Key Safety Systems
- **Coin Flip Guard**: projection within +/-3 of line + Bayesian <60% → cap confidence at 52%, badge
- **Possession Scaling**: pass props scaled by match dominance multiplier post-fusion
- **Game Tempo Estimation**: expected total goals >= 3.2 = high tempo (+4-8% pass boost), <= 1.8 = low tempo (-3-6% reduction)
- **Favorite Dampening**: team odds < 1.60 → OVER pass projections dampened by up to 6% (game management effect)
- **Slip Correlation**: CORRELATED_RISK, OPPOSING_TEAMS_SAME_DIR, CONFLICTING, BOOSTING, POSSESSION_CONTRADICTION warnings
- **Possession Contradiction**: CRITICAL warning when user picks same direction on pass props for BOTH teams in a match (zero-sum violation)

### Calibration System
- **REMOVED** per user request. Was dragging projections toward the line.

## Key Features
- Image scanning with OCR validation
- Soccer-only prediction pipeline (basketball fully removed Feb 2026)
- True unified engine (Bayesian anchors AI, tempo-informed, favorite-aware)
- Game tempo estimation (high/low tempo affects pass projections)
- Heavy favorite dampening (leading teams reduce tempo)
- Post-fusion possession scaling for pass-related props
- Coin-flip zone detection with badge
- Slip correlation warnings with zero-sum possession contradiction detection
- INTEL Tab (aggregate dashboard: hit rates by prop/position/direction)
- Tracking Tab with expandable analysis
- Live game tracking, auto-settlement
- MongoDB fixture caching, Position tracking
- Square/Whop subscription management

## Tech Stack
- Frontend: React.js, Shadcn/UI
- Backend: FastAPI, Python asyncio, MongoDB
- AI: Grok 4.20 (xAI key), GPT-5.2 (Emergent LLM Key)
- Data: API-Sports (Soccer)
- Auth: Whop + Square subscriptions
- Caching: MongoDB with TTL

## Completed Work
- Full prediction pipeline with Bayesian anchor injection
- OCR validation gate
- Bayesian Engine v2 (25% covariate cap)
- Duplicate tactical breakdown fix
- Calibration system removed
- Post-fusion possession scaling
- Slip correlation & coin-flip guard
- Intel tab redesign (informational aggregate stats)
- Basketball complete removal (Feb 2026)
- **Game Tempo Estimation Layer (Feb 2026)** — expected match intensity boosts/suppresses pass projections
- **Heavy Favorite Dampening (Feb 2026)** — reduces OVER pass projections for heavy favorites
- **Possession Contradiction Detection (Feb 2026)** — CRITICAL zero-sum alert when saving same-direction pass props for both teams

## Upcoming Tasks
- Route prediction API calls through MongoDB cache (P2)

## Future/Backlog
- Frontend refactoring: Extract components from App.js (P3) — file is 2200+ lines
- Backend refactoring: Break down monolithic predict.py (P3)
- Auth architecture migration to httpOnly cookies (P3)
- Prediction self-correction feedback loop (P2)
- Batch scan predictions (P2)

## Key API Endpoints
- POST /api/scan-prop — Vision extraction (soccer only)
- POST /api/predict — Soccer prediction pipeline
- POST /api/picks/save — Save pick with slip correlation + possession contradiction
- POST /api/picks/list — List user picks
- POST /api/picks/live-update — Real-time live tracking
- GET /api/intel/sheet — Aggregate hit-rate data
- GET /api/health — Health check

## 3rd Party Integrations
- API-Sports (Soccer Data) — User API Key
- Square (Payments/Subs) — User API Key
- xAI Grok (Grok 4.20) — User API Key
- OpenAI GPT-5.2 — Emergent LLM Key
