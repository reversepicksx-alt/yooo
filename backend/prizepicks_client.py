"""
PrizePicks public API client — no auth required.
Fetches real-time soccer/World Cup player prop lines and caches them in MongoDB.
Used as a market-reference layer on every prediction: surfaces live PrizePicks
lines, tier (standard / demon / goblin), and line movement.
"""

import asyncio
import json as _json
import unicodedata
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

# ── Soccer-related league IDs on PrizePicks ──────────────────────────────────
SOCCER_LEAGUE_IDS = {241, 82, 458, 14, 243, 242, 457, 262}
# 241=WORLD CUP, 82=SOCCER, 458=WORLD CUP 1H, 14=EPL, 243=SOCCER2H,
# 242=SOCCER1H, 457=WORLD CUP TRNY, 262=SOCCERSZN

# ── PrizePicks stat_type → internal propType ──────────────────────────────────
PP_TO_INTERNAL: dict[str, str] = {
    "Passes Attempted":          "pass_attempts",
    "Passes Attempted (Combo)":  "pass_attempts",
    "Goals":                     "goals",
    "Assists":                   "assists",
    "Goal + Assist":             "goal_assist",
    "Shots":                     "shots_total",
    "Shots (Combo)":             "shots_total",
    "Shots On Target":           "shots_on_target",
    "Shots On Target (Combo)":   "shots_on_target",
    "Tackles":                   "tackles",
    "Clearances":                "clearances",
    "Clearances (Combo)":        "clearances",
    "Fouls":                     "fouls",
    "Fouls Drawn":               "fouls_drawn",
    "Goalie Saves":              "saves",
    "Goalie Saves (Combo)":      "saves",
    "Crosses":                   "crosses",
    "Offsides":                  "offsides",
    "Cards":                     "cards",
    "Attempted Dribbles":        "dribbles_attempted",
    "Shots Assisted":            "shots_assisted",
    "Goals Allowed":             "goals_allowed",
    "Goals Allowed (Combo)":     "goals_allowed",
    "Goalie Fantasy Score":      "goalie_fantasy_score",
    "Outfield Fantasy Score":    "outfield_fantasy_score",
}

# Reverse: internal propType → canonical PrizePicks stat name
INTERNAL_TO_PP: dict[str, str] = {}
for _pp, _int in PP_TO_INTERNAL.items():
    if "(Combo)" not in _pp and _int not in INTERNAL_TO_PP:
        INTERNAL_TO_PP[_int] = _pp

# Extra aliases for alternate internal names
INTERNAL_TO_PP.update({
    "shots":           "Shots",
    "shots_off_target": "Shots",
    "key_passes":      "Passes Attempted",
    "total_passes":    "Passes Attempted",
    "goal_assists":    "Goal + Assist",
})

_SUPPORTED_INTERNALS = set(INTERNAL_TO_PP.keys()) | set(PP_TO_INTERNAL.values())

# curl --http2 with iOS Safari UA bypasses PrizePicks PerimeterX bot protection.
# Direct Python HTTP clients (aiohttp/requests) return 403 due to TLS fingerprint.
_CURL_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_CURL_HEADERS = [
    "-H", "Accept: application/json, text/plain, */*",
    "-H", "Accept-Language: en-US,en;q=0.9",
    "-H", "Origin: https://app.prizepicks.com",
    "-H", "Referer: https://app.prizepicks.com/",
]


