---
name: Half-line settlement
description: Player-prop half-lines cannot push; voided picks must be represented separately from true exact-value pushes.
---

Player-prop count stats settle to integer actuals, so a line ending in `.5` must resolve only to HIT or MISS. A true PUSH is reserved for an exact integer result on a whole-number line. Player DNPs, missing stats, and stale/unmatchable picks are VOID/DNP outcomes, not pushes.

**Why:** The settlement engine used generic equality and reused `push` for voids, which produced impossible alerts such as “Pass Attempts — PUSH” on a 30.5 line and obscured missing-stat outcomes.

**How to apply:** Use the shared numeric settlement rule in every sport/live/background/manual settlement path. Preserve `voidReason` and send/render DNP/VOID distinctly. Legacy UI records with `push + voidReason` or a half-line with no actual should be interpreted as DNP/VOID.