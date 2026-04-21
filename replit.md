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

## A-League & Fixture Player Cache

- A-League (`leagueId=188`) is in both `SUPPORTED_LEAGUES` and `PREFETCH_LEAGUES` (added Apr 2026)
- Player names with HTML entities (e.g. `j. o&apos;shea`) are decoded via `html.unescape()` in `backend/cache.py` before storing as `nameClean`
- Fixture player stats are cached as `fxp_{fixture_id}_{player_id}` docs in the `fixture_player_cache` collection
- `fetch_player_game_logs` (primary Wave 2 path) now checks `fixture_player_cache` FIRST before hitting the API, making it dramatically faster for pre-fetched leagues
- Wave 2 runs with a 40s timeout covering 6 concurrent tasks (team/opp fixture stats, player logs, LLM digest, situation engine, web intel); if LLM is slow the whole batch can be cancelled — cache hits eliminate this risk for player logs
- Fallback path at line ~1013 in predict.py also checks cache by player fixture ID when Wave 2 player_game_logs is empty

## Line-Deviation Intelligence Engine (calibration.py)

Added April 2026. Replaces hardcoded Guard 5 market-gap thresholds with data-driven calibration.

**Key functions:**
- `compute_line_deviation_bands(prop_type)` — queries settled picks, buckets by deviation %, returns learned hit rates
- `get_line_deviation_intel(line, projected_value, rec, prop_type)` — returns band, hit rate, confidence delta, note
- Cache TTL: 2 hours (`_dev_band_cache`). Min samples to trust learned rate: 8 picks per band.

**Deviation bands:** aligned (0-5%), mild (5-10%), moderate (10-15%), elevated (15-20%), extreme (20%+)

**Key finding from 177 settled pass_attempts picks (April 2026):**
- UNDER in elevated band (15-20% above model): **91.7% hit rate** (12 picks)
- UNDER in moderate band (10-15% above model): **88.9% hit rate** (9 picks)
- UNDER in mild band (5-10%): **73.7% hit rate** (19 picks)
- UNDER in extreme band (20%+): **63.0% hit rate** (46 picks)
- OVER in aligned band: **30.4% hit rate** (23 picks) — triggers ALIGNED CAUTION warning

**Confidence delta formula:** `(hit_rate - 50) * 0.5` applied to model's confidence score
- Moderate UNDER: +19 pts | Elevated UNDER: +21 pts | Extreme UNDER: +6 pts
- Aligned OVER (below 50% threshold): -10 pts warning

The old hardcoded Guard 5 (penalty for >15% deviation) was backward — data shows those bands are the MOST profitable UNDER situations.

## LLM Integration Shim

`backend/emergentintegrations/` is a local shim (not on PyPI):
- Gemini models → `google-generativeai`
- OpenAI models → `openai` AsyncOpenAI
- xAI/Grok models → `openai` with `https://api.x.ai/v1` base URL
