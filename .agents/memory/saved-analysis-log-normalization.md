---
name: Saved analysis log normalization
description: Saved-pick analysis responses and live prediction responses expose recent logs under different shapes.
---

The saved-analysis endpoint and shared recent-history renderer must accept both normalized `gameLogs` and raw saved-analysis `playerGameLogs.games`, mapping the selected prop field into the display value before filtering. The endpoint should repair an empty prediction packet from the saved pick when possible.

**Why:** H2H was visible because it already read its saved-analysis shape directly, while recent player rows silently disappeared when the renderer only checked `gameLogs`.

**How to apply:** Keep response-shape compatibility at both boundaries: project both fields from saved analysis, repair an empty cached packet from the pick, and normalize venue/value aliases in the shared UI.