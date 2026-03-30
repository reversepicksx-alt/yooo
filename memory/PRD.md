# ReversePicks - Soccer Player Props Analytics

## Product Overview
Mobile-first webapp for analyzing soccer player props (pass attempts, shots, saves). Users upload a screenshot of a player prop via the Scan tab, GPT-4o Vision extracts details, and a Dual AI pipeline (Grok-4.20 + Gemini 2.5 Flash) generates predictions with API-Sports data.

## Tech Stack
- **Frontend**: React.js, Shadcn/UI, CSS Variables, recharts
- **Backend**: FastAPI, Python asyncio, Motor (MongoDB)
- **AI**: GPT-4o (Vision scan), Grok-4.20-reasoning (tactics), Gemini 2.5 Flash (JSON formatting)
- **Data**: API-Sports (v3.football.api-sports.io)
- **Auth**: Whop memberships + password auth
- **DB**: MongoDB (picks, sessions, users, manual_access_grants)

## Architecture (Post-Refactor 2026-03-30)

### Backend Structure
```
/app/backend/
├── server.py              # 61 lines - FastAPI app, CORS, router includes, startup
├── config.py              # Constants, env vars, DB, shared state (SUPPORTED_LEAGUES, etc.)
├── models.py              # All Pydantic request models
├── utils.py               # api_football_request, strip_accents, get_recent_fixtures_fast
├── routes/
│   ├── auth.py            # /api/auth/* (verify-whop, login, set-password, etc.)
│   ├── leagues.py         # /api/health, /api/leagues, /api/leagues/{id}/teams, /api/football/status
│   ├── players.py         # /api/players/search, /api/player/{id}/stats
│   ├── predict.py         # /api/predict (Dual AI pipeline, ~1136 lines)
│   ├── scan.py            # /api/scan-prop (Vision AI + player resolution, ~617 lines)
│   ├── combo.py           # /api/predict-combo, /api/predict-combo/{id}
│   ├── picks.py           # /api/picks/* (save, list, delete, correct, live-update, settle)
│   ├── chat.py            # /api/chat/*, /api/parse-query
│   └── misc.py            # /api/pick-of-the-day
```

### Frontend Structure
```
/app/frontend/src/
├── App.js                 # 1834 lines - Auth state, tab switching, scan/tracking tab JSX
├── App.css                # All styles
├── api.js                 # API fetch wrappers
├── constants.js           # PROP_TYPES, getPropLabel
├── components/
│   └── app/
│       ├── ProjectionCard.jsx   # Full prediction display card
│       ├── LoginPage.jsx        # Auth flow (email, password, setup, reset)
│       ├── PickOfTheDayCard.jsx # Daily featured pick card
│       ├── ProbabilityChart.jsx # Recharts probability distribution
│       ├── MatchStatZones.jsx   # Team vs opponent stat bars
│       └── H2HSection.jsx      # Head-to-head player stats
```

## Completed Work

### 2026-03-30 (Current Session)
- [x] Fixed "NO MATCH" bug for international players with Nordic characters (Højbjerg, Euro Qualifiers)
  - Added Euro Qualifiers (960), Euro Championship (4), World Cup (1), AFCON Qualifiers (115) to INTERNATIONAL_LEAGUES
  - Added "czechia" alias to NATION_TO_LEAGUES and TEAM_LEAGUE_MAP
  - Added Ligue 1 (61) to Denmark's NATION_TO_LEAGUES (Højbjerg → Marseille)
  - International squad fallback now tries CURRENT_SEASON and CURRENT_SEASON-1
- [x] Major Codebase Refactor
  - Backend: server.py 3219 → 61 lines, split into 12 modules (config, models, utils, 9 route files)
  - Frontend: App.js 2704 → 1834 lines, 6 components extracted to components/app/
  - All 21 tests passed post-refactor (16 backend + 5 frontend)

### Previous Sessions
- Scan tab with GPT-4o Vision for screenshot prop extraction
- Dual AI prediction pipeline (Grok-4.20 + Gemini 2.5 Flash)
- Complex player matching (squad fallback, accent stripping, national team resolution)
- Live match tracking with auto-refresh
- Clickable HOME/AWAY venue toggle on Scan results
- Removed all 3rd-party player photos/team logos (copyright)
- Removed all competitor app names (legal)
- Hidden Predict and Guide tabs (user request)
- Hardcoded lifetime VIP emails

## Legal Compliance
- NO 3rd-party app names (PrizePicks, DraftKings, etc.) anywhere
- NO player photos or team logos
- Prompts say "sportsbook" or "player prop image"

## Lifetime VIP Emails
- josselj001@gmail.com (Owner)
- faron2allen@gmail.com, jossel0701@gmail.com, brayanfgaleas@icloud.com
- odr310@gmail.com, joseharo197@gmail.com, rijulgauchan1@gmail.com
- gordo0210@icloud.com, brianavina23@gmail.com, andrewfitz97@yahoo.com
- jose108798@gmail.com, letwins04@gmail.com, Quon.qg@gmail.com
- Jesselopezj@hotmail.com, jaredlee0414@gmail.com

## Prioritized Backlog
### P2 (Medium)
- Slip correlation analysis - Analyze multiple picks for conflicting/boosting patterns
- User Record Tracker - HIT/MISS ratio, ROI, streak display
- Prediction self-correction feedback loop - Store outcomes, feed calibration back

### P3 (Future)
- Batch scan predictions - Multiple props from one image
- RapidAPI SofaScore integration (if user subscribes)
- Scan tab: camera capture (mobile)
- Save scanned picks directly to tracking
