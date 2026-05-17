"""
CS2 prediction routes — /api/cs2/*
"""
import asyncio
import logging
import json
import re
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from config import db, XAI_API_KEY
import cs2_client
import cs2_engine

log    = logging.getLogger("cs2_routes")
router = APIRouter(prefix="/api/cs2", tags=["cs2"])

CS2_PROP_LABELS = {
    "kills":                "Kills",
    "deaths":               "Deaths",
    "assists":              "Assists",
    "adr":                  "ADR",
    "headshot_pct":         "Headshot %",
    "headshots":            "Headshots",
    "first_kills":          "First Kills",
    "clutches_won":         "Clutches Won",
    "rating":               "Rating",
    "maps_1_2_kills":       "Maps 1-2 Kills",
    "maps_1_2_deaths":      "Maps 1-2 Deaths",
    "maps_1_2_assists":     "Maps 1-2 Assists",
    "maps_1_2_adr":         "Maps 1-2 ADR",
    "maps_1_2_headshots":   "Maps 1-2 Headshots",
    "map3_kills":           "Map 3 Kills",
    "map3_headshots":       "Map 3 Headshots",
    "map3_deaths":          "Map 3 Deaths",
    "map3_assists":         "Map 3 Assists",
    "map3_adr":             "Map 3 ADR",
    "maps_1_3_kills":       "Maps 1-3 Kills",
    "maps_1_3_headshots":   "Maps 1-3 Headshots",
}


# ── AI analysis helper ────────────────────────────────────────────────────────

async def _get_cs2_ai_analysis(
    player_nickname: str,
    prop_type: str,
    line: float,
    opponent: str,
    projection: float,
    p_over: float,
    p_under: float,
    recommendation: str,
    map_logs: list,
    prior_mean: float,
    momentum_mean: float,
    streak_flag: str,
    opp_rank: Optional[int],
    tactical_metrics: Optional[dict] = None,
) -> dict:
    prop_label     = CS2_PROP_LABELS.get(prop_type, prop_type.replace("_", " ").title())
    is_match_level = prop_type in cs2_engine.MATCH_LEVEL_PROPS

    # ── Recent log context (last 5 entries) ───────────────────────────────────
    ctx_lines = []
    for i, m in enumerate(map_logs[:5]):
        field = cs2_engine.CS2_PROPS.get(prop_type, prop_type)
        val   = m.get(field, "?")
        opp_t = m.get("opponent", "") or m.get("tournament", "")
        if is_match_level:
            n_maps = m.get("mapsPlayed", 2)
            won    = "W" if m.get("wonMatch") else "L"
            rounds = m.get("maps_1_2_rounds", "?")
            kast   = m.get("maps_1_2_kast", 0)
            kast_s = f" KAST={kast:.0f}%" if kast else ""
            ctx_lines.append(
                f"  Match {i+1} ({n_maps}-map,{won},{rounds}rnd{kast_s}): {val} {prop_label}"
                + (f" vs {opp_t}" if opp_t else "")
            )
        else:
            mn     = m.get("mapName", "?").replace("de_", "")
            won    = "W" if m.get("wonMap") else "L"
            rounds = m.get("totalRounds", "?")
            kpr    = m.get("killsPerRound", 0)
            kpr_s  = f" ({kpr:.2f}k/r)" if kpr and prop_type == "kills" else ""
            ctx_lines.append(
                f"  Map {i+1} ({mn},{won},{rounds}rnd{kpr_s}): {val} {prop_label}"
                + (f" vs {opp_t}" if opp_t else "")
            )
    game_ctx = "\n".join(ctx_lines) or "  (no recent data)"

    # ── Tactical metrics summary ──────────────────────────────────────────────
    tm = tactical_metrics or {}
    tm_lines = []
    if tm.get("avgKillsPerRound"):
        tm_lines.append(f"• Career KPR: {tm['avgKillsPerRound']:.3f} k/round")
    if tm.get("avgKast"):
        tm_lines.append(f"• KAST efficiency: {tm['avgKast']:.0f}% (consistency signal)")
    if tm.get("entryFraggerRatio") and tm["entryFraggerRatio"] != 1.0:
        ratio = tm["entryFraggerRatio"]
        role  = "entry fragger" if ratio > 1.2 else "support" if ratio < 0.8 else "balanced"
        tm_lines.append(f"• First-duel ratio: {ratio:.2f} ({role})")
    if tm.get("oppRankMultiplier") and tm["oppRankMultiplier"] != 1.0:
        direction = "weaker" if tm["oppRankMultiplier"] > 1.0 else "stronger"
        tm_lines.append(f"• Opponent rank adj: {tm['oppRankMultiplier']:.2f}× ({direction} opposition)")
    if tm.get("overtimeBonus") and tm["overtimeBonus"] > 0:
        tm_lines.append(f"• OT frequency bonus: +{tm['overtimeBonus']:.1f} kills")
    if tm.get("winRateAdj") and abs(tm["winRateAdj"] - 1.0) > 0.01:
        tm_lines.append(f"• Win-rate context: {tm['winRateAdj']:.2f}× (team form)")
    tactical_ctx = "\n".join(tm_lines) if tm_lines else "  (standard)"

    rank_note = f"Opponent world rank: #{opp_rank}" if opp_rank else "Opponent rank: unknown"

    prompt = f"""You are a sharp CS2 esports betting analyst with deep knowledge of counter-strike tactics.

Player: {player_nickname}
Prop: {prop_label} | Line: {line}
Opponent: {opponent or 'TBD'} | {rank_note}
Season avg: {prior_mean:.1f} | Momentum avg: {momentum_mean:.1f}
Model projection: {projection:.1f} → {recommendation.upper()} (P(OVER)={p_over}%, P(UNDER)={p_under}%){(' | ' + streak_flag) if streak_flag else ''}

Tactical factors applied by model:
{tactical_ctx}

Recent match/map log (most recent first):
{game_ctx}

Write a crisp, sharp 2-3 sentence CS2 betting analysis using the tactical data above:
1. Why the model projects {projection:.1f} vs the {line} line (cite KPR, KAST, or round counts if relevant)
2. Key matchup or form factor (opponent tier #{opp_rank or '?'}, entry-fragger role, momentum)
3. Main risk or edge

Be specific. Use CS2 terminology. Return JSON ONLY:
{{"sharpSummary": "<1 tight sentence under 20 words>", "reasoning": "<2-3 sharp sentences with specific numbers>"}}"""

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
        resp   = await asyncio.wait_for(
            client.chat.completions.create(
                model="grok-4-1-fast-non-reasoning",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.4,
            ),
            timeout=12,
        )
        raw = resp.choices[0].message.content.strip()
        m   = re.search(r'\{[\s\S]*\}', raw)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        log.warning(f"[CS2 AI] Grok failed: {e}")

    return {}


