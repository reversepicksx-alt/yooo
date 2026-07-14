---
name: Knockout extra-time adjustment
description: Why WC/UCL knockout predictions were at 50% hit rate and how the ET multiplier fixes it.
---

# Knockout Extra-Time Adjustment

## The problem
Settled WC knockout data (Jul 4+ 2026): **50% hit rate** vs 64% group stage.
All misses were UNDER bets on count stats where `actual >> projected` by 20–30+ passes.
Pattern: proj=44, actual=71 (Lerma); proj=66, actual=87 (Lucumí); proj=35, actual=58 (Arias).

## Root cause
~30% of knockout games go to extra time (+30 min). Count stats (pass_attempts, shots, saves, etc.)
scale linearly with minutes. Without the adjustment the engine projects for 90-min games but 
players sometimes play 120 min → UNDER misses. The AI prompt also said "tactical conservatism → 
fewer passes" which is wrong and counter-productive.

## The fix (predict.py)
Three-layer fix, all inside `backend/routes/predict.py`:

1. **KO ET Adjustment** (injected between SITUATION MULT and P-REFRESH ~line 5900):
   - `_KO_ET_MULT = 1 + 0.30 × (30/90) ≈ 1.1000`
   - Applied to `bayesian_posterior` for `_KO_COUNT_PROPS` (pass_attempts, passes, shots, shots_on_target, saves, key_passes, crosses, dribbles, tackles, clearances)
   - P-REFRESH runs AFTER this so p_over/p_under reflect the ET-inflated projection
   - Logged as `[KNOCKOUT ET ADJ]`; stored as `real_bayes["koExtraTimeAdj"]`

2. **KNOCKOUT UNDER CONFIDENCE PENALTY** (injected after BAYESIAN TRUTH flip block ~line 6864):
   - -8pt confidence cap on UNDER bets for count stats in knockout games
   - Floor: max(52, pre_conf - 8) — never suppresses below 52
   - Logged as `[KNOCKOUT UNDER PENALTY]`

3. **Updated AI prompt** (line ~5091):
   - Removed "tactical conservatism → fewer passes" (wrong)
   - Now tells AI: ×1.10 ET uplift already baked in; focus analysis on favorite/possession 
     dynamics, player role in defensive setup, lineup/injury intel

## Knockout detection
`_final_is_knockout` resolved via two sources (OR logic):
1. `game_situation.get("isKnockout", False)` — from situation engine (most reliable)
2. Keyword match on `match_odds["matchRound"]` for: final, quarter, semi, round of, knockout, elimination, playoff

**Why:** Uses OR so either the situation engine flag OR the match round string triggers the adjustment.

## Scope
Applies to ALL knockout competitions (WC, UCL, Copa America, etc.), not just WC.
Activated whenever `_final_is_knockout=True` and `req.propType in _KO_COUNT_PROPS`.
