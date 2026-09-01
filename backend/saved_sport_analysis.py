"""Helpers for serving durable, sport-specific saved analysis snapshots.

The prediction collections are cache-like and may be rotated.  Saved picks are
the durable source for the analysis screen, with a cached prediction allowed to
fill in fields that were not present on an older saved row.
"""

from __future__ import annotations

from typing import Any


SAVED_ANALYSIS_FIELDS = (
    "sport",
    "playerName",
    "playerId",
    "teamName",
    "teamId",
    "opponentName",
    "opponentId",
    "position",
    "playerPosition",
    "role",
    "playerRole",
    "propType",
    "line",
    "projection",
    "projectedValue",
    "recommendation",
    "confidence",
    "confidenceScore",
    "confidenceLevel",
    "rawConfidence",
    "pOver",
    "pUnder",
    "priorMean",
    "momentum",
    "momentumMean",
    "sampleSize",
    "streakFlag",
    "gameLogs",
    "playerGameLogs",
    "recentValues",
    "hitRates",
    "historyGameCount",
    "historySeasons",
    "historyRange",
    "matchupOverview",
    "moneyline",
    "sharpSummary",
    "reasoning",
    "tacticalBreakdown",
    "tacticalAlerts",
    "analysisFactors",
    "modelInputSnapshot",
    "factorLedger",
    "factorLedgerVersion",
    "factorLedgerFingerprint",
    "bayesianMetrics",
    "positionComparison",
    "roleEvidence",
    "roleEvidencePacket",
    "evidenceQuality",
    "qualityConfidenceCapped",
    "passReason",
    "safetyRating",
    "propHistoricalRate",
    "propHistoricalN",
    "lineDeviationBand",
    "lineDeviationPct",
    "lineDeviationHitRate",
    "lineDeviationHitRateN",
    "gameScript",
    "matchScript",
    "tacticalContext",
    "tacticalIntelligence",
    "status",
    "result",
    "actualValue",
    "settledAt",
    "fixtureDate",
    "fixtureId",
    "gameId",
    "gameDate",
    "season",
    "venue",
    "playerIsHome",
    "homeTeam",
    "awayTeam",
    "gameTotalUsed",
    "gameTotalSource",
    "gameTotal",
    "pitcherName",
    "pitcherHandedness",
    "batterHandedness",
    "pitcherEra",
    "lineupSpot",
    "oppRankPercentile",
    "restDays",
)


def merge_saved_analysis(
    pick: dict[str, Any],
    prediction: dict[str, Any] | None,
    sport: str,
) -> dict[str, Any]:
    """Merge a cached prediction with the durable pick without stale overrides.

    Pick identity and market fields always win.  The snapshot fields captured
    at save time win over rotated cache data, so a later prediction at another
    line cannot replace the saved line or its evidence.
    """
    merged: dict[str, Any] = {}
    if isinstance(prediction, dict):
        merged.update(prediction)

    for field in SAVED_ANALYSIS_FIELDS:
        value = pick.get(field)
        if value is not None:
            merged[field] = value

    merged["sport"] = sport
    merged["pickId"] = pick.get("pickId")
    merged["playerName"] = pick.get("playerName") or merged.get("playerName") or ""
    merged["playerId"] = pick.get("playerId") or merged.get("playerId")
    merged["teamName"] = pick.get("teamName") or merged.get("teamName") or ""
    merged["opponentName"] = pick.get("opponentName") or merged.get("opponentName") or ""
    merged["propType"] = pick.get("propType") or merged.get("propType") or ""
    merged["line"] = pick.get("line")
    merged["projection"] = (
        pick.get("projection")
        if pick.get("projection") is not None
        else pick.get("projectedValue")
        if pick.get("projectedValue") is not None
        else merged.get("projection")
        if merged.get("projection") is not None
        else merged.get("projectedValue")
    )
    merged["projectedValue"] = merged.get("projection")
    merged["recommendation"] = (
        str(pick.get("recommendation") or merged.get("recommendation") or "PASS").upper()
    )
    merged["position"] = (
        pick.get("position")
        or pick.get("playerPosition")
        or merged.get("position")
        or merged.get("playerPosition")
        or ""
    )
    merged["playerPosition"] = merged["position"]
    merged["role"] = (
        pick.get("role")
        or pick.get("playerRole")
        or merged.get("role")
        or merged.get("playerRole")
        or ""
    )
    merged["playerRole"] = merged["role"]
    merged["roleEvidencePacket"] = (
        pick.get("roleEvidencePacket")
        or pick.get("roleEvidence")
        or merged.get("roleEvidencePacket")
        or merged.get("roleEvidence")
        or {}
    )
    merged["analysisStatus"] = (
        "complete"
        if merged.get("gameLogs") or merged.get("playerGameLogs") or merged.get("analysisFactors")
        else "limited"
    )
    merged["analysisSource"] = "saved_pick_snapshot"

    # Never let internal account fields cross the analysis endpoint.
    for field in ("email", "token", "_id", "_request"):
        merged.pop(field, None)
    return merged