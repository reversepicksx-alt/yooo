# ReversePicks - Soccer Prediction Platform PRD

## Original Problem Statement
Remake of ReversePicks (originally Gemini version) - a soccer player prop prediction webapp using API-Sports for data gathering and Gemini AI for elite predictions. Dark mode UI. Whop-based auth for subscription management.

## Architecture
- **Backend**: Python FastAPI (port 8001) with Gemini AI via emergentintegrations library
- **Frontend**: React (port 3000) with custom dark CSS, PWA-enabled
- **Database**: MongoDB (local)
- **AI**: Gemini 2.5 Flash via Emergent Universal Key
- **Data Source**: API-Sports v3 (api-sports.io)
- **Auth**: Whop API (subscription verification) + bcrypt password hashing + session tokens

## Core Requirements
- 3-tab layout: PREDICT, TRACKING, CHAT
- Pick of the Day featured card on Predict tab
- AI Wizard: Step-by-Step (6 steps) + Natural Language Search
- 30+ supported leagues (Domestic, International Club, International Team)
- Player props: pass_attempts, shots, saves, tackles, key_passes, interceptions, blocks, dribbles, fouls_drawn, shots_on_target
- Over/Under line predictions with confidence scores
- Tactical chat AI assistant ("Tactical Uplink")
- Pick tracking with live/history views
- Dark mode theme
- Whop-based authentication with owner bypass, lifetime subs, and premium subscriptions
- Forgot Password via Whop re-verification
- PWA: installable, offline static caching, mobile-optimized

## User Personas
- Sports bettors looking for data-driven prop predictions
- Soccer analysts wanting tactical insights
- App owner (josselj001@gmail.com)

## What's Been Implemented
- [x] Full backend with FastAPI + Gemini AI integration
- [x] API-Sports proxy (leagues, teams, players, stats, fixtures, H2H, standings)
- [x] Gemini-powered prediction engine with Bayesian metrics
- [x] Tactical Uplink chat with multi-turn Gemini conversations
- [x] Natural language query parsing
- [x] React frontend with dark mode
- [x] 3-tab navigation (Predict/Tracking/Chat)
- [x] AI Wizard with 6-step flow + breadcrumbs
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

## Bug Fixes (Mar 2026 - Code Audit)
- [x] Natural search no longer hardcoded to league 39 - detects player's actual league from majorLeagues array
- [x] Team/opponent stats now try multiple seasons (2026, 2025, 2024) instead of only CURRENT_SEASON
- [x] stats_list[-1] used for current team (transferred players get correct team, not old one)
- [x] settle-picks handles "push" (actual == line) instead of marking as miss
- [x] useEffect savedPicks dependency fixed (livePickCount instead of filter().length)
- [x] localStorage race condition fixed (picksInitialized ref skips first render write)
- [x] Frontend displays "PUSH" label for push results in tracking history
- [x] Standings fetcher also uses multi-season fallback
- [x] settle-picks timestamp guard — only settles against fixtures AFTER the pick was created (prevents matching old meetings)

## Enhanced Gemini Reasoning (Mar 2026 - Grok-style upgrade)
- [x] Chain-of-thought reasoning: Gemini now follows a 10-step structured analysis before giving projection
- [x] Key Evidence field: Quotes exact stat values with dates, opponents, and venue splits
- [x] Scenario Analysis field: Covers base case, blowout, trailing, cagey game scenarios with probability weights
- [x] Uncertainty Note field: Explicitly flags small sample sizes, missing data, coin-flip zones
- [x] Injury data integration: Fetches injuries/suspensions for the upcoming fixture via API-Sports
- [x] Injury impact rules in prompt: How missing players affect stat distributions
- [x] Sensitivity Tests: Explicit "what if" checks — sub at 60', down 2-0, bus parking, red card. Rates pick as ROBUST/MODERATE/FRAGILE
- [x] Substitution Risk Quantification: Calculates % of games with early sub, avg stat volume lost, weighted projection drag
- [x] Game Flow Dynamics: First-to-score possession impact, leading vs trailing stat adjustments
- [x] PPDA Approximation: Estimates opponent pressing intensity from tackles/interceptions to predict pass volume shifts
## Intelligence Upgrades v4 (Mar 2026 - Triple AI Pipeline + Heat Maps)
- [x] TRIPLE AI ARCHITECTURE:
  - GPT-4.1-mini (Data Processor): Compresses 15k raw JSON into compact analytical brief
  - Claude Sonnet 4.5 (Tactical Analyst): PPDA estimation, sub risk quantification, scenario analysis, sensitivity tests, game flow prediction
  - Gemini 2.5 Flash (Final Predictor): Synthesizes GPT + Claude outputs into calibrated prediction JSON
  - All three run IN PARALLEL during Wave 2 (~38s total, down from ~58s original)
- [x] Per-fixture team/opponent match stats via fixtures/statistics (possession, shots, passes per game)
- [x] Player game-by-game box scores via fixtures/players (individual stat lines with minutes)
- [x] Match Stat Zones visual component: Side-by-side team vs opponent stat bars
- [x] Graceful degradation: 25s timeout on Wave 2 + AI analysis, falls back to raw data if needed
- [x] Gemini prompt optimized: Receives pre-analyzed intel, focuses on calibration and JSON formatting

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

## Prioritized Backlog
### P0 (Critical)
- [x] H2H match limit increased from 3 to 10, parallelized fetches (DONE - Mar 2026)
- [x] Saves prop stat mapping fix — was showing pass_attempts instead of actual saves (DONE - Mar 2026)
- [x] recentSamples now built from REAL API-Sports game logs, not AI-generated (DONE - Mar 2026)
- [x] Picks persistence moved from localStorage to MongoDB — no more data loss (DONE - Mar 2026)
- [x] Live Tracking Cards with NOW/LINE/PACE/HIT%/progress bar, auto-refresh 2min (DONE - Mar 2026)
- [x] Whop signup link on login page (DONE - Mar 2026)
- [x] Systematic prediction fixes: saves ceiling from opponent SOT, position baselines, game flow logic (DONE - Mar 2026)

### P1 (High)
- Push notifications for pick results

### P2 (Medium)
- Slip correlation analysis
- Re-analyze pick button for saved picks
- Prediction self-correction feedback loop (store outcomes → improve calibration)

### Future
- Add more prop types based on user feedback
- Performance optimization for API-Sports rate limits
