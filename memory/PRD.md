# ReversePicks - Multi-Sport Player Props AI Analytics

## Problem Statement
Multi-sport player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players/teams via cache, and runs a multi-AI consensus prediction pipeline. Supports Soccer, NBA, and WNBA with full live in-game tracking.

LEGAL: ALL 3rd-party app names and player/team images removed. No AI model names in user-facing UI text.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Prediction Pipeline**: 3-AI Consensus (Gemini 2.0 Flash, GPT-4.1-mini, Grok 4.1 Fast) temperature=0, 15s grace, MIN_RESULTS=2
- **Payments**: Square Subscriptions (new) + Whop (existing)
- **Live Tracking**: 30-second polling interval, name-based player matching fallback

## Key Files
- `/app/backend/routes/square.py` — Square subscription management
- `/app/backend/routes/auth.py` — Dual auth: Square + Whop
- `/app/backend/routes/admin.py` — Admin settings (API key management, owner-only)
- `/app/backend/routes/picks.py` — Live tracking (30s refresh, name-based fallback)
- `/app/backend/routes/basketball_predict.py` — Basketball prediction
- `/app/backend/routes/predict.py` — Soccer prediction
- `/app/backend/routes/scan.py` — Sport-aware OCR scan (19 soccer + 17 basketball props)
- `/app/backend/basketball_cache.py` — NBA abbreviation map + team/player cache
- `/app/backend/config.py` — Dynamic settings system (get_dynamic_api_key, init_dynamic_settings)
- `/app/frontend/src/components/app/LoginPage.jsx` — Login + Subscribe flow
- `/app/frontend/src/App.css` — Compact card styling (max-width: 400px centered)

## UI Rules
- Cards: compact (10px padding, 13px player name, 14px stat values, 7px labels)
- Main content: max-width 400px, centered with margin: 0 auto
- Live refresh: 30 seconds
- Record tracker: compact, 14px stat values

## Completed Work (Mar-Apr 2026)
- 3-AI consensus engine (Claude removed for cost)
- Square Payments ($11/week, $39.99/month, $99.99/3 months)
- Compact card UI (70% smaller), centered layout
- Live refresh 30s (was 2 min)
- Soccer player name-based stat matching fallback
- All prop types: 19 soccer + 17 basketball
- Cross-league protection, identity field hardening
- NBA team abbreviation map (35 entries, all 30 teams) — fixes NO MATCH for LAC, POR, etc.
- NBA-preferred team lookup (Portland → Trail Blazers, not WNBA)
- API key updated (new key active, Mega plan)
- Soccer + Basketball odds verified working
- Fixed .gitignore blocking .env files from production deployment
- **Admin Settings Panel** — Owner can update API-Sports key directly from Profile tab, stored in MongoDB, takes effect instantly without redeployment. Includes Test Key and Save Key functionality.
- **Admin Settings Extended** — All Square payment keys (Access Token, App ID, Location ID, Environment) now manageable from admin panel. Frontend payment form dynamically fetches Square config from backend.
- **Soccer Position Comparison Engine** — When predicting a prop, fetches 3-7 same-position players (FWD vs FWDs, MID vs MIDs, DEF vs DEFs, GK vs GKs) who played against the same opponent recently. Shows per-90 rates, ratings, and averages. Data is fed to all 3 AIs as additional context and displayed on the prediction card.
- **AI Position Resolver** — When the API returns generic positions (Attacker, Midfielder, etc.), Grok AI identifies the exact position (LW, ST, CM, CB, etc.) and caches it in MongoDB. First call uses AI, all subsequent calls are instant from cache. Player position badge on card shows the specific position (e.g., "LW" instead of "Attacker").
- **Position Resolver Accuracy Fix (Apr 2026)** — Added API-Sports category constraints (GENERIC_TO_SPECIFIC map) so Defender→only CB/LB/RB/LWB/RWB, Attacker→only LW/RW/CF/ST/SS/CAM. Added POSITION_ROLE_MAP validation preventing mismatches (e.g., "Ball-Playing CB" on a LB). Clears cache on mismatch detection.
- **Consensus Text Fix (Apr 2026)** — Fixed hardcoded "4 AI models" in consensus strings to dynamically reflect actual model count (len(valid_preds)). Applied to both soccer and basketball prediction pipelines.
- **AI Model Nicknames (Apr 2026)** — Model breakdown UI now shows GE (Gemini), GK (Grok), GP (GPT) instead of generic AI-1/AI-2/AI-3.
- **Matchup Home/Away Format (Apr 2026)** — Projection card now shows "Team @ Opponent" for away games, "Team vs Opponent" for home games.
- **Position on Scan Card (Apr 2026)** — Scan results now look up player_positions MongoDB cache and display position/role badge on the pre-prediction scan card when available.

## Resolved Issues
- P0: Soccer Odds / Moneyline — RESOLVED (was expired API key)
- NBA team abbreviations not resolving — RESOLVED (added NBA_ABBREV_MAP)
- Production deployment not receiving .env updates — RESOLVED (fixed .gitignore)
- P0: Position resolver misclassifying players (e.g., Alex Sandro as CB) — RESOLVED (added category constraints + role validation)
- P0: Consensus text showing "4 AI models" instead of 3 — RESOLVED (dynamic count)
- P0: AI model recommendation contradicting projected value (e.g., OVER with projection below line) — RESOLVED (enforced per-model recommendation based on projectedValue vs line)
- P1: Position comparison showing 4+ players from same team — RESOLVED (max 1 per team dedup)
- P1: Position comparison showing wrong-position players (LB in CB comparison) — RESOLVED (specific position filtering via cached positions)
- P0: Basketball FGA/FGM stats only counting 2pt attempts, missing 3pt attempts — RESOLVED (combined field_goals + threepoint_goals). Kelly Oubre: 5.9→10.6, Paul George: 6.3→13.1
- P1: Basketball DNP games (0 minutes) counted as 0-stat data points deflating averages — RESOLVED (< 5 min filter)
- P0: Brazilian teams (Coritiba, Botafogo) showing as CONCACAF Nations League — RESOLVED (league inference now checks hardcoded team map BEFORE trusting AI vision guess; added 24+ missing Brazilian teams to TEAM_LEAGUE_MAP)
- P1: Square payment card input all white on dark theme — Styling improved with dark container frame (Square iframe background is unchangeable per their security policy)

## Prioritized Backlog
### P1: Slip correlation analysis
### P2: Prediction feedback loop, Batch scan
### P3: SofaScore for NWSL

## Refactoring Needs
- App.js is 2600+ lines — should be split into smaller components
