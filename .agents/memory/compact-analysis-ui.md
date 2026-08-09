---
name: Compact analysis UI
description: Confirmed presentation preference for prediction analysis, H2H controls, and tactical explanation architecture.
---

Analysis should feel like a dense stats interface, not a stack of dashboard cards. The active live and saved-pick layouts use compact horizontal-scroll vertical bars for up to 20 Recent Matches and H2H rows, with verified possession labels, tap-to-inspect details, and haptic selection feedback. Avoid large tactical verdict, formula, or duplicate evidence cards.

**Why:** The user explicitly rejected the previous block-heavy presentation and approved the compact prediction → recent bars → H2H bars sequence for both live and saved analysis.

**How to apply:** Keep the shared compact bar renderer consistent across scan and saved-pick history. Preserve verified data meaning; never fabricate possession, keep older verbose renderers out of the active presentation, reuse exact fixture IDs when enriching H2H possession, mark every bar H/A, venue-highlight the prediction side in Recent and H2H, and reject partial tactical placeholders in favor of a complete deterministic read.

H2H evidence should remain a compact comparison strip rather than a second full-height chart: retain the stat, date, possession, and H/A marker, but omit repeated opponent labels and expanded explanatory legends when the opponent is already the card context.

**Why:** The previous H2H block consumed most of the analysis viewport while repeating context already present in the card header; the useful evidence is the meeting value and its provenance markers.

**How to apply:** Keep H2H height close to the bar content itself and use tap selection only where it adds information without restoring a large detail panel.

## Venue marker layout (bars)

`H` / `A` venue marker appears as its own `<Text>` element (`styles.venueLabel`) directly beneath the `possessionLabel` row — NOT beside the abbreviated opponent name. Both Recent Matches and H2H bars follow this layout. Color: green for home, blue for away.

## Prop History card (replaces Tactical Read / AI explanation)

Gemini tactical explanation was removed entirely. No AI calls happen on prediction.

Where "TACTICAL READ" used to appear (scan.tsx predict view and picks.tsx analysis modal), a **PROP HISTORY** card now shows:
1. Two stat boxes — OVER RATE (green) and UNDER RATE (red) from `playerGameLogs.hitRates` (`overPct`, `underPct`, `overHits`, `underHits`, `total`)
2. System-wide accuracy line from `propHistoricalRate` (e.g., "UNDER PASS ATTEMPTS picks: 58% accuracy system-wide")
3. Deviation band accuracy from `lineDeviationHitRate` ("This deviation band: 61% historical hit rate")

Card is hidden if all three data sources are absent. `backend/compact_explanation.py` file still exists but `build_compact_explanation` is no longer called from `predict.py` — the import line is commented out and the call block removed.

**Why:** User explicitly removed Gemini/AI tactical text. Hit-rate data is more trustworthy and directly useful than AI-generated tactical prose.
