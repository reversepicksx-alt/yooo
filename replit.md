# ReversePicks — Soccer Player Props Analytics

## Overview

ReversePicks is a premium soccer player props analytics platform designed to provide data-driven insights for sports betting. It features a FastAPI and MongoDB backend supporting a React Native / Expo mobile frontend, intended for App Store deployment. The platform's core purpose is to analyze soccer player performance, predict prop outcomes, and offer tactical insights, leveraging advanced AI models and statistical calibration.

## User Preferences

- The mobile application's design should feature a pure black background with neon green (#39FF14) accents, matching the RP crest logo.
- The project should prioritize data integrity and persistence, especially for MongoDB data, by storing production data outside the workspace to ensure it's immune to redeployments.
- API quota management for external services like API-Football should be robust, including a circuit breaker to prevent excessive calls.
- Player search functionality should be flexible, allowing searches across all leagues initially, then auto-setting the league once a player is selected.
- Game logs for predictions must exclusively use real API-Football fixture data; no synthetic data is to be used.
- Projections for count statistics (e.g., pass attempts, shots) must always be rounded to whole numbers.
- Prediction caching should ensure that the same player, prop, line, and opponent return identical results for all users on a given day.
- The system must ensure possession numbers are consistently calculated from the home team's perspective for any player in the same match.
- Player's current team resolution should always prioritize the most recent data from API-Football.
- The AI architecture should primarily leverage Google Gemini 2.5 Pro, with xAI Grok as a fallback, especially for prediction synthesis and tactical chat. Gemini's JSON mode is preferred for guaranteeing parseable output.
- The Bayesian engine for momentum calculation must always process game logs sorted newest-first to ensure accurate decay weighting.
- AI-driven press intensity scores from Grok should override heuristic methods when available, providing more accurate input for bayesian projections.

## System Architecture

The project is structured into `backend/` (FastAPI, MongoDB) and `mobile/` (Expo React Native) directories. A legacy `frontend/` directory exists but is not active.

### UI/UX Decisions
- **Color Scheme**: Background `#050505`, Primary `#39FF14` (neon green), Cards `#111111`.
- **Mobile Design**: Utilizes Expo React Native with a tab navigator for key screens: Scan/Predict, Picks, Intel, Chat, and Account.
- **Login Flow**: Two-step authentication: email verification followed by password input or setup.

### Technical Implementations
- **Backend**: FastAPI server (`server.py`) with uvicorn, running internally on port 8000. Includes modules for AI engines (`grok_engine.py`), calibration (`calibration.py`), and team resolution (`team_resolver.py`).
- **Mobile Frontend**: Expo React Native app.
- **Proxy Server**: An Express.js proxy (`mobile/proxy.js`) runs on public port 5000, forwarding `/api/*` requests to the internal FastAPI backend (port 8000) and all other requests (`/*`) to the Expo Metro dev server (port 5001).
- **MongoDB**: Runs on `localhost:27017` with database `reversepicks`. Production data persists at `/home/runner/.reversepicks_db`.
- **API Client**: `mobile/lib/api.ts` handles API calls, using relative URLs for web and an environment variable (`EXPO_PUBLIC_API_URL`) or localhost fallback for native.
- **Auth Flow**: Uses `/api/auth/verify-access` and `/api/auth/set-password` or `/api/auth/login`.
- **Subscription Management**: Integrated into `account.tsx` with Square API wrappers for plan details, changes, cancellation, and resubscription.
- **Line-Deviation Intelligence Engine**: `calibration.py` computes line deviation bands based on settled picks, providing data-driven hit rates and confidence adjustments for "UNDER" and "OVER" scenarios.
- **AI Architecture**:
    - **Primary**: Google Gemini 2.5 Pro (prediction synthesis, tactical chat reasoning), Gemini Flash (web intel, data digest, smart scan, tactical chat synthesis).
    - **Fallback**: xAI Grok (prediction synthesis, web intel), Grok-4-1-fast (tactical chat reasoning).
    - **Vision**: Gemini Flash vision and Grok vision for smart scan (OCR).
    - **Position Resolution**: Both Grok and Gemini are used concurrently.
    - Uses Gemini's JSON mode for reliable output parsing.
- **Bayesian Momentum Engine**: The `bayesian_engine.py` is configured to correctly process game logs sorted newest-first, applying decay weights accurately.
- **CDM Inversion Layer (away CDM pass_attempts)**: Mirror of the GK Inverted Possession Model for deep midfielders — when the away team is pinned back (low expected possession) and/or expected to chase (home team favoured), the CDM becomes the build-up outlet and pass volume rises rather than falls. Stacks two effects (possession-based, cap +6%; game-script-based, cap +6%). Mode controlled by `CDM_INVERSION_MODE` env var (`off|shadow|live`, default `shadow`); shadow logs the would-be multiplier without changing the projection. Surfaces in the response under `bayesianMetrics.cdmInversion`.
- **AI-Driven Press Intensity**: `grok_engine.py` fetches an AI-rated press intensity score (0.0-1.0) for opponents using web search and knowledge, overriding heuristic methods for more accurate projections. This score is cached to optimize performance.
- **Player Team Resolution**: Prioritizes `statistics[-1].team.name` from API-Football for current team data.
- **Caching**: Player search results and fixture player stats are cached in MongoDB (`fixture_player_cache` collection) to reduce API calls and improve performance for pre-fetched leagues like A-League.
- **HTML Entity Decoding**: Player names with HTML entities are decoded before storing as `nameClean` in the cache.

