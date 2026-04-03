# ReversePicks — Product Requirements Document

## Original Problem Statement
Web app remake of a sports analytics platform focusing on Sports Player Props (pass attempts, shots, points, assists, etc.). Users scan prop screenshots, an AI Vision model extracts the details, resolves the player/team, and runs a prediction pipeline using a 3-AI consensus engine.

## Core Architecture
- **Frontend**: React.js, Shadcn/UI
- **Backend**: FastAPI, Python asyncio, MongoDB
- **AI Engine**: 3-AI parallel consensus (Gemini 2.0 Flash, Grok 4.1 Fast, GPT-4.1-mini) at temperature=0

## What's Been Implemented

### Core Features (Complete)
- Image scanning pipeline (prop screenshot → AI extraction → player/team resolution)
- Soccer prediction pipeline (19 prop types, per-90 analysis, H2H, position comparison)
- Basketball prediction pipeline (17 prop types, pace analysis, matchup context)
- 3-AI parallel consensus engine with deterministic outputs
- Editable Scan Cards (pencil icon to override player/team/opponent/position/role)
- Auto-caching Team Resolver (266+ teams across 14 leagues on startup)
- Position Validation (auto-corrects impossible combos like GK-Fullback)
- Live game tracking (updated every 30s)
- Won/Missed tabs (replaced old History tab)
- Square Checkout subscription integration
- Admin Settings panel
- Cross-country league detection (Copa Libertadores/Sudamericana)

### Match Dominance Engine + Possession Model + GPT Fix (Completed April 3, 2026)
- **Opponent-Aware Possession Model**: Replaced simple historical averaging with formula: `base = (team_avg + (100 - opp_avg)) / 2`, adjusted for home advantage (+2.5%), standings quality gap (±4%), and odds-derived dominance (±7%). Produces accurate matchup-specific possession predictions.
- **Match Dominance Multiplier**: Calculates how expected possession divergence from season average should adjust projections. Pass-dependent props (pass_attempts, key_passes, crosses) get boosted when expected possession is above average; defensive props scale inversely.
- **GPT Minutes Double-Count Fix**: Added explicit PREDICTION_SYSTEM rule: "NEVER double-count minutes. If data shows 43 passes in 26 minutes, the 43 IS the actual output." Prevents GPT from re-scaling by minutes.
- **Match Context Override in AI Prompt**: Injected [MATCH DOMINANCE ANALYSIS] section with explicit instructions that AIs must raise/lower projections when match context predicts significant possession advantage/disadvantage.
- **Frontend**: ProjectionCard shows Match Dominance indicator (expected possession vs avg, old→new projection, multiplier).

### Stats-Aware Position Resolver + Force-3-Model Consensus (Completed April 3, 2026)
- **Stats-Aware Position Resolution**: Position resolver now extracts player's actual season stats (tackles, blocks, aerial duels, passes, key passes, dribbles, shots, goals) and feeds them to the AI. Stats evidence makes CB vs LB misclassification impossible — a CB with 0 crosses and high blocks can't be confused with a fullback.
- **Dual-AI Validation for Defenders**: For players categorized as "Defender" by API-Football, both Grok + Gemini resolve positions in parallel. If they disagree, stats heuristics break the tie (high tackles+blocks = CB, high key passes+dribbles = fullback).
- **30-Day Cache Expiry**: Position cache now expires after 30 days. Legacy entries without timestamps cleared — all positions re-resolved with the new stats-aware system on next prediction.
- **Force-3-Model Consensus**: Changed from "first-2-wins" to "all-3-required" with automatic retry for any model that fails. Both Soccer and Basketball pipelines now wait for all 3 AIs (Gemini + Grok + GPT) before merging, with a single retry for failures, staying within the 48s K8s deadline.
- **Auto-trigger on settlement**: When a pick settles as a miss (soccer or basketball), background 3-AI postmortem runs automatically
- **Auto-trigger on manual correction**: When user corrects a pick result to "miss", analysis fires
- **Auto-backfill**: When user views "Missed" tab, unanalyzed misses get triggered in background (cap 5)
- **Calibration pattern extraction**: Stores sport/propType bias data (avgError%, missCount, recentErrors)
- **Prediction pipeline injection**: Both soccer and basketball pipelines fetch calibration data before AI calls, inject calibration context into prompts, and apply percentage adjustments to projected values post-consensus
- **Frontend display**: Missed pick cards show inline auto-analysis (primaryReason, factors, calibrationSuggestions, modelsResponded). Prediction cards show calibration indicator when adjustment applied.
- **No manual buttons**: Entire system is autonomous — fire-and-forget background tasks

## Key API Endpoints
- `POST /api/scan-prop` — Vision extraction from prop screenshots
- `POST /api/predict` — Soccer prediction pipeline (with calibration)
- `POST /api/basketball/predict` — Basketball prediction pipeline (with calibration)
- `POST /api/re-resolve` — Re-resolve player after editable scan card changes
- `POST /api/picks/misses` — Get missed picks with auto-analysis
- `POST /api/picks/analyze-miss` — Manual miss analysis trigger (fallback)
- `POST /api/picks/save` — Save a pick for tracking
- `POST /api/picks/live-update` — Refresh live game data
- `POST /api/picks/correct` — Manual correction of actual value

## DB Collections
- `picks` — User picks with settlement data
- `miss_analyses` — 3-AI postmortem results per missed pick
- `calibration_stats` — Aggregated bias patterns per sport+propType
- `calibration_patterns` — Individual miss calibration records
- `player_positions` — Cached position/role data
- `settings` — Admin config key-value store
- `users` — User auth and subscription data

## 3rd Party Integrations
- API-Sports (Sports Data) — User API Key
- Square (Payments/Subs) — User API Key
- xAI Grok (grok-4-1-fast-non-reasoning) — User API Key
- Gemini 2.0 Flash — Emergent LLM Key
- OpenAI GPT-4.1-mini — Emergent LLM Key

## Upcoming Tasks (P1)
- Slip correlation analysis — Analyze multiple saved picks for the same game to flag conflicting or boosting correlations

## Future Backlog
- Batch scan predictions (P2) — Support scanning multiple props from one image
- SofaScore Integration (P3) — Replace API-Sports for NWSL data
- App.js Component Splitting (P3) — Break down ~3000-line file into smaller components

## Legal Compliance
ALL 3rd-party app names and player/team images have been removed. Do not reintroduce them. Do not use explicit AI branding in the UI.
