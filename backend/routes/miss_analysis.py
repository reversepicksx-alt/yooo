"""
Self-Learning Miss Analysis Engine
- Automatically runs 3-AI post-mortem on missed predictions at settlement
- Extracts calibration patterns (sport, propType, venue, position)
- Applies learned adjustments to ALL future predictions across both pipelines
- No manual triggers — fully autonomous feedback loop
"""
import traceback
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from config import db
from models import BaseModel
from typing import Optional

router = APIRouter(prefix="/api", tags=["miss_analysis"])


class AnalyzeMissRequest(BaseModel):
    email: str
    token: str
    pickId: str


class GetMissesRequest(BaseModel):
    email: str
    token: str


async def _run_miss_postmortem(pick: dict) -> dict:
    """Build deterministic postmortem reasoning for a missed prediction."""
    player_name = pick.get("playerName", "Unknown")
    team_name = pick.get("teamName", "Unknown")
    opponent = pick.get("opponentName", "Unknown")
    prop_type = pick.get("propType", "unknown")
    line = pick.get("line", 0)
    recommendation = pick.get("recommendation", "over")
    projected = pick.get("projectedValue", 0)
    actual = pick.get("actualValue", 0)
    confidence = pick.get("confidenceScore", 50)
    venue = pick.get("venue", "home")
    match_score = pick.get("matchScore", "")
    sport = pick.get("sport", "soccer")

    projected_val = float(projected or 0)
    actual_val = float(actual or 0)
    line_val = float(line or 0)
    diff_to_projection = actual_val - projected_val
    diff_to_line = actual_val - line_val
    direction = "over" if actual_val > line_val else "under" if actual_val < line_val else "push"
    projection_error = "too_low" if actual_val > projected_val else "too_high" if actual_val < projected_val else "matched"
    abs_error = abs(diff_to_projection)
    if abs_error <= max(0.25, abs(line_val) * 0.15):
        magnitude = "minor"
    elif abs_error <= max(0.5, abs(line_val) * 0.30):
        magnitude = "moderate"
    else:
        magnitude = "major"
    factors = [
        f"projection_vs_line_delta:{(projected_val - line_val):+.2f}",
        f"actual_vs_projection_delta:{diff_to_projection:+.2f}",
        f"actual_vs_line_delta:{diff_to_line:+.2f}",
        f"direction:{direction}",
        f"confidence:{confidence}",
    ]
    if match_score:
        factors.append(f"settlement_context:{match_score}")
    primary_reason = (
        f"Projection drifted {projection_error.replace('_', ' ')} versus the settled outcome; "
        f"actual landed {direction} the line by {diff_to_line:+.1f}."
    )
    calibration_suggestion = (
        f"Apply a small directional bias for similar {sport} {prop_type} picks at {venue} "
        f"when confidence is {confidence}% and settlement follows the same line direction."
    )
    lesson = "Use line alignment, actual settlement direction, and confidence together before treating the projection as stable."
    return {
        "primaryReason": primary_reason,
        "allReasons": {"deterministic": primary_reason},
        "factors": factors[:5],
        "projectionError": projection_error,
        "errorMagnitude": magnitude,
        "gameContextFactor": bool(match_score) or abs(diff_to_line) > abs(abs_error),
        "calibrationSuggestions": [calibration_suggestion],
        "lessons": [lesson],
        "modelsResponded": ["deterministic"],
        "analyzedAt": datetime.now(timezone.utc).isoformat(),
    }


