# ReversePicks — Product Requirements Document

## Original Problem Statement
Web app remake of a sports analytics platform focusing on Sports Player Props (pass attempts, shots, points, assists, etc.). Users scan prop screenshots, an AI Vision model extracts the details, resolves the player/team, and runs a prediction pipeline using a 3-AI consensus engine.

## Core Architecture
- **Frontend**: React.js, Shadcn/UI
- **Backend**: FastAPI, Python asyncio, MongoDB
- **AI Engine**: 3-AI parallel consensus (Gemini 2.0 Flash, Grok 4.1 Fast, GPT-4.1-mini) at temperature=0

## What's Been Implemented

### Core Features (Complete)
- Image scanning pipeline (prop screenshot -> AI extraction -> player/team resolution)
- Soccer prediction pipeline (19 prop types, per-90 analysis, H2H, position comparison)
- Basketball prediction pipeline (17 prop types, pace analysis, matchup context)
- 3-AI parallel consensus engine with deterministic outputs
- Editable Scan Cards, Auto-caching Team Resolver (266+ teams across 14 leagues)
- Position Validation, Live game tracking (30s), Won/Missed tabs
- Square Checkout subscription + Whop subscription (both active)
- Admin Settings panel, Cross-country league detection

### Square Subscription Sync Fix (April 3, 2026) — P0
- Enhanced webhook: handles payment.completed, order.updated/completed, invoice.payment_made
- Self-recovery: "Already paid? Verify your payment" on login (searches Square payment history)
- Admin tools: admin/activate, admin/bulk-verify endpoints
- All 16 paying customers activated. Access type shows source: "Premium (Square)" / "Premium (Whop)"

### AI Contradiction Fix (April 3, 2026) — P0
- Calibration/dominance can no longer flip unanimous AI consensus (all 3 models agree)
- Applied to both soccer and basketball prediction pipelines

### Opponent Resolution Enhancement (April 3, 2026) — P0
- Added SCAN_ALIASES: 80+ team abbreviations (Bundesliga, PL, La Liga, Serie A, Ligue 1, MLS, Liga MX)
- Enhanced _generate_aliases: city name extraction from compound names, "utd"<->"united" expansion
- Added @ / vs prefix stripping in _resolve_opponent
- Query expansion in find_team: tries abbreviation variants automatically
- **70/70 resolution tests passed** (55 find_team + 15 _resolve_opponent with real scan formats)

### Self-Learning Calibration System (April 3, 2026)
- Automatic 3-AI post-mortem for missed picks, calibration pattern extraction

### Component Split Refactor (April 3, 2026)
- App.js reduced from 3,328 to 2,441 lines (27% reduction)

### Single Prop Intelligence Upgrade & Batch Mode Removal (April 4, 2026) — P0
- **Client feedback**: Batch/multi-prop analysis appeared smarter than single prop because single prop's `ProjectionCard` never displayed the `tacticalBreakdown` AI narrative (despite the backend already generating it)
- Removed batch analysis mode entirely (state, functions, PlayerReport component import, UI)
- Added `tacticalBreakdown` display to `ProjectionCard.jsx` with markdown bold parsing for section headers
- Added `matchContext` badge showing competition name + round (e.g., "FA CUP · Quarter-finals") so users can see if a match is Cup vs League
- Backend now includes `matchContext` (league, round, date) in prediction response from fixture data
- **Testing**: 100% backend (10/10), 100% frontend — verified via testing agent (iteration 48)

### Subscription Plan Management (April 4, 2026) — P0
- **Client request**: User "Zay_Bets" (xaviersteverson@gmail.com) asked how to change from weekly to monthly plan
- Backend: `POST /api/square/change-plan` — uses **cancel+recreate** approach (swap_plan had invisible pending action bug)
- Verifies current plan from Square directly to prevent DB/Square mismatches
- Frontend: SubscriptionManager in ProfileTab shows plan, status, next billing + "Change Plan" toggle
- Only visible for Square subscribers. "Close" button (not "Cancel") to avoid confusion
- **Auto-sync fix**: sorts ACTIVE subs first, tracks emails to prevent cancelled subs overwriting active ones
- **Plan resolution**: Sync now matches `plan_variation_id` against `square_plans` collection
- **Testing**: 100% pass — verified via testing agent (iterations 47, 48, 49)

## 3rd Party Integrations
- API-Sports — User API Key | Square — User API Key | Whop — User API Key (still active)
- xAI Grok — User API Key | Gemini 2.0 Flash — Emergent LLM Key | OpenAI GPT-4.1-mini — Emergent LLM Key

## Upcoming Tasks (P1)
- Slip correlation analysis

## Future Backlog
- ScanTab extraction from App.js (P3)
- Backend function refactoring (P3)
- Auth cookie migration (P3)
- Batch Scan for all users (P3)

## Legal Compliance
ALL 3rd-party app names and player/team images removed. No explicit AI branding in UI.
