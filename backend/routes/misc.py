import json
from datetime import datetime, timezone
from fastapi import APIRouter, Query
from config import db, CURRENT_SEASON
from utils import (
    api_football_request,
    priority_api_football_request,
    select_next_fixture,
    _LIVE_FIXTURE_STATUSES,
    _FINISHED_FIXTURE_STATUSES,
)
from cache import COL_PLAYERS, COL_NATIONAL

router = APIRouter(prefix="/api", tags=["misc"])

# Collection for caching player context results
COL_PLAYER_CTX_CACHE = "player_ctx_cache"
_CONTEXT_CACHE_TTL_H = 12  # hours

# Collection for caching team next-match results
COL_NEXT_MATCH_CACHE = "next_match_cache"
_NEXT_MATCH_TTL_H = 0.25  # 15 min; never let a schedule change linger for hours


def _cached_match_is_active(result: dict, now: datetime) -> bool:
    """Only reuse cached match identity while its fixture is live/upcoming."""
    if not result or not result.get("found"):
        return True
    status = str(result.get("statusShort", "") or "").upper()
    if status in _FINISHED_FIXTURE_STATUSES:
        return False
    if status in _LIVE_FIXTURE_STATUSES:
        return True
    try:
        kickoff = datetime.fromisoformat(str(result.get("date", "")).replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        else:
            kickoff = kickoff.astimezone(timezone.utc)
        return kickoff >= now
    except (TypeError, ValueError):
        return False


@router.get("/players/{player_id}/contexts")
async def player_contexts(player_id: int):
    """Return all team contexts (club + national) for a given player ID.

    Results are cached for 12 h to survive transient API-Football failures.
    The national-team entry is the most important: if an earlier call found it,
    subsequent calls return it instantly even if the live API is slow/down.
    """
    now = datetime.now(timezone.utc)

    # ── Cache read ────────────────────────────────────────────────────────────
    # Respect per-record TTL: records without a national team are stored with
    # ttlHours=1 so they retry quickly after an API quota / transient failure.
    cached = await db[COL_PLAYER_CTX_CACHE].find_one(
        {"playerId": player_id},
        {"_id": 0, "contexts": 1, "cachedAt": 1, "ttlHours": 1}
    )
    if cached:
        ttl_h = cached.get("ttlHours", _CONTEXT_CACHE_TTL_H)
        cached_at = cached.get("cachedAt")
        if cached_at:
            # Normalise: make cached_at timezone-aware if MongoDB returned naive
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            if (now - cached_at).total_seconds() < ttl_h * 3600:
                return {"contexts": cached["contexts"]}

    # ── Live build ────────────────────────────────────────────────────────────
    # Load national team IDs from cache
    national_ids: set = set()
    async for n in db[COL_NATIONAL].find({}, {"teamId": 1, "_id": 0}):
        if n.get("teamId"):
            national_ids.add(n["teamId"])

    seen: set = set()
    contexts = []

    # Step 1 — club contexts from cache_players (fast, no API)
    docs = await db[COL_PLAYERS].find(
        {"playerId": player_id},
        {"_id": 0, "playerId": 1, "teamId": 1, "teamName": 1, "leagueId": 1}
    ).to_list(10)
    for d in docs:
        tid = d.get("teamId", 0)
        if not tid or tid in seen:
            continue
        seen.add(tid)
        contexts.append({
            "teamId": tid,
            "teamName": d.get("teamName", ""),
            "leagueId": d.get("leagueId", 0),
            "isNational": tid in national_ids,
        })

    # Step 2 — national team discovery via API-Football player profile.
    # Try 2026 first (WC year), then 2025 and 2024 as fallbacks — some players
    # accumulate their most recent national caps in prior seasons.
    for season in [2026, 2025, 2024]:
        try:
            player_data = await api_football_request("players", {
                "id": player_id,
                "season": season,
            })
        except Exception:
            player_data = None
        if not player_data:
            continue
        found_national = False
        for entry in player_data:
            for stat in entry.get("statistics", []):
                t = stat.get("team", {})
                tid = t.get("id", 0)
                if not tid or tid in seen:
                    continue
                if tid in national_ids:
                    lg = stat.get("league", {})
                    seen.add(tid)
                    found_national = True
                    contexts.append({
                        "teamId": tid,
                        "teamName": t.get("name", ""),
                        "leagueId": lg.get("id") or 0,
                        "isNational": True,
                    })
        # Once we found a season with national-team data, don't try older seasons
        if found_national:
            break

    # ── Cache write ───────────────────────────────────────────────────────────
    # Only cache when we have at least the club context (avoids storing empty
    # results when the player ID is wrong or not yet active).
    # If no national team context was found (API-Football call may have failed or
    # quota exhausted), use a much shorter TTL (1 h) so we retry sooner instead
    # of serving a stale single-club result for a full 12 h.
    has_national = any(c.get("isNational") for c in contexts)
    effective_ttl_h = _CONTEXT_CACHE_TTL_H if has_national else 1
    if contexts:
        await db[COL_PLAYER_CTX_CACHE].update_one(
            {"playerId": player_id},
            {"$set": {
                "playerId": player_id,
                "contexts": contexts,
                "cachedAt": now,
                "ttlHours": effective_ttl_h,
            }},
            upsert=True,
        )

    return {"contexts": contexts}


@router.get("/teams/{team_id}/next-match")
async def team_next_match(team_id: int):
    """Fetch a team's next scheduled competitive fixture from API-Football.

    Results are cached for 1 h so repeated calls (e.g. context pre-fetch +
    user tap) return instantly without hitting the API quota.

    Strategy:
    1. Try the next 20 upcoming fixtures and return the first non-friendly.
       Using 20 instead of 5 ensures international tournaments (WC, Nations
       League) with sparse scheduling are captured.
    2. If nothing upcoming, fall back to the last 10 completed fixtures and
       return the most recent non-friendly league — so off-season clubs still
       auto-populate the league picker with their competition (e.g. Premier
       League for Sunderland in June).  found=False but leagueId/leagueName
       are set so the frontend can fill in the league even without a next match.
    """
    # ── Cache check ───────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    try:
        cached = await db[COL_NEXT_MATCH_CACHE].find_one({"teamId": team_id})
        if cached:
            age_h = (now - cached["cachedAt"].replace(tzinfo=timezone.utc)
                     if cached["cachedAt"].tzinfo is None
                     else now - cached["cachedAt"]).total_seconds() / 3600
            if age_h < _NEXT_MATCH_TTL_H:
                cached_result = cached["result"]
                # A cached active matchup is safe only while its fixture is
                # still future/live.  Old cache records without a status are
                # intentionally rejected once their kickoff has passed.
                if _cached_match_is_active(cached_result, now):
                    return cached_result
    except Exception:
        pass

    # Leagues to skip — pre-season club friendlies / test events
    _SKIP_LEAGUES = {667, 666}

    # ── 0. TODAY'S fixtures (critical for live-match tracking) ────────────────
    # A match that is currently live (1H, 2H, LIVE, ET) does NOT appear in the
    # "next:N" endpoint — it is no longer "upcoming".  Without this check, users
    # trying to predict on a match that is literally happening right now get
    # the NEXT future fixture (e.g. Netherlands in September) instead.
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        # API-Football "date" requires season for some leagues; try multiple
        # seasons in parallel (current club season 2025-26 + WC 2026 + prev 2025).
        today_results = await asyncio.gather(
            priority_api_football_request("fixtures", {"team": team_id, "date": today_str, "season": 2025}),
            priority_api_football_request("fixtures", {"team": team_id, "date": today_str, "season": 2026}),
            return_exceptions=True,
        )
        today_fixtures = []
        _seen_today: set = set()
        for batch in today_results:
            for f in (batch if isinstance(batch, list) else []):
                _fid = f.get("fixture", {}).get("id")
                if _fid and _fid not in _seen_today:
                    _seen_today.add(_fid)
                    today_fixtures.append(f)
    except Exception:
        today_fixtures = []

    # Accept any non-friendly fixture today (scheduled, live, or just finished)
    fx = select_next_fixture(today_fixtures, team_id, _SKIP_LEAGUES, now)

    # ── 1. Upcoming fixtures ──────────────────────────────────────────────────
    if not fx:
        # Fetch general next-20 AND WC 2026 specifically in parallel.
        try:
            fixtures, wc_fixtures = await asyncio.gather(
                priority_api_football_request("fixtures", {"team": team_id, "next": 20}),
                priority_api_football_request("fixtures", {"team": team_id, "league": 1, "season": 2026, "next": 5}),
                return_exceptions=True,
            )
            if isinstance(fixtures, Exception):
                fixtures = None
            if isinstance(wc_fixtures, Exception):
                wc_fixtures = None
        except Exception:
            fixtures = None
            wc_fixtures = None

        # Merge: general results first, then WC-specific (avoids duplicates via seen set)
        _all_upcoming = []
        _seen_fids: set = set()
        for _batch in [fixtures, wc_fixtures]:
            for _f in (_batch or []):
                _fid = _f.get("fixture", {}).get("id")
                if _fid and _fid not in _seen_fids:
                    _seen_fids.add(_fid)
                    _all_upcoming.append(_f)

        fx = select_next_fixture(_all_upcoming, team_id, _SKIP_LEAGUES, now)

    result = None
    if fx:
        home_team = fx.get("teams", {}).get("home", {})
        away_team = fx.get("teams", {}).get("away", {})
        league    = fx.get("league", {})
        raw_is_home = home_team.get("id") == team_id
        # `opponent` is always determined by the actual fixture pairing —
        # unaffected by which side is the betting favorite.
        opponent  = away_team if raw_is_home else home_team
        league_id = league.get("id", 0)
        fixture_id = fx.get("fixture", {}).get("id", 0)

        # "Home"/"Away" here doesn't just label the fixture — it also drives
        # which team's game logs get pulled for the actual prediction later
        # (scan.tsx sends this straight through as `venue`). For international
        # tournament fixtures API-Football's home/away designation is often
        # arbitrary (there's no true home ground), so it can disagree with who
        # the betting market actually treats as the favorite/home side (e.g. a
        # World Cup favorite drawn as the "away" team). Resolve using the same
        # betting-favorite-priority cascade used for legacy venue="neutral"
        # requests in routes/predict.py, so the auto-filled venue is consistent
        # with the rest of the app instead of trusting the raw fixture flag blindly.
        effective_is_home = raw_is_home
        if league_id in INTERNATIONAL_LEAGUES and fixture_id:
            try:
                odds_data = await priority_api_football_request("odds", {"fixture": fixture_id})
                favorite_side = None
                if odds_data:
                    for bk in odds_data[0].get("bookmakers", [])[:1]:
                        for bet in bk.get("bets", []):
                            if bet.get("name") == "Match Winner":
                                vals = {v["value"]: v["odd"] for v in bet.get("values", [])}
                                try:
                                    home_dec = float(vals.get("Home") or 0)
                                    away_dec = float(vals.get("Away") or 0)
                                except (TypeError, ValueError):
                                    home_dec = away_dec = 0
                                if home_dec and away_dec:
                                    favorite_side = "home" if home_dec < away_dec else "away"
                                break
                if favorite_side is not None:
                    effective_is_home = (favorite_side == "home") == raw_is_home
                    print(f"[NEXT-MATCH EFFECTIVE VENUE] team={team_id} league={league_id} "
                          f"rawIsHome={raw_is_home} favorite={favorite_side} → effectiveIsHome={effective_is_home}")
            except Exception:
                pass  # fall back to raw fixture designation

        result = {
            "found":      True,
            "isHome":     effective_is_home,
            "opponent":   {"id": opponent.get("id", 0), "name": opponent.get("name", "")},
            "leagueId":   league_id,
            "leagueName": league.get("name", ""),
            "date":       fx.get("fixture", {}).get("date", ""),
            "fixtureId":  fixture_id,
            "statusShort": fx.get("fixture", {}).get("status", {}).get("short", ""),
        }

    if result is None:
        # ── 2. No upcoming fixture — use last completed matches for league info ─
        try:
            last_fixtures = await priority_api_football_request("fixtures", {"team": team_id, "last": 10})
        except Exception:
            last_fixtures = None

        if last_fixtures:
            # API-Football returns last:N newest-first; take the first non-friendly
            for candidate in last_fixtures:
                lid = candidate.get("league", {}).get("id", 0)
                if lid not in _SKIP_LEAGUES:
                    league = candidate.get("league", {})
                    result = {
                        "found":             False,
                        "leagueId":          league.get("id", 0),
                        "leagueName":        league.get("name", ""),
                        "leagueFromHistory": True,
                    }
                    break

    if result is None:
        result = {"found": False}

    # ── Cache the result ──────────────────────────────────────────────────────
    if result.get("leagueId"):  # only cache useful results
        try:
            await db[COL_NEXT_MATCH_CACHE].update_one(
                {"teamId": team_id},
                {"$set": {"teamId": team_id, "result": result, "cachedAt": now}},
                upsert=True,
            )
        except Exception:
            pass

    return result


@router.get("/pick-of-the-day")
async def pick_of_the_day():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check cache first
    cached = await db.potd.find_one({"date": today}, {"_id": 0})
    if cached:
        return cached

    result = {
        "date": today,
        "available": False,
        "message": "Daily pick generation is unavailable because external generation is permanently disabled.",
    }
    await db.potd.update_one({"date": today}, {"$set": result}, upsert=True)
    return result


@router.get("/players/{player_id}/advanced-stats")
async def player_advanced_stats(player_id: int, season: int = CURRENT_SEASON):
    """Return advanced per-90 stats for a player from API-Football.

    Caches for 6 h. Includes expected goals, expected assists, shots, key passes,
    passes, tackles, and minutes — the most useful signals for player prop analysis.
    """
    cache_key = f"adv_stats_{player_id}_{season}"
    try:
        cached = await db.player_advanced_stats.find_one({"_key": cache_key}, {"_id": 0})
        if cached and (datetime.now(timezone.utc).timestamp() - (cached.get("_ts") or 0)) < 6 * 3600:
            return cached.get("stats")
    except Exception:
        pass

    data = await api_football_request("players", {"id": player_id, "season": season})
    if not data:
        return {}

    stats = {
        "playerId": player_id,
        "season": season,
        "appearances": 0,
        "minutes": 0,
        "minutesPerGame": 0,
        "xG": 0,
        "xA": 0,
        "goals": 0,
        "assists": 0,
        "shots": 0,
        "shotsOnTarget": 0,
        "keyPasses": 0,
        "passes": 0,
        "passAccuracy": 0,
        "tackles": 0,
        "dribbles": 0,
        "dribbleSuccess": 0,
        "fouls": 0,
        "yellowCards": 0,
        "redCards": 0,
    }

    for entry in data:
        for stat in entry.get("statistics", []):
            games = stat.get("games", {})
            apps = games.get("appearences") or games.get("appearances") or 0
            mins = games.get("minutes") or 0
            if not apps or not mins:
                continue
            stats["appearances"] += apps
            stats["minutes"] += mins
            stats["minutesPerGame"] = round(stats["minutes"] / stats["appearances"], 1)

            def per90(raw):
                if raw is None or raw == "":
                    return 0
                try:
                    return round(float(str(raw).replace("%", "")) / (mins / 90), 2)
                except Exception:
                    return 0

            stats["goals"] += int(games.get("goals") or 0)
            stats["assists"] += int(games.get("assists") or 0)
            stats["yellowCards"] += int(games.get("yellow_cards") or 0)
            stats["redCards"] += int(games.get("red_cards") or 0)

            shots = stat.get("shots", {})
            stats["shots"] += int(shots.get("total") or 0)
            stats["shotsOnTarget"] += int(shots.get("on") or 0)

            passes = stat.get("passes", {})
            stats["passes"] += int(passes.get("total") or 0)
            stats["keyPasses"] += int(passes.get("key") or 0)
            stats["passAccuracy"] = max(stats["passAccuracy"], int(passes.get("accuracy") or 0))

            tackles = stat.get("tackles", {})
            stats["tackles"] += int(tackles.get("total") or 0)

            dribbles = stat.get("dribbles", {})
            stats["dribbles"] += int(dribbles.get("attempts") or 0)
            stats["dribbleSuccess"] = max(stats["dribbleSuccess"], int(dribbles.get("success") or 0))

            fouls = stat.get("fouls", {})
            stats["fouls"] += int(fouls.get("committed") or 0)

            # Expected goals/assists are under the "goals" object in some API-Football responses
            expected = stat.get("goals", {})
            stats["xG"] += float(expected.get("expected") or 0)
            stats["xA"] += float(expected.get("assists_expected") or expected.get("expected_assists") or 0)

    # Convert cumulative to per-90
    if stats["minutes"] > 0:
        stats["xG"] = round(stats["xG"] / (stats["minutes"] / 90), 2)
        stats["xA"] = round(stats["xA"] / (stats["minutes"] / 90), 2)
        stats["goals"] = round(stats["goals"] / (stats["minutes"] / 90), 2)
        stats["assists"] = round(stats["assists"] / (stats["minutes"] / 90), 2)
        stats["shots"] = round(stats["shots"] / (stats["minutes"] / 90), 2)
        stats["shotsOnTarget"] = round(stats["shotsOnTarget"] / (stats["minutes"] / 90), 2)
        stats["keyPasses"] = round(stats["keyPasses"] / (stats["minutes"] / 90), 2)
        stats["passes"] = round(stats["passes"] / (stats["minutes"] / 90), 2)
        stats["tackles"] = round(stats["tackles"] / (stats["minutes"] / 90), 2)
        stats["dribbles"] = round(stats["dribbles"] / (stats["minutes"] / 90), 2)
        stats["fouls"] = round(stats["fouls"] / (stats["minutes"] / 90), 2)

    try:
        await db.player_advanced_stats.update_one(
            {"_key": cache_key},
            {"$set": {"_key": cache_key, "stats": stats, "_ts": datetime.now(timezone.utc).timestamp()}},
            upsert=True
        )
    except Exception:
        pass
    return stats


@router.get("/teams/{team_id}/season-possession")
async def team_season_possession(team_id: int, leagueId: int = Query(None), season: int = Query(None)):
    """Return a team's season-average possession % for a given league/season."""
    from routes.picks import _get_team_avg_possession
    effective_season = season or CURRENT_SEASON
    avg = await _get_team_avg_possession(team_id, leagueId, effective_season)
    return {"teamId": team_id, "avgPossession": avg, "count": None}


@router.get("/live/fixture-events")
async def fixture_events(fixtureId: int = Query(..., gt=0)):
    """Return live match events (goals, cards, substitutions) from API-Football.

    The response is normalised to a simple list so the mobile app can render a
    timeline without parsing vendor-specific shapes. Quota failures are handled
    gracefully: an empty list is returned instead of an error page.
    """
    raw = await api_football_request("fixtures/events", {"fixture": fixtureId})
    if not raw:
        return {"fixtureId": fixtureId, "events": []}

    events = []
    for item in raw:
        t = item.get("time", {})
        elapsed = t.get("elapsed")
        extra = t.get("extra")
        ev_type = (item.get("type") or "").lower()
        detail = (item.get("detail") or "").lower()
        player = item.get("player", {})
        assist = item.get("assist", {})
        team = item.get("team", {})
        extra_text = f"+{extra}" if extra else ""

        event = {
            "elapsed": elapsed,
            "extra": extra,
            "time": f"{elapsed}{extra_text}'",
            "type": "unknown",
            "team": team.get("name", ""),
            "teamId": team.get("id"),
            "playerName": player.get("name", ""),
            "playerId": player.get("id"),
            "assistName": assist.get("name", ""),
            "detail": item.get("detail", ""),
            "comments": item.get("comments"),
        }

        if ev_type == "goal":
            event["type"] = "own_goal" if "own" in detail else "penalty" if "penalty" in detail else "goal"
        elif ev_type == "card":
            event["type"] = "red" if "red" in detail else "yellow"
        elif ev_type == "subst":
            event["type"] = "sub"
            event["assistName"] = assist.get("name", "")
        elif ev_type == "var":
            event["type"] = "var"
        elif ev_type in {"injury", "injury_time"}:
            event["type"] = "injury"

        events.append(event)

    return {"fixtureId": fixtureId, "events": events}
