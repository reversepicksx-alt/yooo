# ReversePicks — Product Requirements Document

## Problem Statement
Web app remake of a sports analytics tool for Soccer Player Props (pass attempts, shots, points, assists, etc.). Users scan prop screenshots, an AI Vision model extracts details, resolves the player/team, and runs a prediction pipeline.

**Legal**: NO 3rd-party app names, logos, team badges, or player headshots. No explicit AI branding.

## Core Architecture
- **Frontend**: React.js + Shadcn/UI
- **Backend**: FastAPI + Python asyncio + MongoDB
- **Prediction Engine**: Pure Bayesian math (100% of the projection number). Grok 3 Mini provides textual tactical reasoning only.
- **OCR**: Grok 4.1 Fast Non-Reasoning (Vision)
- **Synthesis**: Gemini 2.0 Flash (combines AI analyses into cohesive breakdown)

## Key Integrations
- API-Sports (Soccer data) — User API Key
- Square (Payments/Subs) — User API Key
- xAI Grok (grok-3-mini reasoning, grok-4-1-fast-non-reasoning vision) — User API Key
- Gemini 2.0 Flash — Emergent LLM Key

## What's Implemented
- Full Bayesian prediction pipeline with 10+ prop types
- OCR screenshot scanning via Grok Vision
- MongoDB caching layer (players, teams, leagues, national teams)
- Accent-stripped nameClean matching for teams (Bayern München ↔ Bayern Munich)
- First-initial matching for players (Joshua Kimmich → J. Kimmich)
- Consonant-skeleton matching for Arabic transliterations
- Word-overlap fuzzy matching for team names
- Auto-settlement bot (live score checking)
- Auto-backfill for player positions
- Calibration pattern mining
- Square subscription management
- Venue verification via fixture data
- Combo predictions (2-player same game)
- Live game tracking
- Prediction self-correction guard (coin-flip zone capping)

## Recent Fixes (Current Session)
1. **Grok model timeout**: Increased from 8s→20s in grok_engine.py with retry/fallback between models
2. **Bayesian fallback**: predict.py now uses Bayesian-only projection when ALL Grok models fail (no more crashes)
3. **opponentId optional**: PredictionRequest.opponentId defaults to 0 (no more 400 on null)
4. **nameClean for teams**: Added accent-stripped field to cache_teams with backfill (1230 teams)
5. **Model name fix**: Standardized grok-4-1-fast-non-reasoning across all files (was grok-4.1-fast in some)
6. **Removed basketball references**: Cleaned up AUTO-BACKFILL prompt

## Prioritized Backlog
### P1 - Important
- Slip correlation analysis: Analyze multiple saved picks for same game to flag conflicting/boosting correlations

### P2 - Nice to Have
- Route remaining API-Sports calls through MongoDB cache with TTL to avoid API rate limits
- Prediction self-correction feedback loop: Store outcomes and feed calibration patterns back
- Batch scan predictions: Support scanning multiple props from one image

### P3 - Future/Refactoring
- Frontend refactoring: Extract components from App.js (>2200 lines)
- Backend refactoring: Break down monolithic predict.py and scan.py
- Auth architecture migration: Move from localStorage to httpOnly cookies
- Integrate RapidAPI SofaScore for NWSL data

## Key API Endpoints
- `POST /api/scan-prop` — OCR & Resolution
- `POST /api/predict` — Prediction pipeline
- `POST /api/predict-combo` — Combo prediction
- `GET /api/leagues` — Supported leagues
- `GET /api/leagues/{id}/teams` — Teams by league
- `POST /api/players/search` — Player search

## DB Collections
- `cache_players`: Player roster (nameClean, teamId, leagueId)
- `cache_teams`: Team index (nameClean = accent-stripped, nameLower)
- `cache_leagues`, `cache_national`, `cache_transfers`, `cache_meta`
- `picks`: User predictions with settlement tracking
- `predictions`: Full prediction results
- `player_positions`: Cached position/role data
- `calibration_insights`: Pattern mining results
