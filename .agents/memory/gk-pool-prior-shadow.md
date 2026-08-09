---
name: Goalkeeper pool prior shadow layer
description: How goalkeeper pass-attempt pool evidence is scoped and why it remains non-live.
---

Goalkeeper pass-attempt pool evidence is a separate, sample-size-shrunk diagnostic prior. It accepts only verified goalkeeper rows, reports its source scope and effective sample size, and remains shadow-only even if a live mode is requested.

**Why:** One goalkeeper miss and one Guadalajara matchup are not enough to justify a global opponent-specific boost; completion percentage is not a substitute for pass volume.

**How to apply:** Keep the pool evidence in the prediction ledger and model-input snapshot. Promote numeric influence only after leakage-safe settled-pick walk-forward validation demonstrates benefit.