### Feature Specifications
- **Scan / Predict Screen**: Two modes: "Scan" (image OCR via AI) and "Manual" (player name input).
- **Subscription Support**: Weekly, Monthly, Quarterly plans managed via Square.
- **Access Control**: Owner email, `LIFETIME_SUB_EMAILS` env var, manual MongoDB grants, and Square subscription webhooks.
- **Pick Card Delete (platform-split)**:
  - **Web**: a small `trash-outline` icon sits at the bottom-right of every pick card. It's rendered as a real DOM `<button>` (not `Pressable`/`TouchableOpacity`/`RectButton`) with `onClick` + `onPointerDown`/`onMouseDown`/`onTouchStart` `stopPropagation`, so the click fires reliably and never bubbles up to the parent `Pressable` that opens the analysis modal. Swipe-to-delete is disabled on web (`SwipeableRow` returns children directly when `Platform.OS === 'web'`) because react-native-gesture-handler's web shim is unreliable for nested touch handlers and browser users have no swipe affordance anyway. The "Tap for analysis" hint was removed (redundant — the whole card is tappable).
  - **Native iOS/Android (app store builds)**: keeps the iOS-Mail-style swipe-to-reveal `DELETE` button via `ReanimatedSwipeable` (`friction=1.5`, `leftThreshold=40`, `dragOffsetFromLeftEdge=6`, `overshootLeft=false`). Tap on the revealed `TouchableOpacity` fires the Cancel/Delete confirm `Alert`; light haptic on will-open. We deliberately do NOT auto-fire delete on `onSwipeableOpen` so users can swipe back to cancel. The `trackBar` under the OVER/UNDER pill is 6.5px tall with `borderRadius: 3.5` for a chunky-but-thin iOS look.
- **Pick Card Match Context**: Live and history pick cards display the final/live score and ball possession with explicit home/away team labels (e.g. `Bologna 0 – 4 Aston Villa`, `Poss 38% – 62%`). Backend persists `homeTeam`/`awayTeam`/`finalHomeGoals`/`finalAwayGoals`/`homePoss`/`awayPoss` on settle (and refreshes during live updates) via `_fetch_fixture_possession()` in `backend/routes/picks.py`, called from `_build_soccer_update`, the legacy `_settle_soccer_pick`, and `grok_engine.py` auto-settle paths. The subject team's name is colored green/red on settled picks to show win/loss at a glance. Possession is fetched from API-Football's `fixtures/statistics` "Ball Possession" stat. Mobile renders the score line only when orientation is trustworthy (either backend gave fixture-derived names, or `pick.venue` is `'home'`/`'away'`); legacy picks with unknown venue fall back gracefully (no score line) rather than risking a wrong winner color. A one-shot `backend/scripts/backfill_match_meta.py` script (strategies: date+league+season, h2h, team-last) backfilled 526/542 historical settled picks with full metadata.
- **Projected Possession Tracking**: When a pick is saved, mobile (`scan.tsx#handleSavePick`) passes the model's pre-match `expectedPossession.home`/`away` as `projHomePoss`/`projAwayPoss`; backend (`picks.py#save_pick`) persists them as numeric percentages 0-100. PickCard renders a separate "Proj X% – Y%" line under the actual possession line, with a delta tag (≤3pt = green, ≥8pt = red) when both are present — surfaces directional edge signals where the model's possession projection was off vs reality.
- **Confidence Badge on Pick Cards**: Every live + settled pick card shows a tiny pill near the OVER/UNDER recommendation displaying e.g. `85% STRONG`, color-coded by `confidenceLevel` (Strong/High = green, Weak/Low = neutral, else = primary green). Pulls from `pick.confidence` (0-1 or 0-100, normalized) and `pick.confidenceLevel`.
- **Track Bar Thickness**: The OVER/UNDER hit-rate bar under each pick was thinned from 10px → 6.5px (35% reduction) with proportional `borderRadius: 3.5` and `marker: 1.5px` for a subtler, less dominant visual.
- **All-Leagues Fuzzy Picker**: Both league pickers in `mobile/app/(tabs)/scan.tsx` (manual mode + scan correction) use a reusable `mobile/components/LeaguePickerModal.tsx` that shows the popular 8 LEAGUES shortlist when the search box is empty and switches to full-cache fuzzy results from `/api/leagues/search` (1228 cached leagues, including women's leagues like NWSL Women, A-League Women, etc.) once the user types ≥2 chars. 280ms debounce and last-query guard prevent stale results from races.

## External Dependencies

- **MongoDB**: Primary database for all application data.
- **API-Football**: Source for soccer player statistics, fixtures, teams, and standings data.
- **Google Gemini API**: Used for AI models (Gemini 2.5 Pro, Gemini Flash) for prediction synthesis, web intelligence, data digestion, smart scan, and tactical chat.
- **xAI Grok API**: Used as a fallback for AI models and specifically for web search preview, vision capabilities, and AI-driven press intensity.
- **Square API**: For subscription management and payment processing.
- **Expo**: Framework for building the React Native mobile application.
- **React Native Reanimated**: Animation library.
- **Express.js**: Used in `mobile/proxy.js` for the reverse proxy.
- **http-proxy-middleware**: Middleware for the Express proxy.