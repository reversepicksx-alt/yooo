# ReversePicks — Product Requirements Document

## Core Product
Soccer-only player prop prediction platform. Users scan prop screenshots, AI extracts details, resolves player/team, and runs a prediction pipeline.

## Legal Compliance
- NO 3rd-party app names or player/team images
- NO explicit AI branding in UI

## Architecture

### Prediction Pipeline — Bayesian-First Unified Engine
```
Grok OCR → Data Fetch → BAYESIAN MATH (anchor) → Grok AI (math-informed) → BAYESIAN-FIRST FUSION → Tempo Scaling → Favorite Dampening → Guards → Final
```

1. **Grok Vision OCR** — Extracts player/prop/line (with validation gate)
2. **Data Fetch** — Player game logs, match dominance, opponent stats, odds
3. **Game Tempo Estimation** — Expected total goals → high/normal/low tempo classification
4. **Heavy Favorite Detection** — Teams with odds < 1.60 flagged for pass dampening
5. **Bayesian Engine v2** — 3-layer math projection (Prior + Momentum + Covariate)
6. **Grok 4.20 + GPT-5.2** — AI projection (math-informed via anchor injection, tempo-aware, favorite-aware)
7. **Bayesian-First Fusion v3** — Math OWNS the number, AI provides tactical adjustments:
   - Agreement: 55% Bayes, 45% AI
   - Disagree <15%: 70% Bayes, 30% AI
   - Disagree 15-25%: 80% Bayes, 20% AI
   - Disagree 25-35%: 90% Bayes, 10% AI
   - Disagree >35%: 95% Bayes, 5% AI (AI hallucinating)
8. **Post-Fusion Tempo Scaling** — High-tempo games boost pass volumes
9. **Post-Fusion Favorite Dampening** — Reduces OVER pass projections for heavy favorites
10. **Guards** — Coin-flip zone detection, tight edge caps

### Key Safety Systems
- **Divergence Guard**: Smooth gradient — the more AI disagrees with math, the less it matters
- **Coin Flip Guard**: projection within thin edge of line + Bayesian <60% → cap confidence, badge
- **Game Tempo**: expected total goals >= 3.2 = high tempo (+4-8% pass boost)
- **Favorite Dampening**: team odds < 1.60 → OVER pass projections dampened up to 6%
- **Possession Contradiction**: CRITICAL warning when picking same direction on pass props for both teams
- **Slip Correlation**: CORRELATED_RISK, OPPOSING_TEAMS_SAME_DIR, CONFLICTING, BOOSTING warnings
- **Double-Dip Prevention**: Dominance multiplier applied once pre-fusion (not duplicated post-fusion)
- **Possession Monster Guard**: When opponent avg possession >57%, their concession rate overrides the 50/50 blend (60→90% weight). Home advantage dampened up to 70% vs extreme possession teams.

### Calibration System
- **REMOVED** per user request

## Backtest Results (Feb 2026)
4/4 previously-missed picks corrected under the new engine:
- L. Ayling: UNDER→OVER ✅ (Divergence Guard 16%, 80% Bayes)
- A. Morris: UNDER→OVER ✅ (Divergence Guard 39%, 95% Bayes)
- D. Sanderson: OVER→UNDER ✅ (AI+Bayes agreement, 55% Bayes fusion)
- B. Whiteman: OVER→UNDER ✅ (AI+Bayes agreement, 55% Bayes fusion)
Regression check: 0 previously-correct picks broken

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
- Calibration system removed
- Post-fusion possession scaling (single-application, no double-dip)
- Slip correlation & coin-flip guard
- Intel tab redesign (informational aggregate stats)
- Basketball complete removal (Feb 2026)
- Game Tempo Estimation Layer (Feb 2026)
- Heavy Favorite Dampening (Feb 2026)
- Possession Contradiction Detection (Feb 2026)
- **Bayesian-First Fusion v3 (Feb 2026)** — smooth divergence gradient, math owns the number
- **Divergence Guard (Feb 2026)** — catches AI hallucination, overrides to math
- **Double-dip fix (Feb 2026)** — removed duplicate possession scaling post-fusion
- **Grok model name fix (Feb 2026)** — grok_engine.py updated to match predict.py
- **Possession Monster Formula (Feb 2026)** — Dynamic opponent-weighted possession for extreme matchups (opp avg >57%): scales from 60/40 to 90/10 opponent-driven. Home advantage dampened against possession monsters. Fixes over-projection of pass attempts vs teams like Barcelona.

## Upcoming Tasks
- Route prediction API calls through MongoDB cache (P2)

## Future/Backlog
- Frontend refactoring: Extract components from App.js (P3)
- Backend refactoring: Break down monolithic predict.py (P3)
- Auth architecture migration to httpOnly cookies (P3)
- Prediction self-correction feedback loop (P2)
- Batch scan predictions (P2)

## Key API Endpoints
- POST /api/scan-prop — Vision extraction (soccer only)
- POST /api/predict — Soccer prediction pipeline (Bayesian-first fusion)
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
