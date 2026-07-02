"""
CS2 prediction routes — /api/cs2/*
v4 Ultra: LAN/Online · Map KPR · CT/T Side · Enhanced Role · ADR Trend · Form Bias · Underdog Compression
"""
import asyncio
import logging
import json
import re
from fastapi import APIRouter, HTTPException, Query
from models import Cs2PredictRequest
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
    "map1_kills":           "Map 1 Kills",
    "map3_kills":           "Map 3 Kills",
    "map3_headshots":       "Map 3 Headshots",
    "map3_deaths":          "Map 3 Deaths",
    "map3_assists":         "Map 3 Assists",
    "map3_adr":             "Map 3 ADR",
    "maps_1_3_kills":       "Maps 1-3 Kills",
    "maps_1_3_headshots":   "Maps 1-3 Headshots",
}

# Role → human label for AI prompt
_ROLE_LABELS = {
    "awper":         "AWPer (boom-bust, low HS%)",
    "entry_fragger": "Entry Fragger (high FK rate, high KPR)",
    "star_rifler":   "Star Rifler (consistent high KPR)",
    "lurker":        "Lurker (clutch-dependent, flanks)",
    "igl":           "IGL (sacrifices kills for utility/info)",
    "support":       "Support (capped ceiling, very consistent)",
    "rifler":        "Rifler (balanced)",
    "unknown":       "Unknown",
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
    map_name: Optional[str] = None,
    player_team_rank: Optional[int] = None,
    player_team_starts_ct: Optional[bool] = None,
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
            adr    = m.get("adr", 0)
            adr_s  = f" ADR={adr:.0f}" if adr else ""
            ctx_lines.append(
                f"  Map {i+1} ({mn},{won},{rounds}rnd{kpr_s}{adr_s}): {val} {prop_label}"
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

    # Role
    role = tm.get("roleClassification", "unknown")
    role_label = _ROLE_LABELS.get(role, role)
    tm_lines.append(f"• Player role: {role_label}")

    if tm.get("avgHeadshotPct") is not None:
        tm_lines.append(f"• HS%: {tm['avgHeadshotPct']:.0f}% {'→ AWPer signal' if (tm['avgHeadshotPct'] or 100) < 28 else ''}")

    # ADR trend
    if tm.get("careerAdr") and tm.get("recentAdr"):
        adr_delta = tm["recentAdr"] - tm["careerAdr"]
        trend_s = f"+{adr_delta:.1f}" if adr_delta >= 0 else f"{adr_delta:.1f}"
        tm_lines.append(f"• ADR trend: recent {tm['recentAdr']:.0f} vs career {tm['careerAdr']:.0f} ({trend_s})")

    if tm.get("oppRankMultiplier") and tm["oppRankMultiplier"] != 1.0:
        direction = "weaker" if tm["oppRankMultiplier"] > 1.0 else "stronger"
        tm_lines.append(f"• Opponent rank adj: {tm['oppRankMultiplier']:.2f}× ({direction} opposition)")

    if tm.get("underdogCompress") and tm["underdogCompress"] != 1.0:
        gap = (player_team_rank or 0) - (opp_rank or 0)
        label = "underdog compression" if gap > 0 else "favorite boost"
        tm_lines.append(f"• Rank gap factor: {tm['underdogCompress']:.2f}× ({label}, gap={gap:+d})")

    if map_name:
        map_clean = map_name.lower().replace("de_", "").strip()
        tm_lines.append(f"• Map: {map_name} | Expected rounds: {tm.get('mapExpectedRounds', '?')} | KPR factor: {tm.get('mapKprFactor', 1.0):.2f}×")
        if tm.get("mapCtWinRate"):
            ct_pct = round(tm["mapCtWinRate"] * 100, 1)
            side_s = f"CT {ct_pct}% — " + ("CT-favored" if ct_pct > 52 else "T-favored" if ct_pct < 48 else "balanced")
            if player_team_starts_ct is not None:
                side_s += f" | Player team starts: {'CT' if player_team_starts_ct else 'T'} → side adj: {tm.get('mapSideBiasMultiplier', 1.0):.3f}×"
            tm_lines.append(f"• {side_s}")

    if tm.get("overtimeBonus") and tm["overtimeBonus"] > 0:
        tm_lines.append(f"• OT frequency bonus: +{tm['overtimeBonus']:.1f} kills")

    if tm.get("h2hGames", 0) >= 2:
        tm_lines.append(f"• H2H vs {opponent}: {tm['h2hGames']} games, avg {tm.get('h2hAvgKills', '?')} {prop_label} | H2H mult: {tm['h2hFormMult']:.2f}×")

    if tm.get("lanVarMult") and abs(tm["lanVarMult"] - 1.0) > 0.02:
        env = "LAN (structured)" if tm["lanVarMult"] < 1.0 else "Online (volatile)"
        tm_lines.append(f"• Match environment: {env} | Variance factor: {tm['lanVarMult']:.2f}×")

    if tm.get("formWindowBiasMult") and abs(tm["formWindowBiasMult"] - 1.0) > 0.01:
        trend = "hot form" if tm["formWindowBiasMult"] > 1.0 else "cold form"
        tm_lines.append(f"• Recent form bias: {tm['formWindowBiasMult']:.2f}× ({trend})")

    if tm.get("winRateAdj") and abs(tm["winRateAdj"] - 1.0) > 0.01:
        tm_lines.append(f"• Win-rate context: {tm['winRateAdj']:.2f}× (team form)")

    if streak_flag:
        tm_lines.append(f"• Streak: {streak_flag} (P adj: {tm.get('streakPAdj', 0):+.0f}%)")

    tactical_ctx = "\n".join(tm_lines) if tm_lines else "  (standard)"

    rank_note = f"Opponent world rank: #{opp_rank}" if opp_rank else "Opponent rank: unknown"
    team_rank_note = f"Player team rank: #{player_team_rank}" if player_team_rank else ""

    # Build matchup severity warning for large rank gaps
    matchup_warning = ""
    if player_team_rank and opp_rank:
        gap = player_team_rank - opp_rank
        if gap >= 15:
            matchup_warning = f"""
⚠️ MATCHUP CONTEXT — UNDERDOG ALERT (rank gap: +{gap}):
Player's team (#{player_team_rank}) is a SIGNIFICANT UNDERDOG vs #{opp_rank} opponent.
The Bayesian engine has applied underdog compression, but you MUST explicitly weight:
- Historical stats were likely built against WEAKER/MID-TIER opponents (rank 30-80)
- Elite CT setups from the higher-ranked side force eco chains → fewer kill opportunities
- Gun-game disadvantage: eco buys, force buys reduce high-kill-ceiling rounds
- H2H if available: check if player's actual output vs this team is below their season avg
CRITICAL: Do NOT let the player's career average drive the recommendation here — the 
matchup quality gap should meaningfully suppress your OVER conviction."""
        elif gap <= -15:
            matchup_warning = f"""
💪 MATCHUP CONTEXT — FAVORITE BOOST (rank gap: {gap}):
Player's team (#{player_team_rank}) is favored vs #{opp_rank} opponent.
Dominant team typically has more kill opportunities (passive opponent CT, gun game advantage).
Factor this into your confidence assessment."""

    prompt = f"""You are a sharp CS2 esports betting analyst with deep knowledge of Counter-Strike tactics.

Player: {player_nickname} | {team_rank_note}
Prop: {prop_label} | Line: {line}
Opponent: {opponent or 'TBD'} | {rank_note}
Map: {map_name or 'TBD'}
Season avg: {prior_mean:.1f} | Momentum avg: {momentum_mean:.1f}
Model projection: {projection:.1f} → {recommendation.upper()} (P(OVER)={p_over}%, P(UNDER)={p_under}%){matchup_warning}

v4 Tactical factors:
{tactical_ctx}

Recent match/map log (newest first, with ADR and KPR when available):
{game_ctx}

You are explaining WHY the model's verdict is correct — not reaching your own conclusion. The math has already decided: {recommendation.upper()} {projection:.1f} vs {line} line.

Return JSON ONLY (no markdown outside the JSON values):
{{
  "sharpSummary": "<1 decisive sentence under 22 words committing to {recommendation.upper()} — state the single biggest factor>",
  "reasoning": "<2-3 sharp sentences with specific numbers from the data above>",
  "tacticalBreakdown": "## Model Verdict\\n**{recommendation.upper()} {projection:.1f}** vs {line} line — P(OVER)={p_over}%, P(UNDER)={p_under}%\\n\\n## Kill Volume Analysis\\n<2-3 sentences: cite KPR {momentum_mean:.2f}/round, eco-round factor, ADR trend, map pool>\\n\\n## Matchup Context\\n<2-3 sentences: rank gap #{player_team_rank or '?'} vs #{opp_rank or '?'}, LAN/online environment, CT/T-side start, any H2H context>\\n\\n## Recent Form\\n<2 sentences summarising last 3-5 matches with specific kill numbers — support the direction>\\n\\n## Key Risk\\n<1-2 sentences: what single factor could invalidate this pick, and why it is outweighed>"
}}"""

    try:
        from ai_engine import _ai_call as _cs2_ai
        raw = await _cs2_ai(prompt, temperature=0.35, max_tokens=1400, timeout=18, json_mode=True)
        if raw:
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                return json.loads(m.group(0))
    except Exception as e:
        log.warning(f"[CS2 AI] Gemini failed: {e}")

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

@router.post("/predict")
async def cs2_predict(req: Cs2PredictRequest):
    from routes.auth import verify_session
    sess = await verify_session(req)
    if not sess.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or expired session. Please sign in again.")
    access = sess.get("access_type", "")
    if not access or access == "NoSubscription":
        raise HTTPException(status_code=403, detail="Active subscription required")
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

    print(f"[CS2 PREDICT] {nickname} ({player_id}) | {prop_type} {req.line} | team={team_name}({team_id}) | opp={req.opponentName or '?'} | map={req.mapName or '?'} | teamRank={req.playerTeamRank} | startsCT={req.playerTeamStartsCt}")

    # ── Fetch stats ───────────────────────────────────────────────────────────
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
    player_team_rank = req.playerTeamRank
    if req.opponentName and (not opp_rank or not player_team_rank):
        try:
            rankings = await cs2_client.get_rankings(100)
            opp_name_low  = (req.opponentName or "").lower()
            team_name_low = team_name.lower()
            for r in rankings:
                rname = r.get("team", {}).get("name", "").lower()
                if not opp_rank and opp_name_low and opp_name_low in rname:
                    opp_rank = r.get("rank")
                if not player_team_rank and team_name_low and team_name_low in rname:
                    player_team_rank = r.get("rank")
                if opp_rank and player_team_rank:
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
        player_team_rank=player_team_rank,
        player_team_starts_ct=req.playerTeamStartsCt,
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
        map_name=req.mapName or None,
        player_team_rank=player_team_rank,
        player_team_starts_ct=req.playerTeamStartsCt,
    ))

    try:
        ai = await asyncio.wait_for(ai_task, timeout=14)
    except Exception:
        ai = {}

    prop_label = CS2_PROP_LABELS.get(prop_type, prop_type.replace("_", " ").title())

    return {
        "sport":               "cs2",
        "playerName":          nickname,
        "playerId":            player_id,
        "teamName":            team_name,
        "teamId":              team_id,
        "propType":            prop_type,
        "propLabel":           prop_label,
        "line":                req.line,
        "opponentName":        req.opponentName or "",
        "opponentRank":        opp_rank,
        "mapName":             req.mapName or "",
        "playerTeamRank":      player_team_rank,
        "playerTeamStartsCt":  req.playerTeamStartsCt,
        "projection":          result["projection"],
        "pOver":               result["pOver"],
        "pUnder":              result["pUnder"],
        "recommendation":      result["recommendation"],
        "confidenceScore":     result["confidenceScore"],
        "confidenceLevel":     result["confidenceLevel"],
        "priorMean":           result["priorMean"],
        "momentumMean":        result["momentumMean"],
        "sampleSize":          result["sampleSize"],
        "streakFlag":          result.get("streakFlag", ""),
        "sharpSummary":        ai.get("sharpSummary", ""),
        "reasoning":           ai.get("reasoning", ""),
        "tacticalBreakdown":   ai.get("tacticalBreakdown", ""),
        "gameLogs":            map_logs[:15],
        "bayesianMetrics": {
            "priorMean":        result["priorMean"],
            "momentumMean":     result["momentumMean"],
            "sampleSize":       result["sampleSize"],
            "tacticalMetrics":  tactical_metrics,
        },
    }
