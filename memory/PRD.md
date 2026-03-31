# ReversePicks - Multi-Sport Player Props AI Analytics

## Problem Statement
Multi-sport player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players/teams, and runs a multi-AI consensus prediction pipeline. Supports Soccer, NBA, and WNBA with full live in-game tracking.

LEGAL: ALL 3rd-party app names and player/team images removed. No AI model names (Grok, Gemini, GPT, Claude) in any user-facing UI text.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Multi-Sport**: Sport selector (Soccer/Basketball). Basketball covers NBA+WNBA.
- **Data Cache**: All leagues, teams, players in MongoDB
  - Soccer: `cache.py` — leagues, teams, squads, national teams
  - Basketball: `basketball_cache.py` — NBA (31 teams, 744 players) + WNBA (15 teams, 253 players)
- **Prediction Pipeline**: 4-AI Consensus (Gemini 2.0 Flash, Grok 4.1 Fast, GPT-4.1-mini, Claude Haiku 4.5) temperature=0, 15s grace period
- **Live Tracking**: Real-time in-game stats (basketball Q1-Q4/OT, soccer 1H/2H)

## Key Files
- `/app/backend/routes/picks.py` — Unified live tracking (soccer + basketball), propType normalization
- `/app/backend/routes/basketball_predict.py` — Basketball prediction v2, advanced analytics engine
- `/app/backend/basketball_cache.py` — NBA+WNBA data cache
- `/app/backend/basketball_utils.py` — Basketball API helpers
- `/app/backend/routes/predict.py` — Soccer prediction
- `/app/backend/routes/scan.py` — Sport-aware OCR scan
- `/app/frontend/src/App.js` — UI with sport selector, tracking tab, live cards

## Supported Basketball Props
points, rebounds, assists, pts_reb_ast, pts_reb, pts_ast, reb_ast, blk_stl, steals, blocks, turnovers, three_pointers, fgm, ftm, fga, fta, tpa

## Critical Rules
- ALL identity fields (player.name, player.team, opponent) FORCE-SET from request data, never from AI output
- Opponent resolution ALWAYS filters by same league (NBA=12, WNBA=13)
- prediction["opponent"] = req.opponentName (force override, not setdefault)
- prediction["player"] = {...} from req data (force override, not setdefault)
- Live match detection: NO opponent name check needed (a team can only play one game at a time)
- Finished match detection: Uses opponent name + time proximity for accuracy
- Soccer fixture fetching: date=today + date=yesterday + last=3 in parallel, deduplicated by fixture ID
- No AI model names in ANY user-facing text
- temperature=0 on all AI models for deterministic output
- Do NOT reintroduce GPT-4o (15x cost drain) or Gemini 2.5 Flash (fails JSON)

## Completed Work
- Multi-sport (Soccer + Basketball/NBA/WNBA)
- 4-AI consensus + synthesis engine (~20-30s)
- Basketball data cache (46 teams, 997 players)
- Live in-game tracking (quarter, stats, pace, hit%)
- Tracking ID (TRK-XXXXXXXX) on every card
- Sport label on every card
- propType normalization (display labels -> internal keys)
- Advanced analytics engine v2 (per-minute rates, role classification, z-scores, overrides, consistency, streaks)
- Games tracked regardless of when pick was made
- Auto-settle on game finish
- Compound prop types: reb_ast, pts_reb, pts_ast, blk_stl (Mar 2026)
- Cross-league protection: NBA/WNBA opponent resolution filtered by league (Mar 2026)
- Identity field hardening: player/opponent/propType/line force-set from request data (Mar 2026)
- AI name scrubbing: All AI model names removed from user-facing text (Mar 2026)
- **Soccer live tracking fix**: date-based parallel fixture queries + no opponent check for live matches (Mar 2026)
- **Basketball live tracking fix**: same no-opponent-check-for-live logic (Mar 2026)

## Pending Issues
### P0: Soccer Odds / Moneyline
- Previous agent removed `season` filter from API call but never verified if odds data flows through
- Need to test soccer prediction and verify odds section populates

## Prioritized Backlog
### P1: Slip correlation analysis (multi-pick same game)
### P2: Prediction feedback loop, Batch scan predictions
### P3: SofaScore integration for NWSL
