---
name: Positional baseline squeeze — priorSamples bug
description: apply_positional_squeeze must receive the engine's post-filter priorSamples count, not raw len(_bayes_logs).
---

## The Rule
Pass `early_bayes.get("priorSamples", len(_bayes_logs))` as `n_samples` to `apply_positional_squeeze`, not raw `len(_bayes_logs)`.

**Why:** The Bayesian engine filters game logs by the 30-minute threshold (and other quality filters) before computing. `_bayes_logs` can have 10 entries while `priorSamples` is 0 (all filtered). Passing the raw count causes the squeeze to think it has data and skip the n=0 centering branch — leaving posteriorMean pinned at `line` with P(over)=P(under)=50%.

**How to apply:** In `backend/routes/predict.py`, the call to `apply_positional_squeeze` passes:
```python
n_samples=early_bayes.get("priorSamples", len(_bayes_logs))
```
The `apply_positional_squeeze` function in `backend/positional_baseline.py` handles three cases:
- n=0 → center 70% toward baseline p50 (strong prior)
- n≥8 → no squeeze (sufficient data)
- n 1-7 → outlier fence only (1.5×IQR clip)

After centering fires, pOver/pUnder are recomputed from a normal distribution with σ = IQR/1.35 derived from baseline p25/p75.
