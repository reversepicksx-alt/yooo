# ReversePicks — Product Requirements Document

## Original Problem Statement
Web app remake of a sports analytics platform focusing on Sports Player Props (pass attempts, shots, points, assists, etc.). Users scan prop screenshots, an AI Vision model extracts the details, resolves the player/team, and runs a prediction pipeline using a 3-AI consensus engine.

## Core Architecture
- **Frontend**: React.js, Shadcn/UI
- **Backend**: FastAPI, Python asyncio, MongoDB
- **AI Engine**: 3-AI parallel consensus (Gemini 2.0 Flash, Grok 4.1 Fast, GPT-4.1-mini) at temperature=0

## What's Been Implemented

### Core Features (Complete)
- Image scanning pipeline (prop screenshot -> AI extraction -> player/team resolution)
- Soccer prediction pipeline (19 prop types, per-90 analysis, H2H, position comparison)
- Basketball prediction pipeline (17 prop types, pace analysis, matchup context)
- 3-AI parallel consensus engine with deterministic outputs
- Editable Scan Cards, Auto-caching Team Resolver (266+ teams across 14 leagues)
- Position Validation, Live game tracking (30s), Won/Missed tabs
- Square Checkout subscription + Whop subscription (both active)
- Admin Settings panel, Cross-country league detection

### Square Subscription Sync Fix (April 3, 2026) — P0
- Enhanced webhook: handles payment.completed, order.updated/completed, invoice.payment_made
- Self-recovery: "Already paid? Verify your payment" on login (searches Square payment history)
- Admin tools: admin/activate, admin/bulk-verify endpoints
- All 16 paying customers activated. Access type shows source: "Premium (Square)" / "Premium (Whop)"

### AI Contradiction Fix (April 3, 2026) — P0
- Calibration/dominance can no longer flip unanimous AI consensus (all 3 models agree)
- Applied to both soccer and basketball prediction pipelines

### Opponent Resolution Enhancement (April 3, 2026) — P0
- Added SCAN_ALIASES: 80+ team abbreviations (Bundesliga, PL, La Liga, Serie A, Ligue 1, MLS, Liga MX)
- Enhanced _generate_aliases: city name extraction from compound names, "utd"<->"united" expansion
- Added @ / vs prefix stripping in _resolve_opponent
- Query expansion in find_team: tries abbreviation variants automatically
- **70/70 resolution tests passed** (55 find_team + 15 _resolve_opponent with real scan formats)

### Self-Learning Calibration System (April 3, 2026)
- Automatic 3-AI post-mortem for missed picks, calibration pattern extraction

### Component Split Refactor (April 3, 2026)
- App.js reduced from 3,328 to 2,441 lines (27% reduction)

### Competition Detection Fix (April 4, 2026) — P0
- Rewrote `get_match_odds()` to use team's next 10 fixtures across ALL competitions
- H2H demoted to fallback-only with correct `next: 2`
- Added `tacticalBreakdown` display to `ProjectionCard.jsx`
- Added `matchContext` badge showing competition name + round
- **Testing**: 100% pass — verified via testing agent (iteration 48)

### Subscription Plan Management (April 4, 2026) — P0
- Backend: `POST /api/square/change-plan` — cancel+recreate approach
- Frontend: SubscriptionManager in ProfileTab
- **Testing**: 100% pass — verified via testing agent (iterations 47, 48, 49)

### Multi-Prop Inline Predictions (April 5, 2026) — P0
- Refactored `scanPrediction` from single object to index-keyed map `{ idx: result }`
- Each scanned prop card shows prediction results inline (projected value, recommendation, confidence, match context)
- Each predicted card has its own SAVE TO TRACKING button — saves independently without navigating away
- PREDICT ALL button appears above prop list when 2+ props detected
- Removed old "Full Analysis View" navigation — prop cards always stay visible
- Cleaned up dead code: sendScanFollowUp, isScanPredicting, backToScanResults

### Cartoon Sport Toggle (April 5, 2026) — UI Enhancement
- Custom SVG cartoon ball icons (no faces — regular balls, cartoon-styled with bold outlines)
- Soccer ball: classic black/white pentagon pattern, pulsing glow when active
- Basketball: orange with seam lines, pulsing glow when active
- Both scale up with spring bounce when selected

### Cartoon Glow Theme (April 5, 2026) — Full UI Overhaul
- Replaced Manrope font with Nunito (rounder, friendlier, cartoon-appropriate)
- All borders green-tinted (`rgba(16,185,129,...)`) instead of white/grey — cards, inputs, badges, nav, buttons
- Added `--card-glow` CSS variable: subtle green glow `box-shadow` on all cards and containers
- Bottom nav active state: filled pill-shape with green-tinted background + green icon/text glow
- Tab switcher active state: green-tinted background with green text + text-shadow glow
- Buttons: luminous green glow with `box-shadow: 0 0 20px` on primary buttons
- Text headings: `text-shadow` for depth/pop effect
- Spring-bounce transitions (`cubic-bezier(0.34, 1.56, 0.64, 1)`) on interactive elements
- Login page: green-bordered inputs, glowing VERIFY ACCESS button

### Header Mobile Layout Fix (April 5, 2026) — P0
- Split header into two rows: Row 1 (logo + sport toggle + bell + logout), Row 2 (API + v2.3 + Refresh)
- Fixed horizontal overflow on mobile (430px) with overflow-x:hidden and max-width:100vw
- Refresh button moved from icon-only to labeled button in status row

