---
name: Goalkeeper pool prior shadow layer
description: How goalkeeper pass-attempt pool evidence is scoped and why it remains non-live.
---

Goalkeeper pass-attempt pool evidence is a separate, sample-size-shrunk diagnostic prior. It accepts only verified goalkeeper rows, reports its source scope and effective sample size, and remains shadow-only even if a live mode is requested.

**Why:** One goalkeeper miss or one matchup is not enough to justify a global boost; a later replay of three verified keeper UNDER misses found no identity or settlement defect, but the misses came from opposite possession scripts and all lacked player-share evidence. Completion percentage is not a substitute for pass volume.

**How to apply:** Keep pool evidence in the prediction ledger and model-input snapshot. Treat missing player-share/team-volume joins as a confidence limitation, not permission for a large contextual cut. Stratify any promotion by venue, possession band, role source, H2H sample, and line gap; promote numeric influence only after leakage-safe settled-pick walk-forward validation demonstrates benefit.