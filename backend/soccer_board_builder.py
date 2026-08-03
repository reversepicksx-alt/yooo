"""
Native Reverse Picks Soccer Board Builder
==========================================
Builds a cached prop discovery board for upcoming soccer fixtures.

Pipeline:
  1. Fetch upcoming fixtures for configured leagues (Argentina 128 initially)
  2. For each team, fetch their last N completed fixtures from the same league
  3. Fetch /fixtures/players for each historical fixture to collect per-player stats
  4. Compute a weighted rolling average (same 0.93-decay as bayesian_engine momentum)
     as the "projected line" for shots, passes (pass_attempts), and GK saves
  5. Enrich with L5/L10 hit rates from db.picks (settled picks only)
  6. Cache the full document in db.soccer_board_cache with updatedAt timestamp

The board endpoint serves instantly from cache. A background loop rebuilds
every 6 hours without blocking any user request.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger("soccer_board")

# ── League configuration ────────────────────────────────────────────────────
BOARD_LEAGUES = [
    {"leagueId": 128, "name": "Liga Profesional Argentina", "country": "Argentina"},
]

# ── Props to compute for each player ────────────────────────────────────────
BOARD_PROPS = [
    # key = propType used throughout the app (picks DB, Bayesian engine)
    {"key": "shots",         "label": "Shots",    "field": "shots_total",  "position": "any"},
    {"key": "pass_attempts", "label": "Passes",   "field": "passes_total", "position": "any"},
    {"key": "saves",         "label": "GK Saves", "field": "goals_saves",  "position": "G"},
]

_DECAY       = 0.93   # exponential decay per game (newest game = weight 1.0)
_MIN_GAMES   = 3      # minimum games needed to include a player on the board
_MAX_HIST    = 8      # max historical fixtures to fetch per team
_MAX_FIX_PER_LEAGUE = 6  # max upcoming fixtures to include per league


# ── Main builder ─────────────────────────────────────────────────────────────
async def build_soccer_board(db) -> dict:
    """
    Build the board for all BOARD_LEAGUES and persist to db.soccer_board_cache.
    Returns the cached document (without _id).
    """
    from utils import api_football_request, is_quota_exhausted

    if is_quota_exhausted():
        log.warning("[BOARD] API quota exhausted — serving stale cache")
        cached = await db.soccer_board_cache.find_one({"_id": "main"}, {"_id": 0})
        return cached or {"fixtures": [], "updatedAt": None}

    now     = datetime.now(timezone.utc)
    today   = now.strftime("%Y-%m-%d")
    end_dt  = (now + timedelta(days=3)).strftime("%Y-%m-%d")

    all_fixtures = []

    for league_cfg in BOARD_LEAGUES:
        league_id   = league_cfg["leagueId"]
        league_name = league_cfg["name"]

        # ── 1. Upcoming fixtures ─────────────────────────────────────────────
        season = now.year
        upcoming = await api_football_request("fixtures", {
            "league": league_id,
            "season": season,
            "from":   today,
            "to":     end_dt,
            "status": "NS",
        })
        if not upcoming:
            # Try previous season (e.g. Apertura/Clausura that started last year)
            season -= 1
            upcoming = await api_football_request("fixtures", {
                "league": league_id,
                "season": season,
                "from":   today,
                "to":     end_dt,
                "status": "NS",
            })
        if not upcoming:
            log.info(f"[BOARD] No upcoming fixtures for league {league_id}")
            continue

        log.info(f"[BOARD] {league_name}: {len(upcoming)} upcoming fixtures")

        for fixture_data in upcoming[:_MAX_FIX_PER_LEAGUE]:
            fix_info = fixture_data.get("fixture", {})
            teams    = fixture_data.get("teams", {})
            fix_id   = fix_info.get("id")
            home     = teams.get("home", {})
            away     = teams.get("away", {})
            home_id  = home.get("id")
            away_id  = away.get("id")
            if not (fix_id and home_id and away_id):
                continue

            # ── 2. Recent completed fixtures for each team ───────────────────
            hist_home, hist_away = await asyncio.gather(
                api_football_request("fixtures", {
                    "team": home_id, "last": _MAX_HIST,
                    "season": season, "league": league_id,
                }),
                api_football_request("fixtures", {
                    "team": away_id, "last": _MAX_HIST,
                    "season": season, "league": league_id,
                }),
            )

            seen_ids: set[int] = set()
            hist_ids: list[int] = []
            for hf in (hist_home or []) + (hist_away or []):
                hf_id  = hf.get("fixture", {}).get("id")
                status = hf.get("fixture", {}).get("status", {}).get("short", "")
                if hf_id and hf_id != fix_id and status == "FT" and hf_id not in seen_ids:
                    seen_ids.add(hf_id)
                    hist_ids.append(hf_id)
                if len(hist_ids) >= 10:
                    break

            # ── 3. Player stats for each historical fixture ──────────────────
            # Fetch at most 2 in parallel to respect the API semaphore
            player_logs:  dict[int, list[dict]]  = {}
            player_meta:  dict[int, dict]         = {}

            for batch_start in range(0, len(hist_ids), 2):
                batch = hist_ids[batch_start:batch_start + 2]
                results = await asyncio.gather(
                    *[api_football_request("fixtures/players", {"fixture": hid}) for hid in batch]
                )
                for players_resp in results:
                    if not players_resp:
                        continue
                    for team_block in players_resp:
                        team_info   = team_block.get("team", {})
                        players_lst = team_block.get("players", [])
                        for p_block in players_lst:
                            p         = p_block.get("player", {})
                            stats_lst = p_block.get("statistics", [])
                            if not stats_lst:
                                continue
                            st  = stats_lst[0]
                            mins = (st.get("games") or {}).get("minutes") or 0
                            if mins < 30:
                                continue
                            pid = p.get("id")
                            if not pid:
                                continue
                            player_meta[pid] = {
                                "playerId":   pid,
                                "playerName": p.get("name", ""),
                                "teamId":     team_info.get("id"),
                                "teamName":   team_info.get("name", ""),
                                "position":   (st.get("games") or {}).get("position") or "M",
                            }
                            shots_blk  = st.get("shots")  or {}
                            passes_blk = st.get("passes") or {}
                            goals_blk  = st.get("goals")  or {}
                            player_logs.setdefault(pid, []).append({
                                "shots_total":  shots_blk.get("total")  or 0,
                                "passes_total": passes_blk.get("total") or 0,
                                "goals_saves":  goals_blk.get("saves")  or 0,
                                "minutes":      mins,
                            })

            # ── 4. Per-player rolling projections ────────────────────────────
            fixture_players: list[dict] = []
            for pid, logs in player_logs.items():
                if len(logs) < _MIN_GAMES:
                    continue
                meta     = player_meta.get(pid, {})
                position = meta.get("position", "M")
                is_home  = meta.get("teamId") == home_id
                recent   = logs[:5]  # newest first (API returns reverse-chron)

                props_out: dict[str, dict] = {}
                for cfg in BOARD_PROPS:
                    if cfg["position"] == "G" and position != "G":
                        continue
                    if cfg["key"] == "saves" and position != "G":
                        continue

                    vals    = [g.get(cfg["field"]) or 0 for g in recent]
                    weights = [_DECAY ** i for i in range(len(vals))]
                    proj    = sum(w * v for w, v in zip(weights, vals)) / sum(weights)
                    l5_avg  = sum(vals) / len(vals)

                    props_out[cfg["key"]] = {
                        "projected":   round(proj,   1),
                        "l5Avg":       round(l5_avg, 1),
                        "samples":     len(vals),
                        "l5HitPct":    None,
                        "l10HitPct":   None,
                        "settledPicks": 0,
                    }

                if not props_out:
                    continue

                fixture_players.append({
                    **meta,
                    "isHome": is_home,
                    "props":  props_out,
                })

            # Sort: home first, then by highest projected shots
            fixture_players.sort(key=lambda p: (
                0 if p["isHome"] else 1,
                -(p["props"].get("shots", {}).get("projected", 0) or 0),
            ))

            all_fixtures.append({
                "fixtureId":  fix_id,
                "homeTeam":   home.get("name", ""),
                "awayTeam":   away.get("name", ""),
                "homeTeamId": home_id,
                "awayTeamId": away_id,
                "startTime":  fix_info.get("date"),
                "leagueId":   league_id,
                "leagueName": league_name,
                "season":     season,
                "players":    fixture_players,
            })

    # ── 5. Enrich with hit rates from settled picks ──────────────────────────
    await _enrich_hit_rates(db, all_fixtures)

    doc = {
        "_id":       "main",
        "fixtures":  all_fixtures,
        "updatedAt": now.isoformat(),
        "leagueIds": [cfg["leagueId"] for cfg in BOARD_LEAGUES],
    }
    await db.soccer_board_cache.replace_one({"_id": "main"}, doc, upsert=True)

    total_players = sum(len(f["players"]) for f in all_fixtures)
    log.info(f"[BOARD] Cached: {len(all_fixtures)} fixtures, {total_players} players")
    return {k: v for k, v in doc.items() if k != "_id"}


async def _enrich_hit_rates(db, fixtures: list):
    """Fill in l5HitPct / l10HitPct for each player-prop from db.picks."""
    # Gather all (playerName, propType) pairs
    names    = set()
    prop_keys = set()
    for fix in fixtures:
        for player in fix.get("players", []):
            names.add(player.get("playerName", ""))
            for pk in player.get("props", {}):
                prop_keys.add(pk)

    if not names or not prop_keys:
        return

    # Single batch query
    cursor = db.picks.find(
        {
            "playerName": {"$in": list(names)},
            "propType":   {"$in": list(prop_keys)},
            "sport":      "soccer",
            "result":     {"$in": ["hit", "miss"]},
        },
        {"playerName": 1, "propType": 1, "result": 1, "savedAt": 1, "_id": 0},
        sort=[("savedAt", -1)],
    ).limit(10000)

    grouped: dict[tuple, list[str]] = {}
    async for pick in cursor:
        key = (pick.get("playerName", ""), pick.get("propType", ""))
        grouped.setdefault(key, []).append(pick.get("result", ""))

    for fix in fixtures:
        for player in fix.get("players", []):
            name = player.get("playerName", "")
            for pk, prop_data in player.get("props", {}).items():
                results = grouped.get((name, pk), [])
                if results:
                    l5  = results[:5]
                    l10 = results[:10]
                    prop_data["l5HitPct"]    = round(100 * l5.count("hit")  / len(l5))  if l5  else None
                    prop_data["l10HitPct"]   = round(100 * l10.count("hit") / len(l10)) if l10 else None
                    prop_data["settledPicks"] = len(results)


# ── Background loop ───────────────────────────────────────────────────────────
async def soccer_board_loop(db):
    """Rebuild the board every 6 hours. Called from server.py startup."""
    import asyncio as _a
    # Small initial delay so the server finishes startup first
    await _a.sleep(30)
    while True:
        try:
            await build_soccer_board(db)
        except Exception as exc:
            log.error(f"[BOARD] Loop error: {exc}")
        await _a.sleep(6 * 60 * 60)