### Click-to-Expand Prediction Review (April 5, 2026) — P0
- Added `scanExpandedIdx` state to track which prediction card is expanded
- Tapping an inline prediction result toggles full review: ProjectionCard + H2H games + Tactical Breakdown
- Chevron indicator rotates on expand/collapse, "Tap for full breakdown" hint text

### Subscription Visibility Fix (April 5, 2026) — P0
- Removed `isSquareUser` gate — SubscriptionManager now renders for ALL users
- Component auto-hides if no active Square subscription found (via API check)
- Owner and non-Square users see nothing; Square subscribers see full management UI

## 3rd Party Integrations
- API-Sports — User API Key | Square — User API Key | Whop — User API Key (still active)
- xAI Grok — User API Key | Gemini 2.0 Flash — Emergent LLM Key | OpenAI GPT-4.1-mini — Emergent LLM Key

### Header Slim Redesign (April 4, 2026) — UI Fix
- Collapsed header from 2 rows to 1 slim row
- Mobile (≤480px): hides "ReversePicks" text + sport label text, shows icon-only sport toggles
- API dot + v2.3 version integrated inline next to logo instead of separate status row
- Removed Refresh button (pull-to-refresh native on mobile)
- Reduced icon-btn size from 36px to 32px, logo-icon from 36px to 30px
- Desktop still shows full text labels

### API Rate Limit & AI-Only Mode Fix (April 5, 2026) — P0 Critical
- **Bug 1**: API-Sports daily quota error ("request limit") wasn't detected because code only checked for "rate limit" → now detects both, returns empty [] instead of crashing with 400
- **Bug 2**: Hard guards blocked predictions when teamId=0 or opponentId=0, even though frontend allows "AI-ONLY MODE" → removed guards, predictions now gracefully skip API calls and use pure AI analysis
- **Fix**: AI-only mode skips all API data fetching (no wasted quota on fake IDs), proceeds directly to LLM consensus with player name + team + opponent + prop type

### Recommendation vs Projection Mismatch Fix (April 5, 2026) — P0 Critical Bug
- **Bug**: When match dominance multiplier adjusted projectedValue upward (e.g., 49→53.3), the recommendation was still set using the OLD pre-adjustment value (`avg_proj`), causing "projecting 53.3 but recommending UNDER 50.5"
- **Root cause**: Line `prediction["recommendation"] = "over" if avg_proj > req.line` used stale `avg_proj` instead of `prediction["projectedValue"]` (post-dominance)
- **Fix**: Changed to use `prediction.get("projectedValue", avg_proj)` — recommendation now always matches the FINAL projected value

### Prediction Calibration Overhaul (April 4, 2026) — P0 Quality Fix
- **Root cause**: Three bad reads (Romagnoli passes, Keita+Taylor combo, Britschgi shots assisted) — AI models were recommending UNDER without proper calibration guards
- **Fix 1 — AI Prompt Calibration Rules**: Added UNDER skew warning (stats have positive skew), binary line rule (0.5 = zero required), tight edge rule (±1 margin = low confidence), defender pass calibration
- **Fix 2 — Over/Under Hit Rates**: Pre-compute actual hit rates from game logs (e.g., "OVER 70.5 in 5/7 games, 71%") and inject into AI prompt — gives models explicit data signal
- **Fix 3 — Post-Consensus Confidence Guards**: Binary line (0.5) UNDER capped at 55%, tight edge (±1) capped at 58%, UNDER skew penalty of 2-4%

### GK Saves Formula Overhaul (April 4, 2026) — P0 Logic Fix
- **Root cause**: Save rate was inflated to 76.3% because fallback assumed only 0.8 GA/game; `max(formula, avg)` always biased projections upward
- **Fix 1**: Compute GA directly from game log scores (score + venue) — most reliable source
- **Fix 2**: Fallback GA/game raised from 0.8 to 1.3 (league average); save rate capped at 50-80%
- **Fix 3**: Replaced `max(formula, avg)` with weighted blend (60% formula + 40% GK average) — formula can now project below GK's average when appropriate
- **Fix 4**: Symmetric context multiplier (±10% for underdog/favorite, was asymmetric -8/+15%)
- Example: Daniel Peretz (avg 2.57 saves) vs Arsenal → Old: 4.0 projected, New: ~2.7 projected (realistic)

### Duplicate Save Button Fix (April 4, 2026) — Bug Fix
- ProjectionCard's "Save to Tracking" button was showing alongside the inline blue "SAVE TO TRACKING" button when expanded
- Added `hideSave` prop to ProjectionCard, passed `hideSave={true}` from scan inline context
- Only the blue inline save button renders now (no duplicate)

### Saudi Pro League Resolution (April 4, 2026) — P0 Verified
- Added SCAN_ALIASES for all Saudi Pro League teams (Al-Hilal, Al-Nassr, Al Taawon, etc.)
- Enhanced `_generate_aliases` to strip "Al-" prefixes and trailing qualifiers (saudi fc, jeddah, etc.)
- All 9 test variants verified: Taawoun→Al Taawon, Hilal→Al-Hilal Saudi FC, etc.

## Upcoming Tasks (P1)
- Slip correlation analysis

## Future Backlog
- ScanTab extraction from App.js (P3)
- Backend function refactoring (P3)
- Auth cookie migration (P3)
- Batch Scan for all users (P3)

## Legal Compliance
ALL 3rd-party app names and player/team images removed. No explicit AI branding in UI.
