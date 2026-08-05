---
name: Evidence-quality gate
description: Deterministic post-projection layer that can only cap confidence or convert thin edges to PASS; never boosts; absent optional feeds are neutral.
---

## The rule
`backend/prediction_quality.py` runs after the Bayesian projection is finalized.
It evaluates 7 independent evidence groups:
  player_history, opponent_history, tactical_context, possession_context,
  availability_role, fixture_identity, market_context

Score = `45 + applied*8 + warnings*2`, clamped 20–95. Missing optional feeds are neutral (no subtraction).
level: "high" ≥78, "medium" ≥58, "low" <58.

**Confidence cap** triggers (one-way only — never boosts):
- 0 real logs → cap 60; 1–2 logs → cap 60; 3–5 logs → cap 64
- fixture_id is None → cap 60; score<45 → cap 58; score<58 → cap 62; ≥4 warnings → cap 64
- Smallest cap value applies when multiple conditions fire.

**Thin-edge PASS** triggers only when ALL three hold:
  edge_pct < 2.0 AND quality.score < 58 AND current_conf ≤ 62
  → recommendation="PASS", passLeaning=original direction, skipReason="THIN_EDGE_LOW_EVIDENCE"

**Output fields added to prediction dict:**
  evidenceQuality (full quality dict), qualityConfidenceCapped (bool, only when cap fires),
  passReason, passLeaning, skipReason (only when PASS triggered)

**Factor ledger / analysisFactors / modelInputSnapshot:**
  analysisFactors gets an item id="evidence_quality".
  modelInputSnapshot.final.evidenceQuality is always written.
  factorLedger gets entries when cap or PASS fires.

**Why:** prevents sparse or synthetic-looking inputs from presenting as high-certainty picks while keeping the Bayesian projection as the primary signal.

**How to apply:** insert the quality call AFTER all Bayesian/calibration/BAYESIAN-TRUTH overrides are complete, BEFORE final persistence. The gate reads `player_game_logs` — ensure this is the full game-history list (not just fixture-stats rows), especially in quota-exhausted mode where API calls are skipped and the cache must supply real match logs.

## Bayesian edge-case fixes (same session)
- Zero-variance history: market fusion now gated on `prior_variance > 0`; identical-stat players are not pulled toward the book line.
- Covariate weight display: `w_covariate = min(26, round(...))` so rounding never leaks the cap.
