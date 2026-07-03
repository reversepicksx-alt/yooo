---
name: Live pace-divergence warning + match-dominance covariate limitation
description: Why pre-match Bayesian projections can diverge sharply from live in-game reality, and how the app now surfaces that instead of silently under-warning users.
---

## The gap
`compute_match_dominance()` (backend/routes/predict.py) derives `expectedPoss` entirely from
pre-match signals (season possession averages, standings rank gap, betting odds) with a ±20%-of-prior_mean
cap on the resulting covariate adjustment in `bayesian_engine.py`. It has zero awareness of in-game
events (red cards, early goals, a team parking the bus). This is architectural, not a bug — a pre-match
model cannot predict future match-state shifts. The fix is not to make the pre-match number "smarter",
it's to detect and flag the divergence live.

**Why:** A real incident — Bensebaini (Algeria LB) Pass Attempts UNDER 68.5 at 81% confidence — blew past
the line after Switzerland scored early then parked the bus, handing Algeria/Bensebaini's side sustained
possession. The projection was defensible pre-match; it became wrong the moment the match state changed.

## The mitigation
The app already computed live `pace` (linear extrapolation to 90') and `hit_pct` (live-adjusted hit
probability) per pick on every `/api/picks/list` call, but only displayed them as quiet inline numbers
with no alert. Added `paceMismatch`/`paceWarning` fields (fires when live, elapsed≥15min, hit_pct≤25%)
so the mobile pick card now shows an explicit amber warning banner instead of relying on the user to
notice a subtle number drifting.

**How to apply:** When a user reports "the projection was way off because of what happened in the game,"
distinguish two different fixes: (1) pre-match modeling bugs (bucketing, cap sizing, wrong covariate
inputs — actually fixable) vs (2) pre-match-vs-live divergence (not fixable in the projection itself —
mitigate via live signal surfacing instead of pretending the static model can react to the future).
