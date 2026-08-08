---
name: Compact analysis UI
description: Confirmed presentation preference for prediction analysis and H2H controls.
---

Analysis should feel like a dense stats interface, not a stack of dashboard cards. The active live and saved-pick layouts use compact horizontal-scroll vertical bars for Recent Matches and H2H, with verified possession inside H2H bars and `POSS N/A` when unavailable. Avoid large tactical verdict, formula, or duplicate evidence cards.

**Why:** The user explicitly rejected the previous block-heavy presentation and approved the compact prediction → recent bars → H2H bars sequence for both live and saved analysis.

**How to apply:** Keep the shared compact bar renderer consistent across scan and saved-pick history. Preserve verified data meaning; never fabricate possession, and keep older verbose renderers out of the active presentation.