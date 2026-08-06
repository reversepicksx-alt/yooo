"""Deterministic position and tactical role resolution for players."""
from config import db

POSITION_RESOLUTION_VERSION = 7


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

    if gpos == "Goalkeeper":
        return None  # GK sub-role determined by team context, not stats

    if gpos == "Midfielder":
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
    """Resolve position using cache and stat fingerprint only."""
    from datetime import datetime, timezone
    from config import POSITION_PROMPT_VERSION
    ROLE_CACHE_TTL_DAYS = 7
    cached = None
    if player_id:
        cached = await db.player_positions.find_one(
            {"playerId": player_id},
            {"_id": 0, "specificPosition": 1, "role": 1, "updatedAt": 1, "promptVersion": 1}
        )
    if not cached and player_name:
        cached = await db.player_positions.find_one(
            {"playerName": player_name},
            {"_id": 0, "specificPosition": 1, "role": 1, "updatedAt": 1, "promptVersion": 1}
        )

    if cached and cached.get("specificPosition") and cached.get("role") not in _GENERIC_ROLES:
        # Manual overrides are permanent — never re-resolve regardless of version or TTL
        if cached.get("source") == "manual_override":
            return cached["specificPosition"], cached.get("role", ""), "cache"
        stored_ver = cached.get("promptVersion", 0)
        if stored_ver >= POSITION_PROMPT_VERSION:
            cached_at = cached.get("updatedAt", "")
            try:
                age_days = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
                ).days
                if age_days < ROLE_CACHE_TTL_DAYS:
                    return cached["specificPosition"], cached.get("role", ""), "cache"
            except Exception:
                return cached["specificPosition"], cached.get("role", ""), "cache"

    # ── Stat fingerprint (instant hint, no AI) ───────────────────────────────
    fingerprint_hint = _stat_fingerprint_role(generic_position, stats)

    # ── Stat fingerprint fallback ─────────────────────────────────────────────
    if fingerprint_hint and generic_position:
        possible_positions = sorted(_GENERIC_TO_SPECIFIC.get(generic_position, set()))
        # Find the first specific position whose valid-role set actually includes
        # the fingerprint hint, so "False 9" resolves to CF not CAM.
        matched_pos = next(
            (pos for pos in possible_positions
             if fingerprint_hint in _POSITION_ROLE_MAP.get(pos, set())),
            possible_positions[0] if possible_positions else ""
        )
        if matched_pos:
            valid_roles = _POSITION_ROLE_MAP.get(matched_pos, set())
            fallback_role = fingerprint_hint if fingerprint_hint in valid_roles else (sorted(valid_roles)[0] if valid_roles else "")
            print(f"[ROLE RESOLVE] {player_name} → {matched_pos} | {fallback_role} (stat_fingerprint)")
            return matched_pos, fallback_role, "stat_fingerprint"

    # Generic-position fallback: return a canonical specific position without
    # a role when stats aren't available, so callers know at least the broad
    # position rather than getting nothing at all.
    _GENERIC_DEFAULT_POS = {
        "Goalkeeper": "GK", "Defender": "CB",
        "Midfielder": "CM", "Attacker": "CF", "Forward": "CF",
    }
    gp = (generic_position or "").strip().title()
    if gp in _GENERIC_DEFAULT_POS:
        default_pos = _GENERIC_DEFAULT_POS[gp]
        print(f"[ROLE RESOLVE] {player_name} → {default_pos} (generic fallback, no stats)")
        return default_pos, "", "generic_fallback"

    print(f"[ROLE RESOLVE] {player_name} → no resolution possible")
    return "", "", "fallback"


# ─────────────────────────────────────────────────────────────────────────────
# Compatibility API — kept for existing batch position lookups
# ─────────────────────────────────────────────────────────────────────────────
async def resolve_position_deterministic(player_name: str, sport: str = "soccer") -> dict:
    """Lightweight cache-first lookup with deterministic fallback."""

    cached = await db.player_positions.find_one(
        {"playerName": player_name}, {"_id": 0, "specificPosition": 1, "role": 1}
    )
    if cached and cached.get("specificPosition"):
        return {"position": cached["specificPosition"], "role": cached.get("role", "")}

    pos, role, _ = await resolve_player_role(player_name)
    if pos:
        return {"position": pos, "role": role}
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
            {"playerName": name}, {"_id": 0, "specificPosition": 1, "role": 1}
        )
        if cached and cached.get("specificPosition"):
            results[name] = {"position": cached["specificPosition"], "role": cached.get("role", "")}
        else:
            unresolved.append(p)

    for p in unresolved:
        name = p.get("playerName", "")
        pos, role, _ = await resolve_player_role(
            name,
            player_id=p.get("playerId", 0),
        )
        if pos:
            results[name] = {"position": pos, "role": role}

    return results
