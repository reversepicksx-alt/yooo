# ReversePicks - Multi-Sport Player Props AI Analytics

## Problem Statement
Multi-sport player prop analysis app. Users scan prop screenshots, AI extracts details, resolves players/teams, and runs a multi-AI consensus prediction pipeline. Supports Soccer, NBA, and WNBA with full live in-game tracking.

LEGAL: ALL 3rd-party app names and player/team images removed. No AI model names (Grok, Gemini, GPT, Claude) in any user-facing UI text.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **Multi-Sport**: Sport selector (Soccer/Basketball). Basketball covers NBA+WNBA.
- **Prediction Pipeline**: 4-AI Consensus (Gemini 2.0 Flash, Grok 4.1 Fast, GPT-4.1-mini, Claude Haiku 4.5) temperature=0, 15s grace period
- **Live Tracking**: Real-time in-game stats (basketball Q1-Q4/OT, soccer 1H/2H)

## Critical Rules
- ALL identity fields (player, opponent) FORCE-SET from request data, never AI output
- Opponent resolution filters by same league (NBA=12, WNBA=13)
- Live match detection: NO opponent name check (team can only play one game at a time)
- Soccer fixtures: date=today + date=yesterday + last=3 in parallel, deduplicated
- No AI model names in user-facing text
- temperature=0 on all AI models
- Do NOT reintroduce GPT-4o or Gemini 2.5 Flash

## Supported Soccer Props (19)
goals, assists, shots_assisted, pass_attempts, shots, shots_on_target, tackles, key_passes, saves, interceptions, blocks, dribbles, dribbles_success, fouls_drawn, fouls_committed, crosses, clearances, duels_won, yellow_cards

## Supported Basketball Props (17)
points, rebounds, assists, pts_reb_ast, pts_reb, pts_ast, reb_ast, blk_stl, steals, blocks, turnovers, three_pointers, fgm, ftm, fga, fta, tpa

## Completed Work (Mar 2026)
- Compound basketball props: reb_ast, pts_reb, pts_ast, blk_stl, steals, blocks, turnovers
- Cross-league protection: NBA/WNBA opponent resolution filtered by league
- Identity field hardening: player/opponent force-set from request data
- AI name scrubbing from all user-facing text
- Soccer live tracking fix: date-based parallel fixture queries + no opponent check for live matches
- Basketball live tracking fix: same no-opponent-check-for-live logic
- Soccer prop types expanded: goals, assists, shots_assisted, fouls_committed, crosses, clearances, duels_won, yellow_cards, dribbles_success
- Scan combo prediction UI upgraded: full matchup overview, sharp takes, tactical breakdowns per player

## Pending Issues
### P0: Soccer Odds / Moneyline
- Previous agent removed `season` filter but never verified odds data flows through

## Prioritized Backlog
### P1: Slip correlation analysis (multi-pick same game)
### P2: Prediction feedback loop, Batch scan predictions
### P3: SofaScore integration for NWSL
