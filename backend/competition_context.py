"""Competition-aware historical evidence with hierarchical backoff.

This module is deliberately evidence-only.  It describes how a player's
historical prop values behave in the current competition/stage, but it does
not change the Reverse Formula projection until a leakage-safe replay
explicitly promotes it.

The hierarchy is:

    competition + stage + venue
    competition + stage
    competition
    venue
    all verified history

Thin buckets borrow strength from their broader parents through a small
James-Stein-style blend.  The response keeps every bucket auditable so a
consumer can tell whether the current competition sample is real or merely a
fallback.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional


PROP_FIELDS = {
    "goals": "goals_total",
    "assists": "goals_assists",
    "shots_assisted": "passes_key",
    "pass_attempts": "passes_total",
    "passes": "passes_total",
    "passes_attempted": "passes_total",
    "shots": "shots_total",
    "shots_on_target": "shots_on",
    "tackles": "tackles_total",
    "key_passes": "passes_key",
    "saves": "goals_saves",
    "goalie_saves": "goals_saves",
    "interceptions": "tackles_interceptions",
    "blocks": "tackles_blocks",
    "dribbles": "dribbles_attempts",
    "dribbles_success": "dribbles_success",
    "fouls_drawn": "fouls_drawn",
    "fouls_committed": "fouls_committed",
    "crosses": "passes_crosses",
    "clearances": "tackles_clearances",
    "duels_won": "duels_won",
    "yellow_cards": "cards_yellow",
}

PASS_PROPS = {"pass_attempts", "passes", "passes_attempted"}


def _number(value: Any) -> Optional[float]:
    try:
        if value is None or str(value).strip() == "":
            return None
        value = float(value)
        return value if value == value and abs(value) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return text


def normalize_stage(round_value: Any) -> Optional[str]:
    """Collapse provider round labels into stable competition stages."""
    text = str(round_value or "").strip().lower()
    if not text:
        return None
    if "final" in text and "semi" not in text and "quarter" not in text:
        return "final"
    if "semi" in text:
        return "semi_final"
    if "quarter" in text:
        return "quarter_final"
    if "round of 16" in text or "last 16" in text:
        return "round_of_16"
    if "round of 32" in text or "last 32" in text:
        return "round_of_32"
    if "playoff" in text or "play-off" in text:
        return "playoff"
    if "qualif" in text or "preliminary" in text:
        return "qualifying"
    if "group" in text:
        return "group_stage"
    if "regular" in text or "league" in text or "season" in text:
        return "regular_season"
    if "friend" in text:
        return "friendly"
    return _slug(text) or None


def _competition_key(competition_id: Any, competition_name: Any) -> Optional[str]:
    numeric = _number(competition_id)
    if numeric is not None and int(numeric) > 0:
        return f"id:{int(numeric)}"
    slug = _slug(competition_name)
    return f"name:{slug}" if slug else None


def _row_context(row: dict) -> dict:
    competition_id = (
        row.get("competitionId")
        or row.get("leagueId")
        or row.get("league_id")
        or row.get("competition_id")
    )
    competition_name = (
        row.get("competitionName")
        or row.get("league")
        or row.get("leagueName")
        or row.get("competition")
    )
    round_value = row.get("round") or row.get("stage") or row.get("matchRound")
    return {
        "competitionKey": _competition_key(competition_id, competition_name),
        "competitionId": int(_number(competition_id))
        if _number(competition_id) is not None
        else None,
        "competitionName": str(competition_name).strip() if competition_name else None,
        "stage": normalize_stage(round_value),
        "round": str(round_value).strip() if round_value else None,
        "venue": str(row.get("venue") or "").lower().strip() or None,
        "position": str(
            row.get("specificPosition")
            or row.get("position")
            or row.get("providerPosition")
            or ""
        ).upper().strip()
        or None,
        "role": str(row.get("role") or "").lower().strip() or None,
    }


def _target_context(
    competition_id: Any,
    competition_name: Any,
    round_value: Any,
    venue: Any,
    position: Any,
    role: Any,
) -> dict:
    return {
        "competitionKey": _competition_key(competition_id, competition_name),
        "competitionId": int(_number(competition_id))
        if _number(competition_id) is not None
        else None,
        "competitionName": str(competition_name).strip() if competition_name else None,
        "stage": normalize_stage(round_value),
        "round": str(round_value).strip() if round_value else None,
        "venue": str(venue or "").lower().strip() or None,
        "position": str(position or "").upper().strip() or None,
        "role": str(role or "").lower().strip() or None,
    }


def _matches(row: dict, target: dict, *, fields: tuple[str, ...]) -> bool:
    for field in fields:
        if target.get(field) is None or row.get(field) != target.get(field):
            return False
    return True


def _summarize(rows: list[dict], prop_type: str, field: str, line: Any) -> dict:
    values = [row["value"] for row in rows if row.get("value") is not None]
    values = [float(value) for value in values]
    result = {
        "sampleSize": len(values),
        "average": round(sum(values) / len(values), 2) if values else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "metric": field,
    }
    if values and line is not None:
        line_number = _number(line)
        if line_number is not None:
            over = sum(value > line_number for value in values)
            under = sum(value < line_number for value in values)
            result["line"] = line_number
            result["overHits"] = over
            result["underHits"] = under
            result["overPct"] = round(over / len(values) * 100, 1)
            result["underPct"] = round(under / len(values) * 100, 1)
    return result


def _blend_summaries(summaries: list[tuple[str, dict]]) -> dict:
    """Blend from broad to specific, preserving the selected bucket audit."""
    usable = [(level, summary) for level, summary in summaries if summary.get("sampleSize", 0)]
    if not usable:
        return {
            "average": None,
            "sampleSize": 0,
            "sourceLevel": None,
            "effectiveSampleSize": 0,
        }

    # Start with the broadest observed estimate.  Each narrower bucket earns
    # more authority as it accumulates observations, but never fully discards
    # its parent when the sample is thin.
    level, broad = usable[-1]
    estimate = broad.get("average")
    effective_n = float(broad.get("sampleSize") or 0)
    for level, summary in reversed(usable[:-1]):
        child_n = float(summary.get("sampleSize") or 0)
        child_avg = summary.get("average")
        if child_avg is None:
            continue
        weight = child_n / (child_n + 5.0)
        estimate = (
            child_avg * weight + (estimate if estimate is not None else child_avg) * (1.0 - weight)
        )
        effective_n = child_n + effective_n * (1.0 - weight)

    selected_level, selected = usable[0]
    return {
        "average": round(estimate, 2) if estimate is not None else None,
        "sampleSize": selected.get("sampleSize", 0),
        "sourceLevel": selected_level,
        "sourceAverage": selected.get("average"),
        "effectiveSampleSize": round(effective_n, 1),
    }


def _make_rows(logs: Iterable[dict], field: str, prop_type: str) -> list[dict]:
    rows = []
    for log in logs or []:
        if not isinstance(log, dict):
            continue
        value = _number(log.get(field))
        minutes = _number(log.get("minutes"))
        if value is None or (minutes is not None and minutes <= 0):
            continue
        context = _row_context(log)
        # A soccer row without verified venue/competition metadata must not be
        # silently assigned to a competition bucket. It can still be part of
        # the broad "all history" fallback when it is otherwise verified.
        rows.append({
            **context,
            "value": value,
            "teamPasses": _number(
                log.get("teamPassAttempts")
                or log.get("teamPasses")
                or log.get("team_passes")
            ),
        })
    return rows


def _bucket_rows(rows: list[dict], target: dict, level: str) -> list[dict]:
    if level == "competition_stage_venue":
        return [
            row for row in rows
            if _matches(row, target, fields=("competitionKey", "stage", "venue"))
        ]
    if level == "competition_stage":
        return [
            row for row in rows
            if _matches(row, target, fields=("competitionKey", "stage"))
        ]
    if level == "competition":
        return [
            row for row in rows
            if _matches(row, target, fields=("competitionKey",))
        ]
    if level == "venue":
        return [
            row for row in rows
            if target.get("venue") and row.get("venue") == target["venue"]
        ]
    return rows


def build_competition_context(
    logs: Iterable[dict],
    *,
    prop_type: str,
    competition_id: Any = None,
    competition_name: Any = None,
    round_value: Any = None,
    venue: Any = None,
    position: Any = None,
    role: Any = None,
    line: Any = None,
) -> dict:
    """Build an auditable competition/stage evidence packet for any prop."""
    target = _target_context(
        competition_id,
        competition_name,
        round_value,
        venue,
        position,
        role,
    )
    field = PROP_FIELDS.get(prop_type, prop_type)
    rows = _make_rows(logs, field, prop_type)
    levels = [
        "competition_stage_venue",
        "competition_stage",
        "competition",
        "venue",
        "all",
    ]
    buckets = []
    summaries = []
    for level in levels:
        bucket = _bucket_rows(rows, target, level)
        summary = _summarize(bucket, prop_type, field, line)
        summary["level"] = level
        summary["competitionSpecific"] = level.startswith("competition")
        buckets.append(summary)
        summaries.append((level, summary))

    selected = _blend_summaries(summaries)
    packet = {
        "version": "competition-context-v1",
        "available": bool(rows and selected.get("average") is not None),
        "shadowOnly": True,
        "projectionAdjustmentStatus": "shadow_only",
        "projectionAdjustment": 0.0,
        "propType": prop_type,
        "metric": field,
        "target": target,
        "buckets": buckets,
        "selected": selected,
        "historyRows": len(rows),
        "reason": (
            "Competition/stage evidence is descriptive and does not alter the "
            "Reverse Formula projection."
        ),
    }

    if prop_type in PASS_PROPS:
        share_rows = [
            {**row, "value": row["value"] / row["teamPasses"] * 100.0}
            for row in rows
            if row.get("teamPasses") is not None and row["teamPasses"] > 0
        ]
        share_summaries = []
        share_buckets = []
        for level in levels:
            bucket = _bucket_rows(share_rows, target, level)
            summary = _summarize(bucket, prop_type, "playerPassSharePct", None)
            summary["level"] = level
            summary["competitionSpecific"] = level.startswith("competition")
            share_buckets.append(summary)
            share_summaries.append((level, summary))
        packet["passShare"] = {
            "available": bool(share_rows),
            "metric": "player_passes / verified_team_passes × 100",
            "buckets": share_buckets,
            "selected": _blend_summaries(share_summaries),
            "rowCount": len(share_rows),
        }
    else:
        packet["passShare"] = {
            "available": False,
            "metric": "not_applicable",
            "buckets": [],
            "selected": _blend_summaries([]),
            "rowCount": 0,
        }

    return packet