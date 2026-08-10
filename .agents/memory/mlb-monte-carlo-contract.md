---
name: MLB Monte Carlo contract
description: MLB callers must consume the shared seven-value Monte Carlo result.
---

The shared Monte Carlo helper returns probabilities, 60% bounds, 80% bounds, and a most-likely value. MLB must preserve its legacy confidenceInterval shape while exposing the unified range/distribution fields.

**Why:** A helper upgrade from four return values to seven caused every MLB prediction reaching the engine to crash with `ValueError: too many values to unpack`, producing generic HTTP 500 errors for users.

**How to apply:** When changing the shared probability helper, search every caller for tuple unpacking. Prefer explicit named unpacking and add a regression test for at least one MLB composite prop and one standard count prop.