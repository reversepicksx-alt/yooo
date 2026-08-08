---
name: Compact analysis UI
description: Confirmed presentation preference for prediction analysis and H2H controls.
---

Analysis should feel like a dense stats interface, not a stack of dashboard cards. The active live and saved-pick layouts use compact horizontal-scroll vertical bars for up to 20 Recent Matches and H2H rows, with verified possession labels, tap-to-inspect details, and haptic selection feedback. Avoid large tactical verdict, formula, or duplicate evidence cards.

**Why:** The user explicitly rejected the previous block-heavy presentation and approved the compact prediction → recent bars → H2H bars sequence for both live and saved analysis.

**How to apply:** Keep the shared compact bar renderer consistent across scan and saved-pick history. Preserve verified data meaning; never fabricate possession, keep older verbose renderers out of the active presentation, reuse exact fixture IDs when enriching H2H possession, mark every bar H/A, venue-highlight the prediction side in Recent and H2H, and reject partial tactical placeholders in favor of a complete deterministic read.