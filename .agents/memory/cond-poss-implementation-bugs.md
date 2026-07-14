---
name: Conditional possession adjustment — 3 implementation bugs
description: Three bugs that silently killed the condPossAdj feature on first deploy; all in predict.py
---

## The bugs

### 1. NameError: `sport` vs `req.sport`
In the eligibility gate inside a try/except block, `sport == "soccer"` raised `NameError: name 'sport' is not defined`. The correct form is `req.sport == "soccer"`. The `except Exception` swallowed it silently — all that appeared was `[COND POSS] Error: name 'sport' is not defined` in the log.

**Why:** The main predict function uses `req.sport`, not a local `sport` variable. The bare `except Exception` catch pattern means these NameErrors are silent in production unless you grep logs.

### 2. Wrong dict key: `hasRealPossData` vs `seasonAvgIsReal`
The eligibility gate checked `match_dominance.get("hasRealPossData", False)` but the actual key (set at line 1807) is `"seasonAvgIsReal"`. Result: gate was always `False` even with real possession data.

**Why:** `match_dominance` is initialized as `{"expectedPoss": 50.0, ..., "seasonAvgIsReal": False}`. Always grep the dict initialization before adding gates that check it.

### 3. condPossAdj serialized only to `matchFactors.bayesian`, not `bayesianMetrics`
The result was computed and applied (possession DID change), but the response dict put `condPossAdj` only inside `matchFactors.bayesian.condPossAdj`. The mobile tactical-AI prompt reads `pred.bayesianMetrics.condPossAdj`. Fix: mirror it into `bayesianMetrics` after `matchFactors` is built.

**How to apply:** After any new engine result that needs to surface in the mobile, check BOTH serialization paths — `bayesianMetrics` (for tactical AI prompt and mobile display) and `matchFactors.bayesian` (for the Model Factors card).