# ── Player search ─────────────────────────────────────────────────────────────

@router.get("/players/search")
async def search_players(q: str = Query(..., min_length=2)):
    try:
        players = await cs2_client.search_players(q)
        return [
            {
                "id":       p.get("id"),
                "nickname": p.get("nickname", ""),
                "fullName": p.get("fullName", ""),
                "team":     p.get("team"),
                "isActive": p.get("isActive"),
                "age":      p.get("age"),
            }
            for p in players[:15]
        ]
    except Exception as e:
        log.error(f"CS2 player search error: {e}")
        return []


# ── Team search ───────────────────────────────────────────────────────────────

@router.get("/teams/search")
async def search_teams(q: str = Query(..., min_length=2)):
    try:
        return await cs2_client.search_teams(q)
    except Exception as e:
        log.error(f"CS2 team search error: {e}")
        return []


# ── Rankings ─────────────────────────────────────────────────────────────────

@router.get("/rankings")
async def get_rankings():
    try:
        return await cs2_client.get_rankings(30)
    except Exception as e:
        log.error(f"CS2 rankings error: {e}")
        return []


# ── Predict ───────────────────────────────────────────────────────────────────

class Cs2PredictRequest(BaseModel):
    playerNickname: str
    playerId:       Optional[int]  = None
    teamName:       Optional[str]  = ""
    teamId:         Optional[int]  = None
    propType:       str
    line:           float
    opponentName:   Optional[str]  = ""
    opponentRank:   Optional[int]  = None
    mapName:        Optional[str]  = None   # e.g. "Mirage", "de_nuke" — map pool awareness


