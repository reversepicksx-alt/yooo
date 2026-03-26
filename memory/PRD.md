# ReversePicks - Soccer Prediction Platform PRD

## Original Problem Statement
Remake of ReversePicks (originally Gemini version) - a soccer player prop prediction webapp using API-Sports for data gathering and Gemini AI for elite predictions. Dark mode UI. Whop-based auth for subscription management.

## Architecture
- **Backend**: Python FastAPI (port 8001) with Gemini AI via emergentintegrations library
- **Frontend**: React (port 3000) with custom dark CSS
- **Database**: MongoDB (local)
- **AI**: Gemini 2.5 Flash via Emergent Universal Key
- **Data Source**: API-Sports v3 (api-sports.io)
- **Auth**: Whop API (subscription verification) + bcrypt password hashing + session tokens

## Core Requirements
- 3-tab layout: PREDICT, TRACKING, CHAT
- AI Wizard: Step-by-Step (6 steps) + Natural Language Search
- 30+ supported leagues (Domestic, International Club, International Team)
- Player props: pass_attempts, shots, saves, tackles, key_passes, interceptions, blocks, dribbles, fouls_drawn, shots_on_target
- Over/Under line predictions with confidence scores
- Tactical chat AI assistant ("Tactical Uplink")
- Pick tracking with live/history views
- Dark mode theme
- Whop-based authentication with owner bypass, lifetime subs, and premium subscriptions

## User Personas
- Sports bettors looking for data-driven prop predictions
- Soccer analysts wanting tactical insights
- App owner (josselj001@gmail.com)

## What's Been Implemented (Feb 2026)
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
- [x] **FIXED (Feb 2026)**: Critical auth bug - `setPassword` React state setter was being called instead of `apiSetPassword` API function in password setup flow
- [x] **NEW (Feb 2026)**: Forgot Password feature - re-verifies Whop membership, then allows password reset without email sending

## Auth System Details
- Owner email (josselj001@gmail.com): Bypasses password entirely, instant login
- Lifetime sub emails: Hardcoded in server.py, require password setup on first login
- Whop premium members: Verified via Whop API, require password setup
- Non-members: Rejected with "No active membership" message
- Sessions: Stored in MongoDB, verified on page load

## API Keys (in .env)
- API-Sports key in backend/.env
- Emergent LLM Key in backend/.env
- Whop API key + Company ID in backend/.env

## Prioritized Backlog
### P0 (Critical)
- None remaining

### P1 (High)
- Live pick polling (track in-game stats in real-time)
- Push notifications for pick results

### P2 (Medium)
- Re-analyze pick button for saved picks
- Market sentiment analysis
- Slip correlation analysis
- Player photo integration
- Team logo integration
- PWA/mobile optimization

### Future
- Add more prop types based on user feedback
- Performance optimization for API-Sports rate limits
