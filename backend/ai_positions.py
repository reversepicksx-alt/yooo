"""AI-powered position and tactical role resolution for players.

Provides two resolution tiers:
  1. resolve_player_role()   — comprehensive, web-search-grounded Gemini call with
                               stat fingerprint cross-check. Used by the new
                               /players/resolve-role endpoint so the role is known
                               at player-selection time (before prediction runs).
  2. resolve_position_ai()   — lightweight cache-first lookup; falls back to a
                               Gemini knowledge call. Used by batch position lookups.

Cache: MongoDB player_positions collection, keyed by playerId (primary) or
playerName (fallback). TTL 30 days for standard, 7 days for resolve-role calls
that want freshness.  POSITION_PROMPT_VERSION bump (config.py) forces re-resolution.
"""
import json
from config import db, XAI_API_KEY

GROK_POS_PROMPT_VERSION = 6


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
    """Derive an approximate role from per-game stat ratios.

    Returns a role label string, or None if stats are insufficient.
    Used as a hint inside the Gemini prompt — never overrides AI output.
    """
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
        if dribbles_pg >= 3.0 and shots_pg >= 2.5:
            return "Inverted Winger or Inside Forward"
        if goals_pg >= 0.4 and shots_pg >= 2.5:
            return "Poacher or Complete Forward"
        if key_passes_pg >= 2.0 and shots_pg < 1.5:
            return "False 9"
        return None

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Core AI resolver — Gemini knowledge call with rich stats-aware prompt
# ─────────────────────────────────────────────────────────────────────────────
async def resolve_player_role(
    player_name: str,
    team_name: str = "",
    generic_position: str = "",
    player_id: int = 0,
    stats: dict | None = None,
) -> tuple[str, str, str]:
    """Comprehensive role resolution using Gemini knowledge + stat fingerprint.

    Returns (specific_position, role, source).
    source is one of: "cache" | "ai_knowledge" | "stat_fingerprint" | "fallback"

    Caches result in db.player_positions keyed by playerId (if provided) or playerName.
    """
    from datetime import datetime, timezone
    from config import POSITION_PROMPT_VERSION

    ROLE_CACHE_TTL_DAYS = 7  # Fresher than the 30-day prediction-time TTL

    # ── Cache check ──────────────────────────────────────────────────────────
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

    # ── Build stats evidence string ──────────────────────────────────────────
    stats_evidence = ""
    if stats:
        apps = max(1, stats.get("appearances", 1) or 1)
        passes_pg     = round((stats.get("passes_total")       or 0) / apps, 1)
        key_pg        = round((stats.get("key_passes")         or 0) / apps, 1)
        tackles_pg    = round((stats.get("tackles_total")      or 0) / apps, 1)
        dribbles_pg   = round((stats.get("dribbles_attempts")  or 0) / apps, 1)
        shots_pg      = round((stats.get("shots_total")        or 0) / apps, 1)
        goals_pg      = round((stats.get("goals_total")        or 0) / apps, 1)
        assists_pg    = round((stats.get("goals_assists")       or 0) / apps, 1)
        clearances_pg = round((stats.get("clearances")         or 0) / apps, 1)
        interceptions_pg = round((stats.get("tackles_interceptions") or 0) / apps, 1)
        stats_evidence = (
            f"\n\nSEASON STAT FINGERPRINT (per game over {apps} appearances):\n"
            f"  Passes: {passes_pg}  Key passes: {key_pg}  Shots: {shots_pg}\n"
            f"  Tackles: {tackles_pg}  Interceptions: {interceptions_pg}  Dribbles: {dribbles_pg}\n"
            f"  Clearances: {clearances_pg}  Goals: {goals_pg}  Assists: {assists_pg}\n"
            f"  → Clearances/game: {clearances_pg} (≥2.0 = strong CB indicator; <1.0 with forward output = fullback)\n"
            f"  → Stat-derived hint: {fingerprint_hint or 'insufficient data'}"
        )

    # ── Category hint ────────────────────────────────────────────────────────
    category_hint = ""
    if generic_position and generic_position not in ("", "Unknown"):
        suggested = _GENERIC_TO_SPECIFIC.get(generic_position, set())
        if suggested:
            category_hint = (
                f"\nAPI category: {generic_position} "
                f"(likely specific positions: {', '.join(sorted(suggested))}). "
                f"Stats and knowledge may override — use full position list."
            )

    # ── Gemini prompt ────────────────────────────────────────────────────────
    team_ctx = f" at {team_name}" if team_name else ""
    prompt = f"""You are a professional football/soccer tactical analyst. Identify the PRIMARY specific position and EXACT tactical role for:

Player: {player_name}{team_ctx}
{category_hint}{stats_evidence}

ROLE IDENTIFICATION GUIDE:
DEFENSIVE MID / CDM:
• Deep-Lying Playmaker / Regista: sits DEEPEST, highest pass volume on team (65+/game), very high accuracy, orchestrates build-up, LOW shots, LOW tackles. Examples: Rodri, Busquets, Kroos, Thiago.
• Anchor: positional destroyer, moderate passes, holds shape, fewer tackles than Ball Winner but disciplined. Examples: Casemiro (peak Real Madrid), Fabinho, Laimer.
• Ball Winner: PHYSICAL tackler, highest tackle/interception count (5+/game), wins duels, low-to-moderate passing. Examples: Kanté, Ndidi, Caicedo, Soumaré.

CENTRAL MID / CM:
• Box-to-Box: engine role — balanced defensive work AND forward runs, moderate key passes, moderate shots, noticeable dribbles. Examples: Milinkovic-Savic, Vieira, Gerrard.
• Mezzala: half-space runner — attacks from CM, higher shots + key passes than Box-to-Box, cuts into penalty area, moderate dribbles. Examples: Pedri, Gavi, Nainggolan, Müller.
• Deep-Lying Playmaker (CM): same as CDM version but positioned slightly higher. Examples: Xavi (late career), Verratti, Modric (deep phases).

ATTACKING MID / CAM:
• Advanced Playmaker: primary creator, HIGH key passes (2.5+/game), assists, drops between lines to receive. Examples: Bruno Fernandes, De Bruyne, Özil.
• Shadow Striker: goal-threat CAM, HIGH shots (2+/game), runs beyond the striker. Examples: Müller (as SS), Maddison.
• Wide Playmaker: wide-positioned creator who combines rather than beats defenders. Examples: David Neres, Bernardo Silva (wide).

WINGERS / LW / RW:
• Inverted Winger: cuts INSIDE onto stronger foot to shoot, HIGH shots (2.5+/game), LOW crossing. Examples: Salah (RW→LW), Robben, Gnabry, Sané.
• Inside Forward: cuts inside but also creates — mix of key passes + shots. Examples: Vinicius Jr, Son Heung-min, Díaz.
• Traditional Winger: stays WIDE, HIGH crossing, beats defender on the outside. Examples: Trippier (wing), Willian.
• Progressive Carrier: drives forward with ball, high progressive carries + dribbles, creates from movement rather than crosses. Examples: Leão, Mbappé (wide).

STRIKERS / ST / CF:
• Poacher: penalty-box finisher, highest goals/shot ratio, minimal build-up. Examples: Haaland, Inzaghi, Vardy.
• Complete Forward: hold-up + goals + link play + pressing. Examples: Kane, Suárez, Lukaku.
• Target Man: aerial threat, wins flick-ons, brings others into play. Examples: Giroud, Benteke, Drogba.
• Pressing Forward: leads press from front, high press intensity, tracks back. Examples: Firmino (pressing phase), Rashford (Man Utd).
• False 9: drops deep, creates chances, low shots but high key passes and dribbles. Examples: Messi (Barca 2009-12), Coutinho.

DEFENDERS — CRITICAL DISTINCTION (CB vs fullback):
CB (Centre-Back) is the code for ALL central defenders regardless of which side of the back-4 they occupy.
A CB who plays "right-sided" in a back-4 is STILL a CB, NEVER an RB.
RB and LB are WIDE defenders (fullbacks) whose primary job is to overlap forward and provide width.
KEY SIGNAL: Clearances/game ≥2 = almost certainly CB. Low clearances + forward runs = fullback/wing-back.

• Ball-Playing CB (position=CB): high passes (60+/game) + high clearances, comfortable on ball, plays out from back. Examples: Rúben Dias, John Stones, Van Dijk.
• Stopper (position=CB): dominant aerial/duel CB, clears danger constantly (3+/game clearances), fewer passes. Examples: Kompany, Botman, Akanji, Finn Surman (Portland Timbers).
• Inverted Fullback (position=RB or LB): fullback who tucks INSIDE into midfield rather than overlapping. Low clearances. Examples: Trent Alexander-Arnold, Cancelo (left).
• Wing-Back (position=RWB or LWB): wide defender who advances CONSTANTLY up the flank. Low clearances. Examples: Theo Hernandez, Reece James, Dest.
• Fullback (position=RB or LB): traditional balanced fullback — defends + overlaps moderately. Low clearances (<1.5/game). Examples: Jordi Alba, Robertson (standard phase).

GOALKEEPER:
• Sweeper Keeper: actively comes off line, plays high defensive line, distributes quickly. Examples: Alisson, Ederson, ter Stegen.
• Shot-Stopper: traditional GK focused on shot prevention, stays on line. Examples: Courtois, Oblak.

Position codes: {_POS_LIST}
Role labels: {_ROLE_LIST}

Reply with EXACTLY this format on ONE line (no explanation, no markdown):
POSITION|ROLE"""

    # ── AI call ──────────────────────────────────────────────────────────────
    try:
        from config import AI_BACKGROUND_ENRICHMENT_ENABLED
        if not AI_BACKGROUND_ENRICHMENT_ENABLED:
            raise RuntimeError("background position AI disabled")
        from ai_engine import _ai_call
        sys_msg = (
            "You are a football/soccer tactical analyst. Reply in EXACTLY this format on one line:\n"
            "POSITION|ROLE\nNothing else. No markdown, no explanation."
        )
        raw = await _ai_call(
            prompt, system=sys_msg,
            temperature=0, max_tokens=20, timeout=20,
        )
        if raw:
            parts = raw.strip().split("|")
            pos = parts[0].strip().upper().replace(".", "").replace(",", "").replace(" ", "") if parts else ""
            role = parts[1].strip() if len(parts) > 1 else ""

            # Validate position
            if pos not in _VALID_POSITIONS:
                # Try common synonyms
                _SYN = {"GKP": "GK", "GOALKEEPER": "GK", "DEFENDER": "CB", "MIDFIELDER": "CM", "ATTACKER": "ST"}
                pos = _SYN.get(pos, "")

            if pos:
                # Validate role for this position
                valid_roles = _POSITION_ROLE_MAP.get(pos, set())
                if role and valid_roles and role not in valid_roles:
                    # prefer stat fingerprint hint if it fits, else alphabetical first
                    if fingerprint_hint and fingerprint_hint in valid_roles:
                        role = fingerprint_hint
                    else:
                        role = sorted(valid_roles)[0]
                elif not role and valid_roles:
                    if fingerprint_hint and fingerprint_hint in valid_roles:
                        role = fingerprint_hint
                    else:
                        role = sorted(valid_roles)[0]

                # Cache result
                from datetime import datetime, timezone
                cache_doc = {
                    "playerName": player_name,
                    "specificPosition": pos,
                    "role": role,
                    "promptVersion": GROK_POS_PROMPT_VERSION,
                    "source": "ai_knowledge",
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }
                if player_id:
                    cache_doc["playerId"] = player_id
                if team_name:
                    cache_doc["team"] = team_name
                if generic_position:
                    cache_doc["genericPosition"] = generic_position

                await db.player_positions.update_one(
                    {"playerId": player_id} if player_id else {"playerName": player_name},
                    {"$set": cache_doc},
                    upsert=True,
                )
                print(f"[ROLE RESOLVE] {player_name} → {pos} | {role} (ai_knowledge, fingerprint={fingerprint_hint})")
                return pos, role, "ai_knowledge"

    except Exception as e:
        print(f"[ROLE RESOLVE] AI failed for {player_name}: {e}")

    # ── Stat fingerprint fallback ─────────────────────────────────────────────
    if fingerprint_hint and generic_position:
        suggested = sorted(_GENERIC_TO_SPECIFIC.get(generic_position, set()))
        fallback_pos = suggested[0] if suggested else ""
        if fallback_pos:
            valid_roles = _POSITION_ROLE_MAP.get(fallback_pos, set())
            fallback_role = fingerprint_hint if fingerprint_hint in valid_roles else (sorted(valid_roles)[0] if valid_roles else "")
            print(f"[ROLE RESOLVE] {player_name} → {fallback_pos} | {fallback_role} (stat_fingerprint)")
            return fallback_pos, fallback_role, "stat_fingerprint"

    print(f"[ROLE RESOLVE] {player_name} → no resolution possible")
    return "", "", "fallback"


# ─────────────────────────────────────────────────────────────────────────────
# Legacy API — kept for backward compatibility with batch position lookups
# ─────────────────────────────────────────────────────────────────────────────
async def resolve_position_ai(player_name: str, sport: str = "soccer") -> dict:
    """Lightweight cache-first lookup, then Gemini knowledge fallback.
    Returns {"position": "XX", "role": "..."} or empty strings if failed."""

    cached = await db.player_positions.find_one(
        {"playerName": player_name}, {"_id": 0, "specificPosition": 1, "role": 1}
    )
    if cached and cached.get("specificPosition"):
        return {"position": cached["specificPosition"], "role": cached.get("role", "")}

    pos, role, _ = await resolve_player_role(player_name)
    if pos:
        return {"position": pos, "role": role}
    return {"position": "", "role": ""}


async def resolve_positions_ai_batch(players: list) -> dict:
    """Batch-resolve positions for multiple players.
    Input: [{"playerName": "...", "sport": "soccer", "playerId": optional}]
    Returns: {"PlayerName": {"position": "XX", "role": "..."}, ...}
    """
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


# Backward-compat alias used by some import sites
_grok_resolve_batch = resolve_positions_ai_batch
