---
name: Gaussian live probability architecture
description: The original elite three-layer design separates baseline, matchup likelihood, and live Gaussian remaining-outcome updates from later tactical modifiers.
---

The product’s intended “elite” three-layer model is:

1. A player-rate prior from baseline and role evidence.
2. An opponent/same-role likelihood update.
3. A live Gaussian remaining-total update with drift as observed match data arrives.

The pre-match Gaussian probability engine exists, but live `hitPct` must not be described as a full random walk unless it uses the saved distribution, remaining time, and a shrunk observed drift. Tactical modifiers such as deep-block and CDM boosts are contextual signals, not substitutes for the three layers.

**Why:** Confusing these structures led to overstating or understating the importance of the original Bayesian design and risks presenting heuristic live pace as mathematically equivalent to a Gaussian update.

**How to apply:** Keep saved pre-match probability immutable; expose a separate live probability, remaining projection/range, and drift status. Validate the live updater in shadow mode before replacing the current pace heuristic.