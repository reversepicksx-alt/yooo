# ReversePicks - Soccer Prediction Platform PRD

## Original Problem Statement
Remake of ReversePicks (originally Gemini version) - a soccer player prop prediction webapp using API-Sports for data gathering and Gemini AI for elite predictions. Dark mode UI. Whop-based auth for subscription management.

## Architecture
- **Backend**: Python FastAPI (port 8001) with Gemini AI via emergentintegrations library
- **Frontend**: React (port 3000) with custom dark CSS, PWA-enabled
- **Database**: MongoDB (local)
- **AI Pipeline**: Triple AI — GPT-4o-mini (data compression) -> Grok-4 (tactical analysis + web search) -> Gemini 2.5 Flash (final JSON calibration)
- **Data Source**: API-Sports v3 (api-sports.io)
- **Auth**: Whop API (subscription verification) + bcrypt password hashing + session tokens

## Core Requirements
- 2-tab layout: PREDICT, TRACKING
- Pick of the Day featured card on Predict tab
- AI Wizard: Step-by-Step (6 steps single, 8 steps combo) + Natural Language Search (Tactical Search)
- 30+ supported leagues (Domestic, International Club, International Team)
- Player props: pass_attempts, shots, saves, tackles, key_passes, interceptions, blocks, dribbles, fouls_drawn, shots_on_target
- Over/Under line predictions with confidence scores
- Tactical chat AI assistant ("Tactical Search")
- Pick tracking with live/history views
- Dark mode theme
- Whop-based authentication with owner bypass, lifetime subs, and premium subscriptions
- Forgot Password via Whop re-verification
- PWA: installable, offline static caching, mobile-optimized
- Combo/Stack predictions: 2-player combined analysis for same prop type

## User Personas
- Sports bettors looking for data-driven prop predictions
- Soccer analysts wanting tactical insights
- App owner (josselj001@gmail.com)

## What's Been Implemented
- [x] Full backend with FastAPI + Triple AI integration (GPT-4o-mini -> Grok-4 -> Gemini 2.5 Flash)
- [x] API-Sports proxy (leagues, teams, players, stats, fixtures, H2H, standings)
- [x] Gemini-powered prediction engine with Bayesian metrics
- [x] Tactical Search chat with multi-turn Gemini conversations
- [x] Natural language query parsing
- [x] React frontend with dark mode
- [x] 2-tab navigation (Predict/Tracking)
- [x] AI Wizard with 6-step flow + breadcrumbs
- [x] Combo/Stack mode: 8-step flow for 2-player combined predictions
- [x] League selection organized by category
- [x] Player search with fallback across seasons
- [x] Projection card with confidence intervals, probability curves, recommendations
- [x] Pick tracking with save/remove functionality
- [x] Pick detail modal
- [x] Whop authentication system (owner auto-login, lifetime subs, password setup/login)
- [x] Premium UI: glass-morphism header/nav, smooth transitions, shimmer buttons, glow effects
- [x] Login page with RP crown logo, animated glow background
- [x] Logout button in header
- [x] Parallel API-Sports calls (~29s prediction time vs ~60s original)
- [x] Pick of the Day - AI-generated daily best prop bet
- [x] PWA Mobile Optimization - service worker, web manifest
- [x] Pick settling system - auto-checks match results via API-Sports
- [x] Enhanced Gemini prompt with pre-game tactical intel
- [x] Bookmaker Odds integration for accurate favorites/underdogs
- [x] Forgot Password feature
- [x] H2H match limit increased from 3 to 10, parallelized fetches
- [x] Saves prop stat mapping fix
- [x] recentSamples built from REAL API-Sports game logs (not AI hallucinated)
- [x] Picks persistence in MongoDB (no more localStorage data loss)
- [x] Live Tracking Cards with NOW/LINE/PACE/HIT%/progress bar, auto-refresh 2min
- [x] Whop signup link on login page
- [x] Systematic prediction fixes: saves ceiling, position baselines, game flow logic
- [x] Replaced Claude with Grok (xAI) for tactical analysis in Triple AI pipeline
- [x] Grok with live web search for real-time injuries, lineups, team news
- [x] Fixed duplicate player ID issue — name-based fallback for game log matching
- [x] Elite GK Saves Formula: Opp SoT x Save% x Context Multiplier with full UI breakdown
- [x] Matchup Overview on analysis page: possession bar, game type, moneyline, tactical alerts
- [x] Push notifications: in-app toast + notification bell with history
- [x] Re-analyze button on saved picks
- [x] Optimized API calls: removed formations/injuries endpoints, increased fixture pool
- [x] **Matchup Overview locked to real data** — possession, moneyline, game type, team names computed from actual API-Sports fixture stats instead of AI-generated (prevents inconsistency between predictions)
- [x] **Combo/Stack Prediction feature** — Stack 2 players from same game/league, same prop type, get combined projected total vs combined line with individual breakdowns

## Auth System Details
- Owner email (josselj001@gmail.com): Bypasses password entirely, instant login
- Lifetime sub emails: Hardcoded in server.py, require password setup on first login
- Whop premium members: Verified via Whop API, require password setup
- Non-members: Rejected with "No active membership" message
- Forgot Password: Re-verifies membership, allows new password
- Sessions: Stored in MongoDB, verified on page load

## API Keys (in .env)
- API-Sports key in backend/.env
- Emergent LLM Key in backend/.env
- Whop API key + Company ID in backend/.env
- xAI (Grok) API key in backend/.env

## Prioritized Backlog
### P0 (Critical)
All P0 items completed.

### P1 (High)
All P1 items completed.

### P2 (Medium)
- Slip correlation analysis — analyze multiple saved picks for the same game to flag conflicting or boosting correlations
- Prediction self-correction feedback loop — store outcomes after settlement and feed calibration patterns back to Gemini

### Future
- Add more prop types based on user feedback
- Performance optimization for API-Sports rate limits
- Refactor server.py (2200+ lines) and App.js (2000+ lines) into modular files
- Combo pick saving to tracking (currently view-only)
