"""Web-grounded position and tactical-role verification for soccer players.

Gemini is used here only to confirm identity, position, and role from grounded
web results. Its output is never used to generate prose or directly alter
projection math.
"""
from __future__ import annotations

import asyncio as aio
import json
import os
from datetime import datetime, timezone

from config import db

POSITION_RESOLUTION_VERSION = 8
_POSITION_AI_MODEL = os.environ.get("GEMINI_POSITION_MODEL", "gemini-2.5-flash")
_POSITION_AI_TTL_DAYS = 7
_POSITION_AI_DAILY_LIMIT = max(1, int(os.environ.get("GEMINI_POSITION_DAILY_LIMIT", "100")))
_position_usage_date = ""
_position_usage_attempts = 0
_position_usage_lock = aio.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Canonical vocabularies (kept in sync with predict.py POSITION_ROLE_MAP)
# ─────────────────────────────────────────────────────────────────────────────
_POS_LIST = "GK, CB, LB, RB, LWB, RWB, CDM, CM, CAM, LM, RM, LW, RW, CF, ST, SS"
_ROLE_LIST = (
    "Shot-Stopper, Sweeper Keeper, Ball-Playing CB, Stopper, Fullback, Wing-Back, "
    "Inverted Fullback, Anchor, Box-to-Box, Deep-Lying Playmaker, Ball Winner, "
    "Mezzala, Advanced Playmaker, Wide Playmaker, Traditional Winger, Inverted Winger, "
    "Progressive Carrier, Inside Forward, Target Man, Poacher, False 9, Shadow Striker, "
    "Complete Forward, Pressing Forward"
)
_POSITION_ROLE_MAP = {
    "GK":  {"Shot-Stopper", "Sweeper Keeper"},
    "CB":  {"Ball-Playing CB", "Stopper"},
    "LB":  {"Fullback", "Wing-Back", "Inverted Fullback"},
    "RB":  {"Fullback", "Wing-Back", "Inverted Fullback"},
    "LWB": {"Wing-Back", "Fullback"},
    "RWB": {"Wing-Back", "Fullback"},
    "CDM": {"Anchor", "Ball Winner", "Deep-Lying Playmaker"},
    "CM":  {"Box-to-Box", "Mezzala", "Deep-Lying Playmaker", "Ball Winner"},
    "CAM": {"Advanced Playmaker", "Wide Playmaker", "Shadow Striker"},
    "LM":  {"Wide Playmaker", "Traditional Winger"},
    "RM":  {"Wide Playmaker", "Traditional Winger"},
    "LW":  {"Traditional Winger", "Inverted Winger", "Inside Forward", "Progressive Carrier"},
    "RW":  {"Traditional Winger", "Inverted Winger", "Inside Forward", "Progressive Carrier"},
    "CF":  {"Complete Forward", "False 9", "Target Man", "Pressing Forward"},
    "ST":  {"Poacher", "Target Man", "Complete Forward", "Pressing Forward"},
    "SS":  {"Shadow Striker", "False 9"},
}
_GENERIC_TO_SPECIFIC = {
    "Goalkeeper": {"GK"},
    "Defender":   {"CB", "LB", "RB", "LWB", "RWB"},
    "Midfielder": {"CDM", "CM", "CAM", "LM", "RM"},
    "Attacker":   {"LW", "RW", "CF", "ST", "SS", "CAM"},
}
_VALID_POSITIONS = {
    "GK","CB","LB","RB","LWB","RWB","CDM","CM","CAM",
    "LM","RM","LW","RW","CF","ST","SS",
}
_GENERIC_ROLES = {
    "", "Midfielder", "Defender", "Forward", "Attacker",
    "Goalkeeper", "midfielder", "defender", "forward",
}


def _generic_category(value: object) -> str:
    return {
        "goalkeeper": "Goalkeeper",
        "defender": "Defender",
        "midfielder": "Midfielder",
        "attacker": "Attacker",
        "forward": "Attacker",
    }.get(str(value or "").strip().lower(), "")


