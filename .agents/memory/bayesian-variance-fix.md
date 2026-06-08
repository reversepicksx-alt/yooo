---
name: Bayesian Monte Carlo variance fix
description: Critical bug in NBA/WNBA/NCAAB/NCAAW/ATP engines — variance passed as std gets squared inside _monte_carlo_probability.
---

## The Rule
Always pass `math.sqrt(variance)` as the `std` argument to `_monte_carlo_probability`
AND pass `variance=var` as the explicit kwarg. Never pass raw variance as std.

The signature: `_monte_carlo_probability(mean, std, line, ..., variance=None)`
For count stats: `var = variance if variance and variance > 0 else std ** 2`
→ If you pass raw variance as std, the kwarg is None, so it uses `std**2 = variance^2`.

**Why:** All basketball engines compute sample variance (e.g., 4.0) then passed it
as `std` — resulting in NegBin parameterised with variance=16.0 instead of 4.0.
This inflated spread suppresses pOver on above-line projections, causing wrong UNDER
recommendations even when expected value > line.

**How to apply:** Use the `_mc(mean, var, line, is_count)` helper defined at the top of
each engine file. Never call `_baye_mc(mean, variance, ...)` with raw variance.

## NCAAW/ATP Broken Call Pattern (now fixed)
Old broken call: `p_over, p_under = _baye_mc(values[:12], line, prop_type in COUNT_PROPS)`
This passes a LIST as mean → TypeError crash on every prediction.
Fixed: Use `_mc(projection, values, line, is_count)` which computes variance internally.

## Projection Visual Consistency
After [BAYESIAN TRUTH] override, all BDL sport routes now apply:
- if rec=UNDER and projection > line → projection = line - 0.5
- if rec=OVER  and projection < line → projection = line + 0.5
This prevents the confusing "projection above line but UNDER badge" display.
