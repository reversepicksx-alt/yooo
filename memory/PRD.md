# ReversePicks - Soccer Player Props Analytics

## Product Overview
Mobile-first webapp for analyzing soccer player props (pass attempts, shots, saves). Users upload a screenshot of a player prop via the Scan tab, GPT-4o Vision extracts details, and a Dual AI pipeline (Grok-4.20 + Gemini 2.5 Flash) generates predictions with API-Sports data.

## Tech Stack
- **Frontend**: React.js, Shadcn/UI, CSS Variables, recharts
- **Backend**: FastAPI, Python asyncio, Motor (MongoDB)
- **AI**: GPT-4o (Vision scan), Grok-4.20-reasoning (tactics), Gemini 2.5 Flash (JSON formatting)
- **Data**: API-Sports (v3.football.api-sports.io) + MongoDB player/team cache
- **Auth**: Whop memberships + password auth
- **DB**: MongoDB (picks, sessions, users, manual_access_grants, cache_*)

## Architecture (Post-Refactor 2026-03-30)

### Backend Structure
```
/app/backend/
├── server.py              # FastAPI app, CORS, router includes, startup
├── config.py              # Constants, env vars, DB, shared state
├── models.py              # All Pydantic request models
├── utils.py               # api_football_request, strip_accents, get_recent_fixtures_fast
├── cache.py               # MongoDB-backed API-Football cache (players, teams, leagues, national teams)
├── routes/
│   ├── auth.py            # /api/auth/* (verify-whop, login, set-password, etc.)
│   ├── leagues.py         # /api/health, /api/leagues, /api/cache/* endpoints
│   ├── players.py         # /api/players/search, /api/player/{id}/stats
│   ├── predict.py         # /api/predict (Dual AI pipeline)
│   ├── scan.py            # /api/scan-prop (Vision AI + cache-first player resolution)
│   ├── combo.py           # /api/predict-combo
│   ├── picks.py           # /api/picks/* (save, list, delete, correct, live-update, settle)
│   ├── chat.py            # /api/chat/*, /api/parse-query
│   └── misc.py            # /api/pick-of-the-day
```

### Frontend Structure
```
/app/frontend/src/
├── App.js                 # Auth state, tab switching, scan/tracking tab JSX
├── App.css                # All styles
├── api.js                 # API fetch wrappers
├── constants.js           # PROP_TYPES, getPropLabel
├── components/
│   └── app/
│       ├── ProjectionCard.jsx   # Full prediction display card
│       ├── LoginPage.jsx        # Auth flow
│       ├── PickOfTheDayCard.jsx # Daily featured pick card
│       ├── ProbabilityChart.jsx # Recharts probability distribution
│       ├── MatchStatZones.jsx   # Team vs opponent stat bars
│       └── H2HSection.jsx      # Head-to-head player stats
```

## Completed Work

### 2026-03-30 (Session 2 - Cache-First Resolution)
- [x] **Wired scan.py to use MongoDB cache as PRIMARY player resolution**
  - Replaced ~400 lines of complex API-Sports search with cache-first lookups
  - `get_player_by_name()` uses 4-tier search: exact nameClean → last name end-of-string → word-boundary full → word-boundary last
  - Word-boundary regex prevents false positives (e.g., "Saka" no longer matches "Wan-Bissaka")
  - API-Sports fallback only used if cache misses
  - Opponent resolution also uses cache first (`get_team_by_name`, `get_national_team_id`)
  - League inference uses cache `get_team_info()` before hardcoded map
- [x] **Added `get_team_info()` to cache.py** - Returns full team doc including leagueId
- [x] **Fixed player name matching** - Word boundary regex for accent-stripped names
- [x] **All 14 backend tests passed (100%)** - Verified cache lookups for Højbjerg, Salah, Saka, Ødegaard, Mbappé, national teams

### 2026-03-30 (Session 1 - Refactor + Cache Build)
- [x] Fixed "NO MATCH" bug for international players with Nordic characters (Højbjerg, Euro Qualifiers)
- [x] Major Codebase Refactor (backend 3219→61 lines, frontend 2704→1834 lines)
- [x] Fixed international match pipeline — uses national team IDs and data, not club data
- [x] Built MongoDB-backed API-Football cache (`cache.py`)
  - 1,225 leagues, 1,158 teams, ~24,000 players, 485 national team entries
  - `nameClean` field for accent-stripped lookups
  - 7-day auto-refresh TTL, 24h background refresh loop
  - Transfer detection system

### Previous Sessions
- Scan tab with GPT-4o Vision for screenshot prop extraction
- Dual AI prediction pipeline (Grok-4.20 + Gemini 2.5 Flash)
- Live match tracking with auto-refresh
- Clickable HOME/AWAY venue toggle
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

## Cache System Details
- **Collections**: cache_leagues, cache_teams, cache_players, cache_national, cache_transfers, cache_meta
- **Indexes**: teamId, leagueId, nameLower, nameClean, playerId on appropriate collections
- **Refresh**: Background loop every 24h, manual via POST /api/cache/refresh
- **Key endpoints**:
  - GET /api/cache/status - Overview of cached data
  - GET /api/cache/lookup/player?name=X&team_id=Y - Player lookup
  - GET /api/cache/lookup/team?name=X - Team lookup (club or national)
  - GET /api/cache/national-teams - All national teams