def _canonical_position(value: object) -> str:
    text = " ".join(str(value or "").strip().upper().replace("-", " ").split())
    aliases = {
        "GOALKEEPER": "GK", "KEEPER": "GK",
        "CENTRE BACK": "CB", "CENTER BACK": "CB",
        "LEFT BACK": "LB", "RIGHT BACK": "RB",
        "LEFT WING BACK": "LWB", "RIGHT WING BACK": "RWB",
        "DEFENSIVE MIDFIELDER": "CDM", "HOLDING MIDFIELDER": "CDM",
        "CENTRAL MIDFIELDER": "CM", "ATTACKING MIDFIELDER": "CAM",
        "LEFT MIDFIELDER": "LM", "RIGHT MIDFIELDER": "RM",
        "LEFT WINGER": "LW", "RIGHT WINGER": "RW",
        "CENTRE FORWARD": "CF", "CENTER FORWARD": "CF",
        "STRIKER": "ST", "SECOND STRIKER": "SS",
    }
    position = aliases.get(text, text)
    return position if position in _VALID_POSITIONS else ""


def _canonical_role(value: object) -> str:
    text = " ".join(str(value or "").strip().replace("_", " ").split()).lower()
    aliases = {
        "shot stopper": "Shot-Stopper", "sweeper keeper": "Sweeper Keeper",
        "ball playing cb": "Ball-Playing CB", "ball-playing cb": "Ball-Playing CB",
        "stopper": "Stopper", "fullback": "Fullback", "full back": "Fullback",
        "wing back": "Wing-Back", "wing-back": "Wing-Back",
        "inverted fullback": "Inverted Fullback", "anchor": "Anchor",
        "ball winner": "Ball Winner", "deep lying playmaker": "Deep-Lying Playmaker",
        "deep-lying playmaker": "Deep-Lying Playmaker", "box to box": "Box-to-Box",
        "box-to-box": "Box-to-Box", "mezzala": "Mezzala",
        "advanced playmaker": "Advanced Playmaker", "wide playmaker": "Wide Playmaker",
        "traditional winger": "Traditional Winger", "inverted winger": "Inverted Winger",
        "progressive carrier": "Progressive Carrier", "inside forward": "Inside Forward",
        "target man": "Target Man", "poacher": "Poacher", "false 9": "False 9",
        "shadow striker": "Shadow Striker", "complete forward": "Complete Forward",
        "pressing forward": "Pressing Forward",
    }
    return aliases.get(text, "")


def _trusted_cached_profile(cached: dict | None, category: str) -> tuple[str, str] | None:
    """Return a durable grounded/manual profile without requiring freshness.

    Position identity is not a prediction-time feature that should disappear
    when Gemini is temporarily slow.  Once a player-ID record has grounded or
    manual evidence, it remains safe to use until an explicit correction or a
    stronger fixture observation replaces it.
    """
    if not isinstance(cached, dict):
        return None
    position = _canonical_position(cached.get("specificPosition"))
    allowed = _GENERIC_TO_SPECIFIC.get(category, set())
    source = str(cached.get("source") or cached.get("roleSource") or "")
    if (
        not position
        or position not in allowed
        or source not in {
            "gemini_web_grounded",
            "manual_override",
            "api_sports_lineup_history",
        }
    ):
        return None
    role = _canonical_role(cached.get("role"))
    if role not in _POSITION_ROLE_MAP.get(position, set()):
        role = ""
    return position, role


def _grounding_sources(response: object) -> list[dict]:
    def _field(value: object, *names: str):
        if isinstance(value, dict):
            for name in names:
                if name in value:
                    return value[name]
            return None
        for name in names:
            result = getattr(value, name, None)
            if result is not None:
                return result
        return None

    sources: list[dict] = []
    candidates = _field(response, "candidates") or []
    for candidate in candidates:
        # google-genai currently exposes snake_case attributes, while older
        # proxy/test response objects may expose the wire-format camelCase
        # name. Accept both, but still require an actual web URI.
        metadata = _field(candidate, "grounding_metadata", "groundingMetadata")
        chunks = _field(metadata, "grounding_chunks", "groundingChunks") or []
        for chunk in chunks:
            web = _field(chunk, "web", "webGroundingChunk")
            url = _field(web, "uri", "url")
            title = _field(web, "title", "domain")
            if url and not any(item["url"] == url for item in sources):
                sources.append({"url": url, "title": title or ""})
    return sources[:8]


