# ReversePicks - Soccer Prediction Platform PRD

## Original Problem Statement
Remake of ReversePicks (originally Gemini version) - a soccer player prop prediction webapp using API-Sports for data gathering and Gemini AI for elite predictions. Dark mode UI.

## Architecture
- **Backend**: Python FastAPI (port 8001) with Gemini AI via emergentintegrations library
- **Frontend**: React (port 3000) with custom dark CSS
- **Database**: MongoDB (local)
- **AI**: Gemini 2.5 Flash via Emergent Universal Key
- **Data Source**: API-Sports v3 (api-sports.io)

## Core Requirements
- 3-tab layout: PREDICT, TRACKING, CHAT
- AI Wizard: Step-by-Step (6 steps) + Natural Language Search
- 30+ supported leagues (Domestic, International Club, International Team)
- Player props: pass_attempts, shots, saves, clearances, tackles
- Over/Under line predictions with confidence scores
- Tactical chat AI assistant ("Tactical Uplink")
- Pick tracking with live/history views
- Dark mode theme

## User Personas
- Sports bettors looking for data-driven prop predictions
- Soccer analysts wanting tactical insights
- App owner (josselj001@gmail.com)

## What's Been Implemented (Jan 2026)
- [x] Full backend with FastAPI + Gemini AI integration
- [x] API-Sports proxy (leagues, teams, players, stats, fixtures, H2H, standings, odds)
- [x] Gemini-powered prediction engine with Bayesian metrics
- [x] Tactical Uplink chat with multi-turn Gemini conversations
- [x] Natural language query parsing
- [x] React frontend with dark mode
- [x] 3-tab navigation (Predict/Tracking/Chat)
- [x] AI Wizard with 6-step flow
- [x] League selection organized by category
- [x] Player search with fallback across seasons
- [x] Projection card with confidence intervals, probability curves, recommendations
- [x] Pick tracking with save/remove functionality
- [x] Pick detail modal
- [x] **Whop authentication system** (owner auto-login, lifetime subs, password setup/login)
- [x] **Premium UI overhaul**: glass-morphism header/nav, smooth transitions, shimmer buttons, glow effects
- [x] **Login page** with RP crown logo, animated glow background, premium feel
- [x] **Logout button** in header
- [x] **Lifetime subscriber emails** hardcoded: faron2allen, brayanfgaleas, odr310, joseharo197, rijulgauchan1, gordo0210

## API Keys
- API-Sports: 8154742f66d14cb52548c73c3edfbee3
- Emergent LLM Key: sk-emergent-027Fc9d1d15F8D7Df0

## Prioritized Backlog
### P0 (Critical)
- None remaining

### P1 (High)
- Live pick polling (track in-game stats in real-time)
- Re-analyze pick button for saved picks
- Authentication/login system (optional - owner requested)

### P2 (Medium)
- Market sentiment analysis
- Slip correlation analysis
- Player photo integration
- Team logo integration

### Next Tasks
- Add more prop types based on user feedback
- Performance optimization for API-Sports rate limits
- PWA/mobile optimization
