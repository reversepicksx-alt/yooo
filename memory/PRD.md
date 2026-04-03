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
- Editable Scan Cards (pencil icon to override player/team/opponent/position/role)
- Auto-caching Team Resolver (266+ teams across 14 leagues on startup)
- Position Validation (auto-corrects impossible combos like GK-Fullback)
- Live game tracking (updated every 30s)
- Won/Missed tabs (replaced old History tab)
- Square Checkout subscription integration
- Whop subscription integration (legacy, still active)
- Admin Settings panel
- Cross-country league detection (Copa Libertadores/Sudamericana)

### Square Subscription Sync Fix (Completed April 3, 2026) — P0
- **Root cause**: Webhook only handled `subscription.updated` but checkout creates Payment Links. Square sends `payment.completed` which was ignored. Zero records ever created in `square_subscriptions`.
- **Webhook enhanced**: Now handles `payment.completed`, `order.updated`, `order.completed`, `invoice.payment_made` events. Matches by buyer email AND order_id.
- **Self-recovery flow**: "Already paid? Verify your payment" on login page. Searches Square's 90-day payment history directly.
- **Admin tools**: `POST /api/square/admin/activate` and `POST /api/square/admin/bulk-verify`.
- **All 16 paying customers activated** via admin bulk activation.

### AI Contradiction Fix (Completed April 3, 2026) — P0
- **Bug**: Calibration/dominance post-processing could flip recommendation direction AFTER consensus was computed, causing "Unanimous UNDER" + "RECOMMEND: OVER" contradiction.
- **Fix**: Unanimous consensus (all 3 models agree) can no longer be overridden by calibration or match dominance adjustments. Applied to both soccer and basketball prediction pipelines.

### Opponent Resolution Enhancement (Completed April 3, 2026) — P1
- **Bug**: AI vision models output abbreviated team names (M'gladbach, Dortmund, Leverkusen, etc.) that didn't match anything in the team cache.
- **Fix**: Added `SCAN_ALIASES` dictionary with 80+ common team abbreviations covering Bundesliga, La Liga, Serie A, Ligue 1. Enhanced `_generate_aliases` to extract city names from compound team names (Borussia → gladbach/monchengladbach). Added `@`/`vs` prefix stripping in `_resolve_opponent`.

### Access Type Source Display (Completed April 3, 2026)
- Access type now shows source: "Premium (Square)", "Premium (Whop)", "Lifetime", "Owner"
- Displayed in profile tab so admin can see how each user was verified

### Self-Learning Calibration System (Completed April 3, 2026)
- Automatic post-mortem 3-AI analysis for missed picks
- Calibration pattern extraction and bias correction injection
- Auto-trigger on settlement, manual correction, and backfill

### Component Split Refactor (Completed April 3, 2026)
- App.js reduced from 3,328 to 2,441 lines (27% reduction)
- Extracted Header.jsx, TrackingTab.jsx, ProfileTab.jsx, GuideTab.jsx, constants.js

## Key API Endpoints
- `POST /api/scan-prop` — Vision extraction from prop screenshots
- `POST /api/predict` — Soccer prediction pipeline
- `POST /api/basketball/predict` — Basketball prediction pipeline
- `POST /api/square/webhook` — Square webhook (payment.completed, order events)
- `POST /api/square/verify-payment` — Self-recovery for paid users
- `POST /api/square/admin/activate` — Admin: activate specific customer
- `POST /api/square/admin/bulk-verify` — Admin: bulk-verify all pending checkouts
- `POST /api/calibration/insights` — Calibration learnings

## 3rd Party Integrations
- API-Sports (Sports Data) — User API Key
- Square (Payments/Subs) — User API Key
- Whop (Legacy Auth/Subs) — User API Key (still active)
- xAI Grok (grok-4-1-fast-non-reasoning) — User API Key
- Gemini 2.0 Flash — Emergent LLM Key
- OpenAI GPT-4.1-mini — Emergent LLM Key

## Upcoming Tasks (P1)
- Slip correlation analysis — Analyze multiple saved picks for the same game to flag conflicting or boosting correlations

## Future Backlog
- ScanTab component extraction from App.js (P3)
- Backend refactoring: break down high-complexity functions (P3)
- Auth cookie migration: localStorage → httpOnly cookies (P3)
- Enable Batch Scan for all users (P3)
- Prediction self-correction feedback loop (P2)
- SofaScore Integration for NWSL data (P3)

## Legal Compliance
ALL 3rd-party app names and player/team images have been removed. Do not reintroduce them. Do not use explicit AI branding in the UI.