async def _position_ai_budget_available() -> bool:
    global _position_usage_date, _position_usage_attempts
    today = datetime.now(timezone.utc).date().isoformat()
    async with _position_usage_lock:
        if _position_usage_date != today:
            _position_usage_date = today
            _position_usage_attempts = 0
        if _position_usage_attempts >= _POSITION_AI_DAILY_LIMIT:
            return False
        _position_usage_attempts += 1
        return True


async def _verify_with_grounded_gemini(
    player_name: str,
    team_name: str,
    generic_position: str,
) -> dict | None:
    """Ask Gemini only for a web-grounded position/role identity check."""
    if os.environ.get("GEMINI_POSITION_VERIFICATION", "true").lower() not in {
        "1", "true", "yes", "on"
    }:
        return None
    api_key = os.environ.get("AI_INTEGRATIONS_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    base_url = os.environ.get("AI_INTEGRATIONS_GEMINI_BASE_URL")
    if not api_key or not base_url or not await _position_ai_budget_available():
        return None

    prompt = f"""
Use Google Search grounding now to verify the real-world soccer position and
tactical role of exactly this player. You must perform a live web search before
answering and use the search results, not memory.
Player: {player_name}
Current/known club context: {team_name or "unknown"}
Provider broad category: {generic_position or "unknown"}

Search reliable current or recent sources such as the player's official club or
national-team profile, league profile, reputable match reports, or established
football databases. Do not infer position from the requested prop, stats, or
formation assumptions. Avoid same-name players.

Return this compact JSON object only, with no explanation before or after it:
{{
  "position": "one of GK, CB, LB, RB, LWB, RWB, CDM, CM, CAM, LM, RM, LW, RW, CF, ST, SS",
  "role": "one canonical tactical role from the vocabulary below, or empty string",
  "confidence": "high, medium, or low"
}}
Canonical roles: {_ROLE_LIST}
"""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "", "base_url": base_url},
        )
        response = await aio.wait_for(
            aio.to_thread(
                client.models.generate_content,
                model=_POSITION_AI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    maxOutputTokens=500,
                    tools=[types.Tool(googleSearch=types.GoogleSearch())],
                ),
            ),
            # Position grounding is optional identity enrichment. Keep the
            # first attempt short so a slow/unavailable Gemini proxy cannot
            # hold the customer's deterministic prediction hostage.
            timeout=5,
        )
        sources = _grounding_sources(response)
        # Do not retry an ungrounded response in the interactive prediction
        # path. The retry used to add another 20 seconds before falling back
        # to the provider category, while the projection itself was already
        # fully computable from the verified player/fixture data.
        if not sources:
            print(f"[POSITION GEMINI] no grounded result for {player_name}; using provider fallback")
            return None
        raw = str(getattr(response, "text", "") or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.split("```", 1)[0].strip()
        # Search-grounded Gemini may append a short explanatory sentence
        # after the JSON object. Parse the first complete object only.
        json_start = raw.find("{")
        if json_start >= 0:
            raw = raw[json_start:]
        parsed, _ = json.JSONDecoder().raw_decode(raw)
        position = _canonical_position(parsed.get("position"))
        category = _generic_category(generic_position)
        if not position or position not in _GENERIC_TO_SPECIFIC.get(category, set()):
            print(f"[POSITION GEMINI] category mismatch for {player_name}: {position} vs {category}")
            return None
        role = _canonical_role(parsed.get("role"))
        if role not in _POSITION_ROLE_MAP.get(position, set()):
            role = ""
        confidence = str(parsed.get("confidence") or "").lower()
        return {
            "specificPosition": position,
            "role": role,
            "confidence": confidence if confidence in {"high", "medium", "low"} else "medium",
            "sources": sources,
        }
    except Exception as exc:
        print(f"[POSITION GEMINI] verification skipped for {player_name}: {type(exc).__name__}: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Stat fingerprint — fast per-game ratio classifier (no AI, ~0ms)
# ─────────────────────────────────────────────────────────────────────────────
def _stat_fingerprint_role(generic_position: str, stats: dict | None) -> str | None:
    """Derive an approximate role from per-game stat ratios."""
    if not stats:
        return None

    apps = max(1, stats.get("appearances", 1) or 1)
    passes_pg      = ((stats.get("passes_total") or 0) / apps)
    key_passes_pg  = ((stats.get("key_passes")   or 0) / apps)
    tackles_pg     = ((stats.get("tackles_total") or 0) / apps)
    dribbles_pg    = ((stats.get("dribbles_attempts") or 0) / apps)
    shots_pg       = ((stats.get("shots_total")   or 0) / apps)
    clearances_pg  = ((stats.get("clearances")    or 0) / apps)
    goals_pg       = ((stats.get("goals_total")   or 0) / apps)

    gpos = (generic_position or "").strip().title()

    # Broad provider categories cannot support a tactical role. Stats can
    # describe output, but they cannot distinguish CB/LB/RB, CM/CAM, or
    # CF/ST/SS. Exact positions may be used by callers that already have
    # lineup/profile evidence.
    if gpos in {"Goalkeeper", "Defender", "Midfielder", "Attacker", "Forward"}:
        return None
    if gpos in {"Cm", "Cdm", "Cam"}:
        if passes_pg >= 65 and tackles_pg < 3.5 and shots_pg < 1.5:
            return "Deep-Lying Playmaker"
        if tackles_pg >= 6.0:
            return "Ball Winner"
        # Mezzala: cuts into half-space, notable shots + key passes, moderate dribbles
        if shots_pg >= 1.2 and key_passes_pg >= 1.5 and dribbles_pg >= 1.2 and tackles_pg < 4.0:
            return "Mezzala"
        if key_passes_pg >= 2.5 and shots_pg >= 1.5:
            return "Advanced Playmaker"
        if shots_pg >= 1.5 and dribbles_pg >= 1.5:
            return "Box-to-Box"
        if passes_pg >= 50 and tackles_pg < 4.0:
            return "Deep-Lying Playmaker"
        if tackles_pg >= 4.5:
            return "Anchor"
        return None

    if gpos == "Defender":
        # Clearances are the strongest CB signal — fullbacks rarely clear 3+/game
        if clearances_pg >= 3.0:
            return "Ball-Playing CB" if passes_pg >= 50 else "Stopper"
        if passes_pg >= 62:
            return "Ball-Playing CB"
        if clearances_pg >= 2.0:
            return "Stopper"
        # Clearly a CB: very low dribbles/shots and at least some clearances
        if dribbles_pg < 0.8 and shots_pg < 0.4 and clearances_pg >= 1.0:
            return "Stopper"
        if dribbles_pg >= 1.5 and shots_pg >= 0.5:
            return "Inverted Fullback"
        # Low clearances with forward output = fullback
        if clearances_pg < 1.0 and (dribbles_pg >= 0.8 or shots_pg >= 0.3):
            return "Fullback"
        return None

    if gpos in ("Attacker", "Forward"):
        # Creative hub / False 9: drops deep, threads passes, dribbles to create space.
        # Messi-style: high key passes + dribbles AND shots below 2.5 (feeds > shoots).
        if key_passes_pg >= 2.0 and dribbles_pg >= 2.0 and shots_pg < 2.5:
            return "False 9"
        # Inside Forward: cuts in from a wide channel, high dribbles AND high shots
        if dribbles_pg >= 2.5 and shots_pg >= 2.0:
            return "Inside Forward"
        # Inverted Winger: extreme dribble volume from a wide position
        if dribbles_pg >= 3.5 and shots_pg >= 1.5:
            return "Inverted Winger"
        # Poacher: pure finisher — high goals and shots, low dribbles/key passes
        if goals_pg >= 0.45 and shots_pg >= 2.5 and dribbles_pg < 2.0:
            return "Poacher"
        # Complete Forward: well-rounded scorer with some creativity
        if goals_pg >= 0.3 and shots_pg >= 2.0:
            return "Complete Forward"
        # Target Man: high shot volume from static central role, low dribbles
        if shots_pg >= 2.5 and dribbles_pg < 1.5:
            return "Target Man"
        return None

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Core deterministic resolver — cache and stat-fingerprint only
# ─────────────────────────────────────────────────────────────────────────────
async def resolve_player_role(
    player_name: str,
    team_name: str = "",
    generic_position: str = "",
    player_id: int = 0,
    stats: dict | None = None,
) -> tuple[str, str, str]:
    """Resolve position/role with grounded web evidence and category safety."""
    from config import POSITION_PROMPT_VERSION
    category = _generic_category(generic_position)
    allowed_positions = _GENERIC_TO_SPECIFIC.get(category, set())
    cached = None
    projection = {
        "_id": 0,
        "specificPosition": 1,
        "role": 1,
        "source": 1,
        "roleSource": 1,
    }
    if player_id:
        cached = await db.player_positions.find_one(
            {
                "$or": [
                    {"playerId": player_id},
                    {"playerId": str(player_id)},
                ]
            },
            projection,
        )
    if not cached and player_name:
        cached = await db.player_positions.find_one(
            {"playerName": player_name},
            projection,
        )

    cached_profile = _trusted_cached_profile(cached, category)
    if cached_profile:
        cached_pos, cached_role = cached_profile
        return cached_pos, cached_role, "cache"

    verified = await _verify_with_grounded_gemini(player_name, team_name, category)
    if verified:
        fields = {
            "playerId": player_id,
            "playerName": player_name,
            "team": team_name or "",
            "genericPosition": category,
            "specificPosition": verified["specificPosition"],
            "role": verified["role"],
            "confidence": verified["confidence"],
            "evidenceSources": verified["sources"],
            "source": "gemini_web_grounded",
            "promptVersion": POSITION_PROMPT_VERSION,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            cache_key = {"playerId": player_id} if player_id else {"playerName": player_name}
            await db.player_positions.update_one(cache_key, {"$set": fields}, upsert=True)
        except Exception as exc:
            print(f"[POSITION GEMINI CACHE WRITE] skipped for {player_name}: {exc}")
        print(
            f"[POSITION GEMINI] {player_name} → "
            f"{verified['specificPosition']}/{verified['role'] or 'role unavailable'} "
            f"({verified['confidence']})"
        )
        return verified["specificPosition"], verified["role"], "gemini_web_grounded"

    # Provider category is the only non-web fallback. It supplies only the
    # broad observed category and never manufactures an exact position or
    # tactical role.  A generic M/MID row is not evidence for CM/CDM/CAM.
    if category:
        print(
            f"[ROLE RESOLVE] {player_name} → {category} "
            "(provider category fallback; Gemini unavailable)"
        )
        return category, "", "provider_category_fallback"

    print(f"[ROLE RESOLVE] {player_name} → no resolution possible")
    return "", "", "fallback"


# ─────────────────────────────────────────────────────────────────────────────
# Compatibility API — kept for existing batch position lookups
# ─────────────────────────────────────────────────────────────────────────────
async def resolve_position_deterministic(player_name: str, sport: str = "soccer") -> dict:
    """Compatibility lookup that only trusts grounded/manual identity records."""
    cached = await db.player_positions.find_one(
        {"playerName": player_name},
        {"_id": 0, "specificPosition": 1, "role": 1, "source": 1},
    )
    if cached and cached.get("specificPosition") and cached.get("source") in {
        "gemini_web_grounded",
        "manual_override",
        "api_sports_lineup_history",
    }:
        return {"position": cached["specificPosition"], "role": cached.get("role", "")}

    return {"position": "", "role": ""}


async def resolve_positions_batch(players: list) -> dict:
    """Batch-resolve positions for multiple players."""
    if not players:
        return {}

    results = {}
    unresolved = []
    for p in players:
        name = p.get("playerName", "")
        if not name:
            continue
        cached = await db.player_positions.find_one(
            {"playerName": name},
            {"_id": 0, "specificPosition": 1, "role": 1, "source": 1},
        )
        if (
            cached
            and cached.get("specificPosition")
            and cached.get("source") in {
                "gemini_web_grounded",
                "manual_override",
                "api_sports_lineup_history",
            }
        ):
            results[name] = {"position": cached["specificPosition"], "role": cached.get("role", "")}
        else:
            unresolved.append(p)

    for p in unresolved:
        name = p.get("playerName", "")
        pos, role, _ = await resolve_player_role(
            name,
            team_name=p.get("teamName") or p.get("team") or "",
            generic_position=p.get("genericPosition") or p.get("position") or "",
            player_id=p.get("playerId", 0),
        )
        if pos:
            results[name] = {"position": pos, "role": role}

    return results
