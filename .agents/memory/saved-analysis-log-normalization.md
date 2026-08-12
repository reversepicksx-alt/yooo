---
name: Saved analysis log normalization
description: Saved-pick analysis responses and live prediction responses expose recent logs under different shapes.
---

The shared recent-history renderer must accept both normalized `gameLogs` and raw saved-analysis `playerGameLogs.games`, mapping the selected prop field into the display value before filtering.

**Why:** H2H was visible because it already read its saved-analysis shape directly, while recent player rows silently disappeared when the renderer only checked `gameLogs`.

**How to apply:** Keep response-shape compatibility at the shared UI boundary; do not require every saved-analysis endpoint to be rewritten into the live-prediction normalization format.