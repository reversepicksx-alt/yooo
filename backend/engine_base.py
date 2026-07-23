"""
engine_base.py — Unified Engine Contract
=========================================
Every sport engine MUST produce a prediction dict conforming to STANDARD_FIELDS.
Use normalize_response() at the end of any predict route to fill missing fields
with safe defaults so the mobile UI never has to check `prediction.sport` before
rendering a feature.

RULE: The mobile UI renders features based on DATA PRESENCE, not sport name.
      Never add `prediction.sport === 'soccer'` gates in the frontend.
      Instead add the field to every sport backend.

Adding a new sport? Checklist:
  [x] gameLogs — list of game/match logs, each with: value, opponent, venue
                  ('home'/'away'), score (display string), date
  [x] matchupOverview — {homeTeam, awayTeam, playerIsHome, moneyline?,
                          expectedGameType, keyMatchupFactor}
  [x] sharpSummary — 1-sentence AI verdict
  [x] reasoning — 2-3 sentence AI reasoning
  [x] tacticalBreakdown — full AI markdown analysis
  [x] riskSignals — {redCardRisk?, note?} or sport-equivalent risk flags
  [x] confidenceScore, confidenceLevel, pOver, pUnder, recommendation
  [x] projection, line, propType, playerName, teamName, opponentName
  [x] sport — the sport key string
"""

from typing import Any

STANDARD_FIELDS: dict[str, Any] = {
    # ── Identity ────────────────────────────────────────────────────────────
    "sport":            "",
    "playerName":       "",
    "playerId":         None,
    "teamName":         "",
    "opponentName":     "",
    "playerPosition":   "",
    "propType":         "",
    "line":             None,
    "projection":       None,
    "sport":            "",

    # ── Probability core ────────────────────────────────────────────────────
    "pOver":            50.0,
    "pUnder":           50.0,
    "recommendation":   "OVER",
    "confidenceScore":  50,
    "confidenceLevel":  "Low",
    "lowConviction":    False,
    "streakFlag":       "",
    "priorMean":        None,
    "momentumMean":     None,
    "sampleSize":       0,

    # ── Game logs (REQUIRED — venue label 'home'/'away' on every log) ───────
    # Shape: [{date, value, opponent, venue, score, ...sport-specific}]
    "gameLogs":         [],

    # ── Matchup overview (REQUIRED for all sports) ──────────────────────────
    # Shape: {homeTeam, awayTeam, playerIsHome, expectedGameType,
    #          keyMatchupFactor, moneyline?}
    "matchupOverview":  None,

    # ── AI narrative (REQUIRED — AI engine fills these for every sport) ─────
    "sharpSummary":     "",
    "reasoning":        "",
    "tacticalBreakdown": "",
    "tacticalAlerts":   [],

    # ── Risk signals (optional but supported on all sports) ─────────────────
    # Shape: {redCardRisk?, note?} — use sport-appropriate risk label
    "riskSignals":      None,
    "congestion":       None,
}


def normalize_response(response: dict) -> dict:
    """
    Fill any missing standard fields with safe defaults.
    Call this at the END of every sport predict route before returning.

    Example:
        from engine_base import normalize_response
        return normalize_response(response)
    """
    for field, default in STANDARD_FIELDS.items():
        response.setdefault(field, default)

    # Normalize gameLogs: ensure every log has a 'venue' string field
    logs = response.get("gameLogs") or []
    for log in logs:
        if "venue" not in log:
            # Derive from isHome bool if present
            if "isHome" in log:
                log["venue"] = "home" if log["isHome"] else "away"
            else:
                log["venue"] = "neutral"
        # Normalize score field: try common aliases
        if "score" not in log:
            log["score"] = (
                log.get("matchScore")
                or log.get("gameScore")
                or (
                    f"{log['homeScore']}-{log['awayScore']}"
                    if log.get("homeScore") is not None and log.get("awayScore") is not None
                    else None
                )
            )

    return response