@router.post("/predict")
async def cs2_predict(req: Cs2PredictRequest):
    prop_type = req.propType.lower().strip()
    if prop_type not in cs2_engine.CS2_PROPS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown CS2 prop: {prop_type}. Valid: {sorted(cs2_engine.CS2_PROPS)}",
        )
    if req.line <= 0:
        raise HTTPException(status_code=400, detail="Line must be positive.")

    # ── Resolve player ────────────────────────────────────────────────────────
    player_id = req.playerId
    team_id   = req.teamId
    team_name = req.teamName or ""
    nickname  = req.playerNickname.strip()

    if not player_id:
        results = await cs2_client.search_players(nickname)
        if not results:
            raise HTTPException(status_code=404, detail=f"Player '{nickname}' not found in CS2 database.")
        # Pick best match — prefer active, exact nickname match first
        best = None
        nick_low = nickname.lower()
        for p in results:
            if p.get("nickname", "").lower() == nick_low and p.get("isActive"):
                best = p
                break
        if not best:
            best = results[0]
        player_id = best["id"]
        nickname  = best.get("nickname", nickname)
        if best.get("team"):
            team_id   = team_id   or best["team"].get("id")
            team_name = team_name or best["team"].get("name", "")

    if not team_id:
        raise HTTPException(
            status_code=422,
            detail=f"Could not determine team for '{nickname}'. Provide teamId.",
        )

    print(f"[CS2 PREDICT] {nickname} ({player_id}) | {prop_type} {req.line} | team={team_name}({team_id}) | opp={req.opponentName or '?'}")

    # ── Fetch stats (match-level for maps_1_2 props, per-map for everything else) ─
    is_match_level = prop_type in cs2_engine.MATCH_LEVEL_PROPS
    try:
        if is_match_level:
            map_logs = await cs2_client.get_player_recent_match_stats(player_id, team_id, limit=30)
        else:
            map_logs = await cs2_client.get_player_recent_map_stats(player_id, team_id, limit=30)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch CS2 data: {e}")

    if not map_logs:
        data_kind = "match" if is_match_level else "map"
        raise HTTPException(
            status_code=404,
            detail=f"No recent {data_kind} stats found for {nickname}. They may not have played recently.",
        )

    # ── Fetch opponent rank if opponentName given ────────────────────────────
    opp_rank = req.opponentRank
    if req.opponentName and not opp_rank:
        try:
            rankings = await cs2_client.get_rankings(50)
            for r in rankings:
                if req.opponentName.lower() in r.get("team", {}).get("name", "").lower():
                    opp_rank = r.get("rank")
                    break
        except Exception:
            pass

    # ── Run engine ────────────────────────────────────────────────────────────
    result = cs2_engine.compute_cs2_projection(
        map_logs=map_logs,
        prop_type=prop_type,
        line=req.line,
        opponent_rank=opp_rank,
        opponent_name=req.opponentName or None,
        map_name=req.mapName or None,
    )

    if result.get("error") == "insufficient_data":
        raise HTTPException(
            status_code=404,
            detail=f"Insufficient data for {nickname} on {prop_type}. Need at least 2 matches/maps.",
        )

    tactical_metrics = result.get("tacticalMetrics", {})

    # ── AI analysis (non-blocking) ─────────────────────────────────────────────
    ai_task = asyncio.create_task(_get_cs2_ai_analysis(
        player_nickname=nickname,
        prop_type=prop_type,
        line=req.line,
        opponent=req.opponentName or "",
        projection=result["projection"],
        p_over=result["pOver"],
        p_under=result["pUnder"],
        recommendation=result["recommendation"],
        map_logs=map_logs,
        prior_mean=result["priorMean"],
        momentum_mean=result["momentumMean"],
        streak_flag=result.get("streakFlag", ""),
        opp_rank=opp_rank,
        tactical_metrics=tactical_metrics,
    ))

    try:
        ai = await asyncio.wait_for(ai_task, timeout=12)
    except Exception:
        ai = {}

    prop_label = CS2_PROP_LABELS.get(prop_type, prop_type.replace("_", " ").title())

    return {
        "sport":            "cs2",
        "playerName":       nickname,
        "playerId":         player_id,
        "teamName":         team_name,
        "teamId":           team_id,
        "propType":         prop_type,
        "propLabel":        prop_label,
        "line":             req.line,
        "opponentName":     req.opponentName or "",
        "opponentRank":     opp_rank,
        "projection":       result["projection"],
        "pOver":            result["pOver"],
        "pUnder":           result["pUnder"],
        "recommendation":   result["recommendation"],
        "confidenceScore":  result["confidenceScore"],
        "confidenceLevel":  result["confidenceLevel"],
        "priorMean":        result["priorMean"],
        "momentumMean":     result["momentumMean"],
        "sampleSize":       result["sampleSize"],
        "streakFlag":       result.get("streakFlag", ""),
        "sharpSummary":     ai.get("sharpSummary", ""),
        "reasoning":        ai.get("reasoning", ""),
        "gameLogs":         map_logs[:15],
        "bayesianMetrics": {
            "priorMean":       result["priorMean"],
            "momentumMean":    result["momentumMean"],
            "sampleSize":      result["sampleSize"],
            "tacticalMetrics": tactical_metrics,
        },
    }
