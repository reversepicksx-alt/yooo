---
name: Compact analysis UI
description: Confirmed presentation preference for prediction analysis, H2H controls, and tactical explanation architecture.
---

Analysis should feel like a dense stats interface, not a stack of dashboard cards. The active live and saved-pick layouts use compact horizontal-scroll vertical bars for up to 20 Recent Matches and H2H rows, with verified possession labels, tap-to-inspect details, and haptic selection feedback. Avoid large tactical verdict, formula, or duplicate evidence cards.

**Why:** The user explicitly rejected the previous block-heavy presentation and approved the compact prediction → recent bars → H2H bars sequence for both live and saved analysis.

**How to apply:** Keep the shared compact bar renderer consistent across scan and saved-pick history. Preserve verified data meaning; never fabricate possession, keep older verbose renderers out of the active presentation, reuse exact fixture IDs when enriching H2H possession, mark every bar H/A, venue-highlight the prediction side in Recent and H2H, and reject partial tactical placeholders in favor of a complete deterministic read.

## Venue marker layout (bars)

`H` / `A` venue marker appears as its own `<Text>` element (`styles.venueLabel`) directly beneath the `possessionLabel` row — NOT beside the abbreviated opponent name. Both Recent Matches and H2H bars follow this layout. Color: green for home, blue for away.

## Tactical Read / explanation

The user requires genuine soccer tactical reasoning — not template-filled boilerplate. Key design decisions:

- Cache version: `compact-v3-tactical` (bumped from v2-longform and v1). Any new explanation change **must** bump this to prevent stale cached text from being reused.
- Target length: **~500 words** (`_MIN_WORDS=380`, `_MAX_WORDS=600`, `max_output_tokens=900`).
- Prompt strategy: Gemini is told explicitly WHO the player is, WHAT team, WHAT opponent, and instructed to **use its real-world knowledge** as the primary foundation. The evidence block provides only the specific numbers (projection, line, baseline, momentum, home/away split, opponent allowed avg) as anchors. The old "use only the JSON evidence" instruction was the root cause of generic template output.
- Evidence block (not full JSON dump): `Player`, `Team vs Opponent`, `Line/Projection/Verdict`, `Baseline/Momentum`, `Home avg/Away avg/Sample`, `Opponent allowed avg if available`, `H2H appearances if any`, `Limitations if any`.
- Fallback: `_fallback()` produces a clean 4-paragraph deterministic explanation using real names and numbers (not "unavailable" placeholders). It does NOT need to pass `_longform_usable()` — that check is only for Gemini output.
- `opponentProfile.allowedAverage` in the evidence packet now checks `avgAllowed`, `allowedAvg`, AND `allowedAverage` (mapping fix).
- All sports get the fallback (deterministic). Only soccer goes through Gemini generation.
