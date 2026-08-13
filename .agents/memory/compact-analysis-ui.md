---
name: Compact analysis UI
description: Confirmed presentation preference for prediction analysis, H2H controls, and tactical explanation architecture.
---

Analysis should feel like a dense stats interface, not a stack of dashboard cards. The active live and saved-pick layouts use compact horizontal-scroll vertical bars for up to 20 Recent Matches and H2H rows, with verified possession labels, tap-to-inspect details, and haptic selection feedback. Avoid large tactical verdict, formula, or duplicate evidence cards.

**Why:** The user explicitly rejected the previous block-heavy presentation and approved the compact prediction → recent bars → H2H bars sequence for both live and saved analysis.

**How to apply:** Keep the shared compact bar renderer consistent across scan and saved-pick history. Preserve verified data meaning; never fabricate possession, keep older verbose renderers out of the active presentation, reuse exact fixture IDs when enriching H2H possession, mark every bar H/A, venue-highlight the prediction side in Recent and H2H, and reject partial tactical placeholders in favor of a complete deterministic read.

Recent Match venue summaries should stay inline and numeric (`H n · A n`, with optional compact averages) rather than rendering separate HOME/AWAY metric blocks. Model Source should explain its relationship to calibrated probability in short text lines, not another large card.

**Why:** The mobile analysis screenshot became visually block-heavy and made the player-history source, calibrated probability, and evidence confidence look interchangeable.

**How to apply:** Keep Recent Matches focused on bars; reserve full cards for the actual history/probability evidence. Define calibrated probability as the final line-clearing probability after projection context and settled calibration; define evidence confidence separately as data-quality strength.

H2H evidence should remain a compact comparison strip rather than a second full-height chart: retain the stat, date, possession, and H/A marker, but omit repeated opponent labels and expanded explanatory legends when the opponent is already the card context.

**Why:** The previous H2H block consumed most of the analysis viewport while repeating context already present in the card header; the useful evidence is the meeting value and its provenance markers.

**How to apply:** Keep H2H height close to the bar content itself and use tap selection only where it adds information without restoring a large detail panel.

Recent Matches, player-history/model-source numbers, and H2H should share one bordered history card. When a fixture venue is known, the Recent Matches chart must show only that venue’s verified player matches, while the compact header may summarize both archive counts.

**Why:** The user wants the selected home/away sample immediately visible without stacking separate history and H2H cards that push the useful bars below the fold.

**How to apply:** Render the venue-filtered chart first, add the small history-rate row and one-line calibration note beneath it, then place H2H as a subsection in the same card. Keep venue labels explicit when the selected sample is home or away.

Analytics hit rates must display their scored numerator/denominator and excluded outcome counts; never let a small daily rate read like it applies to every headline pick.

**Why:** A Today screenshot showed 90.9% beside 15 picks, but the rate was 10/11 HIT/MISS rows while four non-directional outcomes were excluded, and one scored row had no recognized OVER/UNDER direction.

**How to apply:** Label totals as settled/events versus scored, show `hits / (hits + misses)`, and surface DNP/unknown rows plus directionless scored records.

## Venue marker layout (bars)

`H` / `A` venue marker appears as its own `<Text>` element (`styles.venueLabel`) directly beneath the `possessionLabel` row — NOT beside the abbreviated opponent name. Both Recent Matches and H2H bars follow this layout. Color: green for home, blue for away.

H2H bars must reserve enough horizontal space for the full `YYYY-MM-DD` date and the combined possession/venue line, with both rows forced to one line.

**Why:** A narrow H2H column wrapped those rows into the same fixed-height area, making valid dates and H/A markers look corrupted or overlapped on mobile.

**How to apply:** Keep H2H columns materially wider than Recent Matches columns, use one-line clipped text for date and metadata, and size the horizontal scroll content to the wider columns.

## Prop History card (replaces Tactical Read / AI explanation)

Gemini tactical explanation was removed entirely. No AI calls happen on prediction.

Where "TACTICAL READ" used to appear (scan.tsx predict view and picks.tsx analysis modal), a **PROP HISTORY** card now shows:
1. Two stat boxes — OVER RATE (green) and UNDER RATE (red) from `playerGameLogs.hitRates` (`overPct`, `underPct`, `overHits`, `underHits`, `total`)
2. System-wide accuracy line from `propHistoricalRate` (e.g., "UNDER PASS ATTEMPTS picks: 58% accuracy system-wide")
3. Deviation band accuracy from `lineDeviationHitRate` ("This deviation band: 61% historical hit rate")

Card is hidden if all three data sources are absent. `backend/compact_explanation.py` file still exists but `build_compact_explanation` is no longer called from `predict.py` — the import line is commented out and the call block removed.

**Why:** User explicitly removed Gemini/AI tactical text. Hit-rate data is more trustworthy and directly useful than AI-generated tactical prose.
