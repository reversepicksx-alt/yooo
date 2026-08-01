---
name: AI direction reconciliation
description: How to handle Gemini tactical prose when the final Bayesian pass changes the prediction direction.
---

The final Bayesian direction is authoritative, but a direction mismatch must reconcile the Gemini narrative rather than erase it. Preserve substantive matchup, role, manager, possession, game-flow, and recent-game evidence; replace direction-bearing Verdict/TL;DR text with the completed model call and add an explicit reconciliation note. Keep `aiSource` as Gemini when substantive prose remains.

**Why:** Gemini synthesis can run before the complete posterior and later math guards can legitimately flip the recommendation. Wiping the narrative downgrades a successful AI explanation to math-only output and hides the evidence users need.

**How to apply:** Any final-direction guard must update only contradictory direction claims, preserve substantive tactical sections, and ensure the visible first/last sections agree with the final recommendation. Recompute the final direction on every request; do not discard reusable daily AI cache solely because the posterior changed.