# ReversePicks — Soccer Player Props Analytics

## Project Overview

ReversePicks is a premium soccer player props analytics platform. It combines a FastAPI + MongoDB backend with a React Native / Expo mobile frontend designed for App Store submission. The design features a pure black background with neon green (#39FF14) accents matching the RP crest logo.

## Critical: MongoDB Data Persistence

**Production DB path:** `/home/runner/.reversepicks_db` (outside workspace — immune to redeployments)
**Dev DB path:** `/home/runner/workspace/mongodb_data` (workspace, dev only)

The production path lives in the home directory, NOT inside `/home/runner/workspace/`. This means every time you redeploy (which updates the workspace), the database is completely untouched. User passwords, picks, subscriptions — all safe forever.

`start.sh` includes a one-time migration: if the new path is empty but the old workspace path has data, it copies it over automatically on first boot.

## Architecture

```
/
├── backend/          # FastAPI + MongoDB API server (port 8000, internal only)
│   ├── server.py     # Main FastAPI app with startup events
│   ├── grok_engine.py
│   ├── calibration.py
│   ├── team_resolver.py
│   └── routes/       # intel, picks, auth, etc.
│
├── mobile/           # Expo React Native app
│   ├── proxy.js          # Express proxy: port 5000 → /api to :8000, /* to :5001
│   ├── app/
│   │   ├── _layout.tsx        # Root layout with AuthContext
│   │   ├── auth.tsx           # Login screen (logo + 2-step auth)
│   │   └── (tabs)/            # Tab navigator screens
│   │       ├── scan.tsx       # Scan/Predict tab
│   │       ├── picks.tsx      # Picks tab
│   │       ├── intel.tsx      # Intel tab
│   │       ├── chat.tsx       # Tactical Chat tab
│   │       └── account.tsx    # Account tab
│   ├── contexts/AuthContext.tsx
│   ├── lib/api.ts             # API client (relative URLs on web, env var for native)
│   ├── constants/colors.ts    # Pure black + neon green (#39FF14) theme
│   ├── assets/logo.png        # RP crest logo
│   └── babel.config.js
│
└── frontend/         # Legacy React web frontend (not active)
```

## Port Architecture (IMPORTANT)

- **Port 5000** (public) → `mobile/proxy.js` (Express reverse proxy)
  - `/api/*` → `http://localhost:8000` (FastAPI backend)
  - `/*` → `http://localhost:5001` (Expo Metro dev server)
- **Port 5001** (internal) → Expo Metro dev server
- **Port 8000** (internal) → FastAPI + uvicorn backend

This proxy architecture allows API calls from any device (mobile, desktop) to reach the backend without needing a separate public URL for port 8000.

## Key Configuration

- **Bundle ID**: `com.reversepicks.app`
- **Owner email**: `reversepicksx@gmail.com`
- **MongoDB**: localhost:27017, DB `reversepicks`, data at `/home/runner/.reversepicks_db` (BOTH dev workflow and production use this path — NEVER change back to workspace path or data will be wiped on redeploy)
- **API calls (web)**: Relative URLs `/api/...` — proxy handles routing
- **API calls (native)**: `EXPO_PUBLIC_API_URL` env var or localhost:8000 fallback
- **Design**: Background `#050505`, Primary `#39FF14` neon green, Cards `#111111`

## Workflows

- **Start Backend**: `mkdir -p /tmp/mongodb/data && (mongod ...) && sleep 5 && cd backend && uvicorn server:app --host 0.0.0.0 --port 8000`
- **Start application**: `cd mobile && (node proxy.js &) && node node_modules/expo/bin/cli start --web --port 5001`

## Auth Flow

Two-step auth:
1. `/api/auth/verify-access` — check email (owner, lifetime, manual access, Square sub)
2. Returns `requires_password_setup` (new user) or `requires_password` (returning) or `verified`
3. Password screen → `/api/auth/set-password` or `/api/auth/login`

## Subscription Management

The Account tab (`account.tsx`) includes full subscription management for Square subscribers:
- View plan details (name, price, status, next billing, card on file)
- Change plan (Weekly/Monthly/Quarterly) via `/api/square/change-plan`
- Cancel subscription via `/api/square/cancel` (access retained until billing period ends)
- Resubscribe after cancellation via `/api/square/resubscribe-checkout` (opens Square checkout)
- Lifetime/Owner users see "Lifetime Access" badge; Whop members see "Managed by Whop"
- API wrappers: `getSubscriptionStatus()`, `cancelSubscription()`, `changePlan()`, `resubscribeCheckout()` in `mobile/lib/api.ts`

No Whop integration — access control is:
- Owner email (`reversepicksx@gmail.com`) → always allowed
- `LIFETIME_SUB_EMAILS` env var → lifetime access
- Manual access grants in MongoDB
- Square subscription webhook data

## Dependency Notes

- `react-native-reanimated` pinned to `~3.16.1` (v4.x requires `react-native-worklets`)
- `babel-preset-expo` pinned to `~54.0.10`
- `express` + `http-proxy-middleware` in `mobile/` for the proxy server

## API-Football Reference

- **Documentation**: https://www.api-football.com/documentation-v3
- **Player IDs**: https://dashboard.api-football.com/soccer/ids/players
- **Team IDs**: https://dashboard.api-football.com/soccer/ids/teams
- **League IDs**: https://dashboard.api-football.com/soccer/ids
- **Base URL**: `https://v3.football.api-sports.io`
- **Key endpoints used**: `players`, `fixtures`, `fixtures/players`, `teams/statistics`, `standings`
- **Player stats per fixture**: `fixtures/players?fixture={id}` — gives passes.total, shots.total, goals.saves, etc.
- **Count stats** (pass_attempts, shots, tackles, etc.) must ALWAYS be whole numbers — never decimals

## Prediction Data Integrity

- Game logs MUST come from real API-Football fixture data (`fixtures/players` endpoint)
- No synthetic/fabricated game logs — if real data unavailable, use line as prior
- Projections for count stats are rounded to integers (no 23.1 pass attempts)
- Prediction caching: same player+prop+line+opponent returns same result for all users per day

## Possession Symmetry (CRITICAL)

- `compute_match_dominance()` always calculates from HOME team perspective first, then maps back
- This guarantees that analyzing ANY player in the SAME match produces IDENTICAL possession numbers
- The function normalizes inputs: if `is_home=False`, it swaps team/opp stats to home/away before computing
- Output includes `homePoss` and `awayPoss` (match-level, deterministic) plus `expectedPoss`/`oppExpectedPoss` (player-relative)
- The matchup overview uses `homePoss`/`awayPoss` directly for the possession bar display

## Player Team Resolution

- Player's CURRENT team is resolved from API-Football player stats (`statistics[-1].team.name`), not from scan input
- `req.teamName` (from scan/user) is only used as fallback when API data is unavailable
- This prevents stale club associations (e.g., Wan-Bissaka showing as Man United when he's at West Ham)

## LLM Integration Shim

`backend/emergentintegrations/` is a local shim (not on PyPI):
- Gemini models → `google-generativeai`
- OpenAI models → `openai` AsyncOpenAI
- xAI/Grok models → `openai` with `https://api.x.ai/v1` base URL
