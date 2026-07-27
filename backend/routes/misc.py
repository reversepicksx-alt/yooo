import asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Query
from emergentintegrations.llm.chat import LlmChat, UserMessage

from config import db, EMERGENT_LLM_KEY, CURRENT_SEASON, GROK_MODEL, INTERNATIONAL_LEAGUES
from utils import api_football_request
from cache import COL_PLAYERS, COL_NATIONAL

router = APIRouter(prefix="/api", tags=["misc"])

# Collection for caching player context results
COL_PLAYER_CTX_CACHE = "player_ctx_cache"
_CONTEXT_CACHE_TTL_H = 12  # hours

# Collection for caching team next-match results
COL_NEXT_MATCH_CACHE = "next_match_cache"
_NEXT_MATCH_TTL_H = 1  # 1 hour — short enough to pick up schedule changes


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
    _stale_cached_result = None  # saved for quota-exhausted fallback below
    try:
        cached = await db[COL_NEXT_MATCH_CACHE].find_one({"teamId": team_id})
        if cached:
            age_h = (now - cached["cachedAt"].replace(tzinfo=timezone.utc)
                     if cached["cachedAt"].tzinfo is None
                     else now - cached["cachedAt"]).total_seconds() / 3600
            _stale_cached_result = cached["result"]  # save regardless of age
            if age_h < _NEXT_MATCH_TTL_H:
                return cached["result"]
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
            api_football_request("fixtures", {"team": team_id, "date": today_str, "season": 2025}),
            api_football_request("fixtures", {"team": team_id, "date": today_str, "season": 2026}),
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
    fx = None
    for candidate in (today_fixtures or []):
        lid = candidate.get("league", {}).get("id", 0)
        if lid not in _SKIP_LEAGUES:
            fx = candidate
            break

    # ── 1. Upcoming fixtures ──────────────────────────────────────────────────
    if not fx:
        # Fetch general next-20 AND WC 2026 specifically in parallel.
        try:
            fixtures, wc_fixtures = await asyncio.gather(
                api_football_request("fixtures", {"team": team_id, "next": 20}),
                api_football_request("fixtures", {"team": team_id, "league": 1, "season": 2026, "next": 5}),
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

        for candidate in _all_upcoming:
            lid = candidate.get("league", {}).get("id", 0)
            if lid not in _SKIP_LEAGUES:
                fx = candidate
                break

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
                odds_data = await api_football_request("odds", {"fixture": fixture_id})
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
        }

    if result is None:
        # ── 2. No upcoming fixture — use last completed matches for league info ─
        try:
            last_fixtures = await api_football_request("fixtures", {"team": team_id, "last": 10})
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
        # ── Quota-exhausted / API-down fallback: serve stale cache ────────────
        # When every API call returns nothing (quota blown, transient error),
        # returning {"found":false} breaks the UI for everyone.  A stale cached
        # result from earlier today is almost always still correct — fixtures
        # don't change minute-to-minute.  Mark it stale so callers know.
        if _stale_cached_result and _stale_cached_result.get("found"):
            print(f"[NEXT-MATCH STALE FALLBACK] team={team_id} serving cached result (API unavailable)")
            stale = dict(_stale_cached_result)
            stale["stale"] = True
            return stale
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

    # Fetch today's fixtures to find live games
    try:
        fixtures = await api_football_request("fixtures", {"date": today, "status": "NS"})
        if not fixtures:
            # Try tomorrow
            tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
            fixtures = await api_football_request("fixtures", {"date": tomorrow, "status": "NS"})

        if not fixtures:
            # Fallback: get next fixtures from top leagues
            fixtures = []
            for lid in [39, 140, 135, 78, 61]:
                try:
                    f = await api_football_request("fixtures", {"league": lid, "next": 3, "season": CURRENT_SEASON})
                    fixtures.extend(f or [])
                except Exception:
                    continue
                if len(fixtures) >= 5:
                    break
    except Exception:
        fixtures = []

    if not fixtures:
        result = {
            "date": today,
            "available": False,
            "message": "No fixtures found for today. Check back later."
        }
        await db.potd.update_one({"date": today}, {"$set": result}, upsert=True)
        return result

    # Prepare fixture summaries for Gemini
    fixture_summaries = []
    for f in fixtures[:10]:
        home = f.get("teams", {}).get("home", {})
        away = f.get("teams", {}).get("away", {})
        league = f.get("league", {})
        fixture_summaries.append({
            "home": home.get("name", ""),
            "away": away.get("name", ""),
            "league": league.get("name", ""),
            "leagueId": league.get("id", 0),
            "date": f.get("fixture", {}).get("date", ""),
        })

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"potd-{uuid.uuid4().hex[:8]}",
        system_message="You are an elite soccer prop analyst. Return ONLY valid JSON."
    )
    chat.with_model("gemini", "gemini-2.5-flash")

    prompt = f"""Today's fixtures:
{json.dumps(fixture_summaries, default=str)}

Pick the SINGLE best player prop bet of the day. Choose a real star player from one of these matchups who has a strong statistical edge. Return ONLY this JSON:
{{"playerName":"","teamName":"","opponentName":"","league":"","leagueId":0,"propType":"pass_attempts|shots|shots_on_target|tackles|key_passes|saves|interceptions|blocks|dribbles|fouls_drawn","suggestedLine":0,"recommendation":"over|under","confidenceScore":0-100,"confidenceLevel":"Low|Medium|High|Very High","sharpSummary":"2-3 sentence sharp analysis of WHY this is the pick","reasoning":"1 paragraph explaining the matchup edge, recent form, and statistical backing"}}

Pick a REAL player from these actual fixtures. Be specific and data-driven."""

    try:
        response = await chat.send_message(UserMessage(text=prompt))
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        pick_data = json.loads(text)
    except Exception:
        pick_data = {
            "playerName": "Unable to generate",
            "teamName": "",
            "opponentName": "",
            "league": "",
            "propType": "shots",
            "suggestedLine": 0,
            "recommendation": "over",
            "confidenceScore": 0,
            "confidenceLevel": "Low",
            "sharpSummary": "Pick generation failed. Try refreshing.",
            "reasoning": ""
        }

    result = {
        "date": today,
        "available": True,
        "pick": pick_data,
        "generatedAt": datetime.now(timezone.utc).isoformat()
    }

    await db.potd.update_one({"date": today}, {"$set": result}, upsert=True)
    return result


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
