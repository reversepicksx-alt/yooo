"""Deterministic evidence-quality controls for prediction decisions.

This module does not generate text and does not change a projection merely
because a data source is missing.  It answers a narrower question:

    How much independent, fixture-specific evidence supports the confidence
    attached to the projection we already calculated?

The controls are intentionally one-way.  They may cap confidence or suppress
an unsupported thin edge, but they never boost a prediction.  This keeps the
Bayesian projection and its probability distribution as the source of truth
while preventing sparse or synthetic-looking inputs from presenting as
high-certainty picks.
"""

from __future__ import annotations

from typing import Any


QUALITY_VERSION = "evidence-quality-v1"

_TARGET_FIELDS = {
    "pass_attempts": "passes_total",
    "passes": "passes_total",
    "shots": "shots_total",
    "shots_on_target": "shots_on",
    "shots_assisted": "passes_key",
    "tackles": "tackles_total",
    "key_passes": "passes_key",
    "crosses": "passes_crosses",
    "saves": "goals_saves",
    "goalie_saves": "goals_saves",
    "interceptions": "tackles_interceptions",
    "blocks": "tackles_blocks",
    "dribbles": "dribbles_attempts",
    "clearances": "tackles_clearances",
    "duels_won": "duels_won",
    "goals": "goals_total",
    "assists": "goals_assists",
    "fouls_drawn": "fouls_drawn",
    "fouls_committed": "fouls_committed",
    "yellow_cards": "cards_yellow",
}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _usable_player_logs(logs: list[dict[str, Any]], prop_type: str) -> list[dict[str, Any]]:
    """Return real logs with target-stat evidence.

    Season-average fallback rows are deliberately not counted as independent
    match evidence.  They can still support the underlying prior, but they
    must not make the quality gate believe recent fixture history exists.

    A log is counted as a real fixture game when ANY of the following hold:
      1. The prop-specific stat field (or the generic "targetStat" key) has a
         numeric value.
      2. The log has minutes > 0.  When the fixture-player cache is the source
         (quota-exhausted mode), API-Football returns ``null`` for stats where
         the player recorded zero — not for stats that are genuinely absent.
         A player who played 90 minutes with 0 shots is real game evidence; it
         must not be silently excluded and make an established player look like
         a no-data case.
    """
    field = _TARGET_FIELDS.get(prop_type, prop_type)
    usable = []
    for log in logs or []:
        if not isinstance(log, dict) or log.get("synthetic"):
            continue
        # Prefer an explicit numeric stat value for the prop.
        value = log.get(field)
        if value is None:
            value = log.get("targetStat")
        if _num(value) is not None:
            usable.append(log)
            continue
        # Fallback: the player played (minutes > 0) — null stat means 0, not
        # absent.  This covers fixture-cache rows written during live API
        # calls where the target stat came back null from the provider.
        if _num(log.get("minutes")) is not None and (log.get("minutes") or 0) > 0:
            usable.append(log)
    return usable


