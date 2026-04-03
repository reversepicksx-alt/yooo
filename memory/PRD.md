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
- Admin Settings panel
- Cross-country league detection (Copa Libertadores/Sudamericana)

### Batch Scan Player Report (Completed April 3, 2026) — Owner-Only
- Multi-prop extraction from single screenshots
- Player Report UI with prop selection, sequential predictions, comparison table
- Color-coded recommendations, expandable details, Best Value auto-highlight
- Owner-only gate for testing

### Self-Learning Calibration System (Completed April 3, 2026)
- Automatic post-mortem 3-AI analysis for missed picks
- Calibration pattern extraction and bias correction injection
- Auto-trigger on settlement, manual correction, and backfill
- Autonomous fire-and-forget background tasks

### Match Dominance Engine (Completed April 3, 2026)
- Opponent-aware possession model with home advantage and odds-derived dominance
- Match dominance multiplier for pass-dependent props
- GPT minutes double-count fix

### Stats-Aware Position Resolver (Completed April 3, 2026)
- Stats-evidence position resolution (tackles, blocks, aerials, passes, dribbles, shots)
- Dual-AI validation for defenders with stats heuristic tiebreaker
- 30-day cache expiry
- Force-3-model consensus with retry logic

### Code Quality Fixes (Completed April 3, 2026)
- Fixed React hook stale closure bug in live polling (savedPicksRef pattern)
- Replaced all index-as-key anti-patterns with unique IDs (chat messages, follow-ups, FAQs, factors, h2h games, player breakdowns)
- Added error logging to all 6 empty catch blocks in App.js
- Fixed broken eslint-disable comment format (em-dash -> proper format)
- Moved hardcoded test emails to env vars in test_auth.py

### Tracking Tab Split + Calibration Insights Panel (Completed April 3, 2026)
- **Tab split**: Tracking tabs changed from `Live | Won | Missed` to `Live | Won | Lost | Pushed | Insights`
- **Pushes separated**: Push results now have their own tab instead of being grouped with Won
- **Calibration Insights panel**: New "Insights" tab showing everything the system learned from miss analysis:
  - System Learning Summary: total analyzed, total misses, active corrections count
  - Per sport/prop type cards: miss count, avg error %, bias direction, active correction %
  - Status indicators: CORRECTING (active), pending (needs more misses), or inconsistent bias
  - Refresh button to reload latest calibration data
- **Notification routing**: Click-through from notifications now routes to correct tab (hit→Won, miss→Lost, push→Pushed)

## Key API Endpoints
- `POST /api/scan-prop` — Vision extraction from prop screenshots
- `POST /api/predict` — Soccer prediction pipeline (with calibration)
- `POST /api/basketball/predict` — Basketball prediction pipeline (with calibration)
- `POST /api/re-resolve` — Re-resolve player after editable scan card changes
- `POST /api/picks/misses` — Get missed picks with auto-analysis
- `POST /api/picks/analyze-miss` — Manual miss analysis trigger (fallback)
- `POST /api/calibration/insights` — Get all calibration learnings per sport/prop type
- `POST /api/picks/save` — Save a pick for tracking
- `POST /api/picks/live-update` — Refresh live game data
- `POST /api/picks/correct` — Manual correction of actual value

## DB Collections
- `picks` — User picks with settlement data
- `miss_analyses` — 3-AI postmortem results per missed pick
- `calibration_stats` — Aggregated bias patterns per sport+propType
- `calibration_patterns` — Individual miss calibration records
- `player_positions` — Cached position/role data
- `settings` — Admin config key-value store
- `users` — User auth and subscription data

## 3rd Party Integrations
- API-Sports (Sports Data) — User API Key
- Square (Payments/Subs) — User API Key
- xAI Grok (grok-4-1-fast-non-reasoning) — User API Key
- Gemini 2.0 Flash — Emergent LLM Key
- OpenAI GPT-4.1-mini — Emergent LLM Key

## Upcoming Tasks (P1)
- Slip correlation analysis — Analyze multiple saved picks for the same game to flag conflicting or boosting correlations

## Future Backlog
- Frontend Refactoring: Split App.js (~3,150 lines) into smaller components + custom hooks (P3)
- Backend Refactoring: Break down high-complexity functions in basketball_predict.py, basketball_utils.py, auth.py (P3)
- Auth Architecture Migration: Move from localStorage to httpOnly cookies (P3)
- Enable Batch Scan for all users (P3)
- Prediction self-correction feedback loop (P2)
- SofaScore Integration (P3) — Replace API-Sports for NWSL data

## Legal Compliance
ALL 3rd-party app names and player/team images have been removed. Do not reintroduce them. Do not use explicit AI branding in the UI.
