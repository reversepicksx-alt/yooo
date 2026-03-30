# ReversePicks - Sports Player Props AI Analytics

## Problem Statement
A web app for soccer player prop analysis. Users upload screenshots of player props (pass attempts, shots, saves), AI Vision extracts details, resolves the player via cache-first MongoDB lookup, and runs a dual-AI prediction pipeline (Grok-4.20-reasoning + Gemini 2.5 Flash) to deliver statistical predictions and deep tactical analysis.

## Architecture
- **Frontend**: React.js, Shadcn/UI, Lucide Icons, Manrope + JetBrains Mono fonts
- **Backend**: FastAPI, Python asyncio, MongoDB
- **AI Pipeline**: Grok-4.20 (web search + tactical) → Gemini 2.5 Flash (calibration + JSON synthesis)
- **Auth**: Whop membership verification + email/password login
- **Data**: API-Sports (cached in MongoDB)

## 3-Tab Navigation
1. **Scan** — Upload prop screenshot → AI Vision extraction → Player resolution → Unified prediction + tactical breakdown + follow-up chat
2. **Tracking** — Live/History pick tracking with Record Tracker (HIT/MISS/PUSH/WIN%/STREAK)
3. **Profile** — Account info (email, access level, total picks), password reset, app version, logout

## Key Features Implemented
- [x] Cache-first player resolution (MongoDB)
- [x] Combo prop support (multi-stat detection)
- [x] International context awareness (National Team vs Club stats)
- [x] Unified Scan + Tactical pipeline (single API response)
- [x] Push status tracking (amber borders for ties)
- [x] User Record Tracker (HIT/MISS/PUSH ratio, WIN%, streak)
- [x] Profile tab with password reset
- [x] 3-tab iOS-like navigation
- [x] Follow-up chat under predictions
- [x] VIP/Lifetime hardcoded emails

## VIP Emails (Lifetime Access)
- josselj001@gmail.com (Owner)
- letwins04@gmail.com
- Quon.qg@gmail.com
- Jesselopezj@hotmail.com
- jaredlee0414@gmail.com
- michael1069_6910@yahoo.com

## AI Pipeline (v2.2 - Peak Calibration)
### Grok-4.20-reasoning
- 5-phase analysis: Verified Stats → Live Intelligence → Matchup Dynamics → Scenario Modeling → Edge Assessment
- Web search for real per-game stats from SofaScore/FotMob/WhoScored
- 4 weighted scenarios (Base/Blowout/Trailing/Cagey)
- Sensitivity classification (ROBUST/MODERATE/FRAGILE)
- 3000 max output tokens

### Gemini 2.5 Flash (Final Calibration Engine)
- 5-step protocol: Anchor on Evidence → Position-Calibrated Ceilings → Bayesian Calibration → Edge Detection → Probability Curve
- Bayesian metrics: priorMean (recency-weighted), momentumEffect, covariateAdjustment, reversalFlag
- Confidence bands: coin flip (<0.3 diff) max 52%, low edge (<0.8) max 62%, strong edge (>=1.5) 70-85%
- 10-point probability distribution with normal distribution

### Tactical Breakdown Synthesis
- 6-section format: Verdict → Player Role Analysis → The Numbers → Game Script Scenarios → Risk Radar → TL;DR
- Position-specific ceilings enforced
- No AI branding exposed to user

## Key API Endpoints
- POST /api/scan-prop — OCR extraction + player resolution
- POST /api/predict — Unified stats + tactical generation
- POST /api/tactical/message — Follow-up chat
- POST /api/auth/verify-whop — Membership verification
- POST /api/auth/reset-password — Password reset
- POST /api/picks/save — Save to tracking
- POST /api/picks/list — Get user picks

## Prioritized Backlog
### P2 (Medium)
- Slip correlation analysis - Analyze multiple picks for conflicting/boosting patterns
- Prediction self-correction feedback loop - Store outcomes, feed calibration back

### P3 (Low)
- Batch scan predictions - Multiple props from one image
- RapidAPI SofaScore integration (NWSL data)