def evaluate_prediction_quality(
    *,
    prop_type: str,
    player_logs: list[dict[str, Any]] | None = None,
    h2h_logs: list[dict[str, Any]] | None = None,
    comparable_sample: int = 0,
    team_fixture_stats: list[dict[str, Any]] | None = None,
    opponent_fixture_stats: list[dict[str, Any]] | None = None,
    match_dominance: dict[str, Any] | None = None,
    lineup_status: str | None = None,
    fixture_id: Any = None,
    match_odds: dict[str, Any] | None = None,
    position: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Assess independent evidence groups without changing model math."""
    real_logs = _usable_player_logs(player_logs or [], prop_type)
    h2h_n = len([row for row in (h2h_logs or []) if isinstance(row, dict)])
    comparable_n = max(0, int(comparable_sample or 0))
    team_n = len([row for row in (team_fixture_stats or []) if isinstance(row, dict)])
    opponent_n = len([row for row in (opponent_fixture_stats or []) if isinstance(row, dict)])
    dominance = match_dominance or {}
    real_possession = bool(dominance.get("hasRealPossData")) and (
        _num(dominance.get("expectedPoss")) is not None
    )
    lineup = str(lineup_status or "").lower()
    odds = match_odds or {}
    has_market = bool(
        odds.get("bookmakerOdds")
        or odds.get("americanOdds")
        or odds.get("favorite")
    )

    groups: dict[str, dict[str, Any]] = {
        "player_history": {
            "status": "applied" if len(real_logs) >= 6 else "warning" if real_logs else "unavailable",
            "sampleSize": len(real_logs),
            "detail": f"{len(real_logs)} real game logs with {prop_type.replace('_', ' ')} evidence",
        },
        "opponent_history": {
            "status": "applied" if h2h_n >= 3 or comparable_n >= 3 else "warning" if h2h_n or comparable_n else "unavailable",
            "sampleSize": h2h_n + comparable_n,
            "detail": f"{h2h_n} direct opponent games and {comparable_n} comparable matchups",
        },
        "tactical_context": {
            "status": "applied" if team_n >= 3 and opponent_n >= 3 else "warning" if team_n or opponent_n else "unavailable",
            "sampleSize": min(team_n, opponent_n) if team_n and opponent_n else max(team_n, opponent_n),
            "detail": f"{team_n} team and {opponent_n} opponent fixture-stat rows",
        },
        "possession_context": {
            "status": "applied" if real_possession else "unavailable",
            "sampleSize": int(dominance.get("h2hPossCount") or 0) or None,
            "detail": (
                f"verified expected possession {dominance.get('expectedPoss')}%"
                if real_possession else "verified possession data unavailable"
            ),
        },
        "availability_role": {
            "status": "applied" if lineup in {"confirmed", "starting"} else "warning" if lineup in {"predicted", "substitute"} or position or role else "unavailable",
            "sampleSize": None,
            "detail": f"lineup={lineup_status or 'unknown'} position={position or 'unknown'} role={role or 'unknown'}",
        },
        "fixture_identity": {
            "status": "applied" if fixture_id is not None else "warning",
            "sampleSize": None,
            "detail": "verified fixture identity present" if fixture_id is not None else "fixture identity unavailable",
        },
        "market_context": {
            "status": "applied" if has_market else "unavailable",
            "sampleSize": None,
            "detail": "fixture market context available" if has_market else "fixture market context unavailable",
        },
    }

    applied = sum(group["status"] == "applied" for group in groups.values())
    warnings = sum(group["status"] == "warning" for group in groups.values())
    unavailable = sum(group["status"] == "unavailable" for group in groups.values())

    # Independent evidence is rewarded, but missing inputs stay neutral rather
    # than becoming negative numeric evidence.  Optional feeds are not
    # guaranteed for every league, so their absence must not punish a pick
    # that has a real player history and verified fixture identity.  The score
    # is a confidence-control signal, not a probability or projection
    # multiplier.
    score = 45 + applied * 8 + (warnings * 2)
    score = max(20, min(95, int(round(score))))
    level = "high" if score >= 78 else "medium" if score >= 58 else "low"

    caps: list[tuple[int, str]] = []
    if not real_logs:
        caps.append((60, "No real player game-log sample was available."))
    elif len(real_logs) < 3:
        caps.append((60, f"Only {len(real_logs)} real player game log(s) were available."))
    elif len(real_logs) < 6:
        caps.append((64, f"Only {len(real_logs)} real player game logs were available."))
    if fixture_id is None:
        caps.append((60, "The exact fixture could not be verified."))
    if score < 45:
        caps.append((58, f"Independent evidence quality is low ({score}/100)."))
    elif score < 58:
        caps.append((62, f"Independent evidence quality is limited ({score}/100)."))
    if warnings >= 4:
        caps.append((64, f"{warnings} evidence groups require caution."))

    confidence_cap = min((cap for cap, _ in caps), default=None)
    cap_reasons = [reason for _, reason in caps]

    return {
        "version": QUALITY_VERSION,
        "score": score,
        "level": level,
        "groups": groups,
        "appliedGroups": applied,
        "warningGroups": warnings,
        "unavailableGroups": unavailable,
        "realPlayerLogCount": len(real_logs),
        "confidenceCap": confidence_cap,
        "capReasons": cap_reasons,
        "thinEvidence": score < 58 or len(real_logs) < 3,
    }


def apply_prediction_quality_controls(
    prediction: dict[str, Any],
    *,
    line: float | int | None,
    quality: dict[str, Any],
) -> dict[str, Any]:
    """Apply one-way confidence and thin-edge controls to a prediction."""
    if not isinstance(prediction, dict):
        return prediction

    current_conf = _num(prediction.get("confidenceScore")) or 50.0
    recommendation = str(prediction.get("recommendation") or "").upper()
    projected = _num(prediction.get("projectedValue"))
    line_num = _num(line)
    cap = quality.get("confidenceCap")
    changed = False
    edge_pct = (
        abs(projected - line_num) / line_num * 100
        if projected is not None and line_num and line_num > 0 else None
    )
    quality["edgePercent"] = round(edge_pct, 2) if edge_pct is not None else None

    if recommendation != "PASS" and cap is not None and current_conf > float(cap):
        prediction["confidenceScore"] = int(round(cap))
        prediction["confidenceLevel"] = (
            "High" if cap >= 70 else "Medium" if cap >= 55 else "Low"
        )
        prediction["qualityConfidenceCapped"] = True
        changed = True
        current_conf = float(prediction["confidenceScore"])

    # PASS only when both the edge and the evidence are weak.  Strong
    # projections are not suppressed solely because one optional data source
    # is unavailable.
    if (
        recommendation in {"OVER", "UNDER"}
        and edge_pct is not None
        and edge_pct < 2.0
        and quality.get("score", 0) < 58
        and current_conf <= 62
    ):
        prediction["passLeaning"] = recommendation
        prediction["recommendation"] = "PASS"
        prediction["passReason"] = (
            f"PASS — the final edge is only {edge_pct:.1f}% and the "
            f"independent evidence quality is {quality.get('score', 0)}/100."
        )
        prediction["skipReason"] = "THIN_EDGE_LOW_EVIDENCE"
        prediction["confidenceScore"] = 50
        prediction["rawConfidence"] = 50
        prediction["confidenceLevel"] = "Low"
        prediction["coinFlip"] = False
        changed = True

    prediction["evidenceQuality"] = quality
    if changed:
        alerts = prediction.get("tacticalAlerts") or []
        for reason in quality.get("capReasons") or []:
            message = f"EVIDENCE QUALITY: {reason}"
            if message not in alerts:
                alerts.append(message)
        if prediction.get("passReason") and prediction["passReason"] not in alerts:
            alerts.append(prediction["passReason"])
        prediction["tacticalAlerts"] = alerts
    return prediction