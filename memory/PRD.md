# ReversePicks - Multi-Sport Player Props AI Analytics

## Problem Statement
Multi-sport player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players/teams, and runs a multi-AI consensus prediction pipeline. Supports Soccer, NBA, and WNBA with full live in-game tracking.

LEGAL: ALL 3rd-party app names and player/team images removed. No AI model names in user-facing UI text.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Multi-Sport**: Sport selector (Soccer/Basketball). Basketball covers NBA+WNBA.
- **Prediction Pipeline**: 3-AI Consensus (Gemini 2.0 Flash, GPT-4.1-mini, Grok 4.1 Fast) temperature=0, 15s grace, MIN_RESULTS=2
- **Payments**: Square Subscriptions (new signups) + Whop (existing subscribers)
- **Live Tracking**: Real-time in-game stats (basketball Q1-Q4/OT, soccer 1H/2H)

## Key Files
- `/app/backend/routes/square.py` — Square subscription management (plans, subscribe, status, cancel, webhook)
- `/app/backend/routes/auth.py` — Dual auth: Square subscriptions checked before Whop
- `/app/backend/routes/picks.py` — Live tracking with parallel fixture queries
- `/app/backend/routes/basketball_predict.py` — Basketball prediction engine
- `/app/backend/routes/predict.py` — Soccer prediction engine
- `/app/backend/routes/scan.py` — Sport-aware OCR scan (19 soccer + 17 basketball props)
- `/app/frontend/src/components/app/LoginPage.jsx` — Login + Subscribe flow

## Square Subscription Plans
- Weekly: $11/week (WEEKLY cadence)
- Monthly: $39.99/month (MONTHLY cadence, "Most Popular")
- Quarterly: $99.99/3 months (QUARTERLY cadence)

## Square Credentials (Sandbox)
- Application ID: sandbox-sq0idb-Cm8BJHqP_I76duTeYFFSvQ
- Location ID: L3MEW5MF01WTK
- Environment: sandbox (swap to production when ready)

## Critical Rules
- 3-AI engine only: Gemini 2.0 Flash + GPT-4.1-mini + Grok 4.1 Fast
- Do NOT reintroduce Claude Haiku (cost savings), GPT-4o (15x cost), Gemini 2.5 Flash (bad JSON)
- Identity fields force-set from request data, never AI output
- Live match detection: no opponent name check needed
- No AI model names in user-facing text
- Auth: Square subscriptions checked BEFORE Whop in check_access

## Completed Work (Mar 2026)
- 3-AI consensus engine (Claude removed for cost savings)
- Square Payments integration (3 plans, subscribe, cancel, status, webhook)
- Dual auth: Square + Whop coexistence
- Compound basketball props (reb_ast, pts_reb, pts_ast, blk_stl, steals, blocks, turnovers)
- Expanded soccer props (goals, assists, shots_assisted, fouls_committed, crosses, clearances, duels_won, yellow_cards, dribbles_success)
- Cross-league protection, identity field hardening, AI name scrubbing
- Soccer live tracking fix (parallel fixture queries)
- Combo prediction UI upgrade

## Pending Issues
### P0: Soccer Odds / Moneyline — needs verification

## Prioritized Backlog
### P1: Slip correlation analysis
### P2: Prediction feedback loop, Batch scan
### P3: SofaScore for NWSL