async def _curl_get(url: str, timeout: int = 30) -> dict | None:
    """Async curl GET with HTTP/2 + iOS Safari UA. Returns parsed JSON or None."""
    cmd = [
        "curl", "-s", "--http2", "--compressed",
        "-A", _CURL_UA,
        *_CURL_HEADERS,
        "--max-time", str(timeout),
        url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
        if not stdout:
            return None
        return _json.loads(stdout)
    except Exception as exc:
        print(f"[PP] curl GET failed for {url}: {exc}")
        return None

_TIER_COLOR = {
    "demon":    "#FF6B35",
    "goblin":   "#39FF14",
    "standard": "#60A5FA",
}

_TIER_SIGNAL = {
    "demon":    "Market tilted OVER hard — lean UNDER",
    "goblin":   "Market tilted UNDER hard — lean OVER",
    "standard": "Fair market line — no tier tilt",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    """Strip accents, lowercase, remove non-alphanumeric."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", "", s.lower().strip())


# ─────────────────────────────────────────────────────────────────────────────
# Fetch
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_league(league_id: int) -> list[dict]:
    url = (
        "https://api.prizepicks.com/projections"
        f"?league_id={league_id}&per_page=10000&single_stat=true&game_mode=pickem"
    )
    raw = await _curl_get(url, timeout=45)
    if not raw:
        return []

    data     = raw.get("data", [])
    included = raw.get("included", [])

    players = {i["id"]: i["attributes"] for i in included if i["type"] == "new_player"}
    games   = {i["id"]: i["attributes"] for i in included if i["type"] == "game"}

    out: list[dict] = []
    for proj in data:
        attr = proj.get("attributes", {})
        rels = proj.get("relationships", {})

        stat_type = attr.get("stat_type", "")
        internal  = PP_TO_INTERNAL.get(stat_type)
        if not internal:
            continue

        pid = rels.get("new_player", {}).get("data", {}).get("id")
        gid = rels.get("game",       {}).get("data", {}).get("id")

        player = players.get(pid, {})
        game   = games.get(gid,   {})

        pname = player.get("display_name") or player.get("name", "")
        if not pname:
            continue

        game_meta  = game.get("metadata", {})
        game_teams = game_meta.get("game_info", {}).get("teams", {})

        out.append({
            "player_name":      pname,
            "player_name_norm": _normalize(pname),
            "player_team":      player.get("team", ""),
            "stat_type":        stat_type,
            "stat_internal":    internal,
            "line":             attr.get("line_score"),
            "flash_line":       attr.get("flash_sale_line_score"),
            "odds_type":        attr.get("odds_type", "standard"),
            "status":           attr.get("status", ""),
            "is_live":          attr.get("is_live", False),
            "opponent":         attr.get("description", ""),
            "home_team":        game_teams.get("home", {}).get("abbreviation", ""),
            "away_team":        game_teams.get("away", {}).get("abbreviation", ""),
            "game_start":       attr.get("start_time", ""),
            "league":           player.get("league", ""),
            "league_id":        league_id,
            "_refreshed":       datetime.now(timezone.utc),
        })
    return out


async def refresh_prizepicks_board(db) -> int:
    """
    Fetch all active soccer/WC projections from PrizePicks and cache in MongoDB.
    Safe to call any time — does a full replace of the board collection.
    Returns the number of props cached.
    """
    leagues_raw = await _curl_get("https://api.prizepicks.com/leagues", timeout=15)
    if not leagues_raw:
        print("[PP] Leagues discovery failed")
        return 0

    active = [
        int(l["id"])
        for l in leagues_raw.get("data", [])
        if int(l["id"]) in SOCCER_LEAGUE_IDS
        and l["attributes"].get("projections_count", 0) > 0
    ]

    if not active:
        print("[PP] No active soccer leagues right now — board unchanged")
        return 0

    print(f"[PP] Active soccer leagues: {active}")
    batches = await asyncio.gather(*[_fetch_league(lid) for lid in active])

    all_props = [p for batch in batches for p in batch]
    if not all_props:
        print("[PP] Board fetch returned 0 props")
        return 0

    coll = db.prizepicks_board
    await coll.delete_many({})
    await coll.insert_many(all_props)
    try:
        await coll.create_index([("stat_internal", 1), ("player_name_norm", 1)])
    except Exception:
        pass

    await db.prizepicks_meta.replace_one(
        {"_id": "board_refresh"},
        {"_id": "board_refresh", "ts": datetime.now(timezone.utc), "count": len(all_props)},
        upsert=True,
    )
    print(f"[PP] Board cached: {len(all_props)} soccer props across {len(active)} leagues")
    return len(all_props)


# ─────────────────────────────────────────────────────────────────────────────
# Lookup
# ─────────────────────────────────────────────────────────────────────────────

async def lookup_player_prop(db, player_name: str, prop_type: str) -> dict | None:
    """
    Fuzzy-match player_name + prop_type against the cached PrizePicks board.
    Returns the best-matching prop dict or None (threshold: 0.55 similarity).
    """
    if not player_name or not prop_type:
        return None
    if prop_type not in _SUPPORTED_INTERNALS:
        return None  # unsupported sport/prop

    coll = db.prizepicks_board
    candidates = await coll.find(
        {"stat_internal": prop_type},
        {"_id": 0},
    ).to_list(500)

    if not candidates:
        return None

    query_norm  = _normalize(player_name)
    query_parts = query_norm.split()
    query_last  = query_parts[-1] if query_parts else query_norm

    best_score = 0.0
    best: dict | None = None

    for c in candidates:
        c_norm  = c.get("player_name_norm", "")
        c_parts = c_norm.split()
        c_last  = c_parts[-1] if c_parts else c_norm

        score = SequenceMatcher(None, query_norm, c_norm).ratio()

        if query_last and c_last and query_last == c_last:
            score = min(1.0, score + 0.15)
        if len(query_norm) >= 4 and query_norm in c_norm:
            score = min(1.0, score + 0.1)

        if score > best_score:
            best_score = score
            best = c

    if best_score < 0.55:
        print(f"[PP] No board match: '{player_name}' / {prop_type} (best={best_score:.2f})")
        return None

    print(
        f"[PP] Match: '{player_name}' → '{best['player_name']}' "
        f"line={best['line']} {best['odds_type']} (score={best_score:.2f})"
    )
    return best


# ─────────────────────────────────────────────────────────────────────────────
# Context builders
# ─────────────────────────────────────────────────────────────────────────────

def build_pp_context_string(pp: dict, entered_line: float) -> str:
    """Build the AI-prompt block for PrizePicks market context."""
    pp_line   = pp.get("line")
    flash     = pp.get("flash_line")
    odds_type = (pp.get("odds_type") or "standard").upper()
    team      = pp.get("player_team", "")
    opponent  = pp.get("opponent", "")

    if pp_line is None:
        return ""

    diff = round(float(pp_line) - float(entered_line), 2)
    if diff == 0:
        movement = f"User-entered line ({entered_line}) matches the PrizePicks board exactly."
    elif diff > 0:
        movement = (
            f"User-entered line ({entered_line}) is {diff} BELOW the PrizePicks market line ({pp_line}). "
            f"The line has moved down — market may have shifted toward UNDER."
        )
    else:
        movement = (
            f"User-entered line ({entered_line}) is {abs(diff)} ABOVE the PrizePicks market line ({pp_line}). "
            f"The line has moved up — market may have shifted toward OVER."
        )

    tier_meanings = {
        "STANDARD": "Standard tier — PrizePicks considers this a fair, symmetric line.",
        "DEMON":    "DEMON tier — PrizePicks has raised this line vs. normal. The market believes OVER is hard to hit. This is a strong lean-UNDER signal.",
        "GOBLIN":   "GOBLIN tier — PrizePicks has lowered this line vs. normal. The market believes UNDER is hard to hit. This is a strong lean-OVER signal.",
    }
    tier_note   = tier_meanings.get(odds_type, "")
    flash_note  = f" (⚡ Flash sale line: {flash})" if flash and flash != pp_line else ""
    opp_note    = f" | vs {opponent}" if opponent else ""
    team_note   = f" | Team: {team}" if team else ""

    return (
        f"[PRIZEPICKS MARKET REFERENCE — Real-time board data]\n"
        f"PrizePicks Line: {pp_line}{flash_note} | Tier: {odds_type}{team_note}{opp_note}\n"
        f"{movement}\n"
        f"Tier signal: {tier_note}\n"
        f">>> IMPORTANT: Tier is market intelligence. DEMON = market tilted OVER hard, lean UNDER. "
        f"GOBLIN = market tilted UNDER hard, lean OVER. Weight this alongside the Bayesian projection. <<<"
    )


def build_pp_response(pp: dict, entered_line: float) -> dict:
    """Build the prizePicksContext object included in the API response."""
    pp_line   = pp.get("line")
    odds_type = pp.get("odds_type", "standard")

    if pp_line is None:
        return {}

    diff = round(float(pp_line) - float(entered_line), 2)

    return {
        "marketLine":    pp_line,
        "flashLine":     pp.get("flash_line"),
        "marketTier":    odds_type,
        "lineMovement":  diff,
        "tierSignal":    _TIER_SIGNAL.get(odds_type, ""),
        "tierColor":     _TIER_COLOR.get(odds_type, "#60A5FA"),
        "ppPlayer":      pp.get("player_name", ""),
        "ppTeam":        pp.get("player_team", ""),
        "ppOpponent":    pp.get("opponent", ""),
        "ppLeague":      pp.get("league", ""),
        "gameStart":     pp.get("game_start", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Background refresh loop
# ─────────────────────────────────────────────────────────────────────────────

async def prizepicks_refresh_loop(db) -> None:
    """Background task: refresh PrizePicks board every 4 hours."""
    print("[PP] PrizePicks market reference loop started")

    # Skip initial fetch if we already have a fresh board
    try:
        meta = await db.prizepicks_meta.find_one({"_id": "board_refresh"})
        if meta and meta.get("ts"):
            ts = meta["ts"]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            if age_h < 4:
                count = meta.get("count", 0)
                print(f"[PP] Board fresh ({age_h:.1f}h old, {count} props) — skipping startup fetch")
                await asyncio.sleep(max(60, (4 - age_h) * 3600))
    except Exception:
        pass

    while True:
        try:
            await refresh_prizepicks_board(db)
        except Exception as exc:
            print(f"[PP] Refresh loop error: {exc}")
        await asyncio.sleep(4 * 3600)