async def _extract_calibration_pattern(pick: dict, analysis: dict):
    """Extract a calibration pattern from a miss and store it."""
    if not analysis:
        return

    pattern = {
        "sport": pick.get("sport", "soccer"),
        "propType": pick.get("propType"),
        "leagueId": pick.get("leagueId"),
        "venue": pick.get("venue"),
        "projectionError": analysis.get("projectionError"),
        "errorMagnitude": analysis.get("errorMagnitude"),
        "gameContextFactor": analysis.get("gameContextFactor"),
        "projectedValue": pick.get("projectedValue"),
        "actualValue": pick.get("actualValue"),
        "line": pick.get("line"),
        "recommendation": pick.get("recommendation"),
        "confidence": pick.get("confidenceScore"),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    await db.calibration_patterns.insert_one(pattern)

    # Update aggregate stats for this prop type
    prop_type = pick.get("propType", "unknown")
    sport = pick.get("sport", "soccer")
    projected = pick.get("projectedValue", 0)
    actual = pick.get("actualValue", 0)

    if projected and actual is not None:
        error_pct = ((actual - projected) / projected * 100) if projected != 0 else 0
        await db.calibration_stats.update_one(
            {"propType": prop_type, "sport": sport},
            {
                "$inc": {"missCount": 1, "totalErrorPct": error_pct},
                "$push": {"recentErrors": {"$each": [error_pct], "$slice": -20}},
                "$set": {"updatedAt": datetime.now(timezone.utc).isoformat()},
            },
            upsert=True,
        )



@router.post("/calibration/insights")
async def get_calibration_insights(req: GetMissesRequest):
    """Return everything the system has learned from miss analyses."""
    session = await db.sessions.find_one(
        {"email": req.email.lower(), "session_token": req.token}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    # Get all calibration stats
    stats = await db.calibration_stats.find({}, {"_id": 0}).to_list(100)

    insights = []
    for s in stats:
        miss_count = s.get("missCount", 0)
        total_error = s.get("totalErrorPct", 0)
        avg_error = total_error / miss_count if miss_count > 0 else 0
        recent = s.get("recentErrors", [])
        recent_avg = sum(recent) / len(recent) if recent else 0

        # Determine bias direction
        positive = sum(1 for e in recent if e > 0)
        negative = sum(1 for e in recent if e < 0)
        total = len(recent)
        bias_consistent = (max(positive, negative) / total >= 0.6) if total > 0 else False

        # Current correction being applied
        adjustment = 0.0
        if miss_count >= 3 and bias_consistent:
            adjustment = max(-15.0, min(15.0, recent_avg * 0.5))

        insights.append({
            "sport": s.get("sport", "unknown"),
            "propType": s.get("propType", "unknown"),
            "missCount": miss_count,
            "avgErrorPct": round(avg_error, 1),
            "recentAvgErrorPct": round(recent_avg, 1),
            "biasDirection": "under-projecting" if avg_error > 0 else "over-projecting",
            "biasConsistent": bias_consistent,
            "activeCorrection": round(adjustment, 1) if bias_consistent and miss_count >= 3 else 0,
            "recentSampleSize": total,
            "updatedAt": s.get("updatedAt", ""),
        })

    # Sort by most misses first
    insights.sort(key=lambda x: x["missCount"], reverse=True)

    # Get total miss analyses count
    total_analyzed = await db.miss_analyses.count_documents({})
    total_misses = await db.picks.count_documents({"email": req.email.lower(), "result": "miss"})

    return {
        "insights": insights,
        "totalAnalyzed": total_analyzed,
        "totalMisses": total_misses,
        "totalPropTypes": len(insights),
    }


@router.post("/picks/analyze-miss")
async def analyze_miss(req: AnalyzeMissRequest):
    """Run 3-AI post-mortem on a missed prediction."""
    session = await db.sessions.find_one(
        {"email": req.email.lower(), "session_token": req.token}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    # Get the pick
    pick = await db.picks.find_one(
        {"pickId": req.pickId, "email": req.email.lower()}, {"_id": 0}
    )
    if not pick:
        raise HTTPException(status_code=404, detail="Pick not found")

    if pick.get("result") != "miss":
        raise HTTPException(status_code=400, detail="Pick is not a miss")

    # Check if already analyzed
    existing = await db.miss_analyses.find_one(
        {"pickId": req.pickId}, {"_id": 0}
    )
    if existing:
        return {"analysis": existing}

    # Run the 3-AI post-mortem
    analysis = await _run_miss_postmortem(pick)
    if not analysis:
        raise HTTPException(status_code=500, detail="All AI models failed to analyze")

    # Store the analysis
    analysis_doc = {
        "pickId": req.pickId,
        "email": req.email.lower(),
        **analysis,
    }
    await db.miss_analyses.update_one(
        {"pickId": req.pickId}, {"$set": analysis_doc}, upsert=True
    )

    # Extract and store calibration pattern
    await _extract_calibration_pattern(pick, analysis)

    return {"analysis": analysis_doc}


@router.post("/picks/misses")
async def get_misses(req: GetMissesRequest):
    """Get all missed picks with their analyses."""
    session = await db.sessions.find_one(
        {"email": req.email.lower(), "session_token": req.token}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    # Get all settled misses
    misses = await db.picks.find(
        {"email": req.email.lower(), "result": "miss"},
        {"_id": 0},
    ).sort("settledAt", -1).to_list(50)

    # Get analyses for these picks
    pick_ids = [m["pickId"] for m in misses]
    analyses = {}
    if pick_ids:
        async for a in db.miss_analyses.find(
            {"pickId": {"$in": pick_ids}}, {"_id": 0}
        ):
            analyses[a["pickId"]] = a

    # Merge analyses into picks (no auto-trigger — removed to save AI tokens)
    for m in misses:
        m["missAnalysis"] = analyses.get(m["pickId"])

    # Get calibration stats
    stats = await db.calibration_stats.find({}, {"_id": 0}).to_list(20)
    calibration = {}
    for s in stats:
        key = f"{s['sport']}_{s['propType']}"
        miss_count = s.get("missCount", 0)
        total_error = s.get("totalErrorPct", 0)
        avg_error = total_error / miss_count if miss_count > 0 else 0
        calibration[key] = {
            "missCount": miss_count,
            "avgErrorPct": round(avg_error, 1),
            "propType": s["propType"],
            "sport": s["sport"],
        }

    return {
        "misses": misses,
        "calibration": calibration,
        "totalMisses": len(misses),
        "analyzedCount": len(analyses),
    }


async def auto_analyze_miss_background(pick_id: str, email: str):
    """Background task: automatically analyze a missed prediction on settlement.
    Fire-and-forget — runs 3-AI postmortem, stores analysis, extracts calibration."""
    try:
        await asyncio.sleep(2)  # Brief delay to ensure DB write is committed
        pick = await db.picks.find_one(
            {"pickId": pick_id, "email": email}, {"_id": 0}
        )
        if not pick or pick.get("result") != "miss":
            return

        existing = await db.miss_analyses.find_one({"pickId": pick_id}, {"_id": 0})
        if existing:
            return

        analysis = await _run_miss_postmortem(pick)
        if not analysis:
            print(f"[AUTO-CALIBRATE] All AIs failed for pick {pick_id}")
            return

        analysis_doc = {
            "pickId": pick_id,
            "email": email,
            "auto": True,
            **analysis,
        }
        await db.miss_analyses.update_one(
            {"pickId": pick_id}, {"$set": analysis_doc}, upsert=True
        )

        await _extract_calibration_pattern(pick, analysis)

        player = pick.get("playerName", "?")
        prop = pick.get("propType", "?")
        proj = pick.get("projectedValue", 0)
        actual = pick.get("actualValue", 0)
        print(f"[AUTO-CALIBRATE] {player} {prop}: projected {proj}, actual {actual} — {analysis.get('primaryReason', '')[:80]}")
    except Exception as e:
        print(f"[AUTO-CALIBRATE] Error analyzing pick {pick_id}: {e}")
        traceback.print_exc()


async def get_calibration_adjustment(sport: str, prop_type: str, venue: str = None) -> dict:
    """Get calibration adjustment for a prop type based on historical misses.
    Returns a dict with adjustment percentage, context string, and metadata.
    This is called by BOTH prediction pipelines before AI models run.
    """
    result = {"adjustment": 0.0, "applied": False, "context": "", "missCount": 0}

    stat = await db.calibration_stats.find_one(
        {"propType": prop_type, "sport": sport}, {"_id": 0}
    )
    if not stat or stat.get("missCount", 0) < 3:
        return result  # Need at least 3 misses to calibrate

    recent = stat.get("recentErrors", [])
    if not recent:
        return result

    miss_count = stat.get("missCount", 0)
    avg_error = sum(recent) / len(recent)

    # Only apply if the bias is consistent (>60% of errors in same direction)
    positive = sum(1 for e in recent if e > 0)
    negative = sum(1 for e in recent if e < 0)
    total = len(recent)
    if max(positive, negative) / total < 0.6:
        return result  # Too mixed, no clear bias

    # Cap adjustment at ±15%
    # avg_error > 0 means actual > projected (under-projecting) → need positive adjustment to raise projection
    # avg_error < 0 means actual < projected (over-projecting) → need negative adjustment to lower projection
    adjustment = max(-15.0, min(15.0, avg_error * 0.5))
    bias_dir = "under-projecting" if avg_error > 0 else "over-projecting"
    prop_label = prop_type.replace("_", " ").title()

    result = {
        "adjustment": round(adjustment, 1),
        "applied": True,
        "context": (
            f"[SELF-LEARNING CALIBRATION]\n"
            f"System has historically {bias_dir} {prop_label} by {abs(avg_error):.1f}% "
            f"({miss_count} misses analyzed, last {total} tracked).\n"
            f"Applying {adjustment:+.1f}% correction to projection.\n"
            f">>> If system under-projects, raise your projection. If over-projects, lower it. <<<"
        ),
        "missCount": miss_count,
        "avgError": round(avg_error, 1),
        "biasDirection": bias_dir,
    }
    return result
