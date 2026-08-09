import asyncio
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
from config import INTERNATIONAL_LEAGUES
from cache import COL_PLAYERS, COL_NATIONAL

router = APIRouter(prefix="/api", tags=["misc"])

# Collection for caching player context results
COL_PLAYER_CTX_CACHE = "player_ctx_cache"
_CONTEXT_CACHE_TTL_H = 12  # hours

# Collection for caching team next-match results
COL_NEXT_MATCH_CACHE = "next_match_cache"
_NEXT_MATCH_TTL_H = 0.25  # 15 min; never let a schedule change linger for hours
_CLUB_VERIFY_CACHE_TTL_H = 0.25  # 15 minutes; transfer detection must stay fresh
_CLUB_LEAGUE_EXCLUDES = {
    1, 9, 10, 11, 15, 16, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 667
}


def _dedupe_contexts(contexts: list) -> list:
    """Keep one context per canonical team ID, preferring current club evidence.

    A provider/cache row cannot be both a club and a national team when the
    team ID is identical.  Older cached records did contain that contradiction
    (the same club ID was marked national), which rendered duplicate context
    buttons.  Team ID is the identity key; ``isNational`` is only metadata.
    """
    selected = {}
    for context in contexts or []:
        team_id = context.get("teamId")
        if not team_id:
            continue
        try:
            key = int(team_id)
        except (TypeError, ValueError):
            key = str(team_id)
        previous = selected.get(key)
        if previous is None:
            selected[key] = context
            continue
        previous_rank = (
            bool(previous.get("verified")),
            not bool(previous.get("isNational")),
            not bool(previous.get("lastKnown")),
            bool(previous.get("teamName")),
        )
        current_rank = (
            bool(context.get("verified")),
            not bool(context.get("isNational")),
            not bool(context.get("lastKnown")),
            bool(context.get("teamName")),
        )
        if current_rank > previous_rank:
            selected[key] = context
    return list(selected.values())


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


async def _verify_player_club_bg(player_id: int, cached_contexts: list):
    """Background club-verification for a player whose contexts were served
    from cache.  If the API shows a different current club, update db.players,
    invalidate the context cache and the next-match cache so the very next
    interaction (seconds later) returns the correct team.
    """
    _INTL = {1, 9, 10, 11, 15, 16, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35}
    try:
        for s in [CURRENT_SEASON + 1, CURRENT_SEASON]:
            data = await api_football_request("players", {"id": player_id, "season": s})
            if not data:
                continue
            stats = data[0].get("statistics") or []
            # Find the most recent club stat (non-international, non-qualifier)
            api_tid, api_tname, api_lid = 0, "", 0
            for st in reversed(stats):
                lid = (st.get("league") or {}).get("id", 0)
                tm  = st.get("team") or {}
                tid = tm.get("id", 0)
                if lid and lid not in _INTL and lid != 667 and tid:
                    api_tid, api_tname, api_lid = tid, tm.get("name", ""), lid
                    break
            if not api_tid:
                break  # season had data but no club stat — stop

            # Compare against all cached club contexts
            cached_club_ids = {
                c["teamId"] for c in cached_contexts if not c.get("isNational")
            }
            if api_tid not in cached_club_ids:
                now = datetime.now(timezone.utc)
                print(
                    f"[CLUB CHANGE BG] pid={player_id}: "
                    f"cached={cached_club_ids} → new={api_tid} ({api_tname})"
                )
                # Update every old entry for this player to the new club
                await db[COL_PLAYERS].update_many(
                    {"playerId": player_id},
                    {"$set": {
                        "teamId":    api_tid,
                        "teamName":  api_tname,
                        "leagueId":  api_lid,
                        "_cachedAt": now.timestamp(),
                    }},
                )
                # Upsert fresh entry under the new club
                await db[COL_PLAYERS].update_one(
                    {"playerId": player_id, "teamId": api_tid},
                    {"$setOnInsert": {
                        "playerId":  player_id,
                        "teamId":    api_tid,
                        "teamName":  api_tname,
                        "leagueId":  api_lid,
                        "_cachedAt": now.timestamp(),
                    }},
                    upsert=True,
                )
                # Invalidate context cache — next call rebuilds with correct team
                await db[COL_PLAYER_CTX_CACHE].delete_one({"playerId": player_id})
                # Invalidate next-match cache for old teams so the new team's
                # fixture is fetched on the next interaction
                for old_tid in cached_club_ids:
                    await db["next_match_cache"].delete_one({"teamId": old_tid})
            return  # done after first season with club data
    except Exception as e:
        print(f"[CLUB VERIFY BG] pid={player_id} err={e}")


async def _resolve_verified_club(player_id: int):
    """Resolve the player's current club without falling back to cache.

    A cached club is useful for search ranking but is not evidence of a current
    transfer.  This helper intentionally returns ``None`` when the provider is
    unavailable or has no current club row.  Callers must not substitute an old
    cache row in that case.
    """
    evidence = await _resolve_club_evidence(player_id)
    if evidence.get("status") == "verified":
        return evidence.get("club")
    return None


async def _is_player_on_current_squad(player_id: int, team_id: int) -> bool:
    """Confirm a last-known club using the provider's current squad feed."""
    try:
        data = await priority_api_football_request(
            "players/squads",
            {"team": team_id},
            force_refresh=True,
        )
        for squad in data or []:
            for player in squad.get("players") or []:
                if int(player.get("id") or 0) == int(player_id):
                    return True
    except Exception as exc:
        print(f"[CLUB SQUAD VERIFY] pid={player_id} team={team_id} err={exc}")
    return False


async def _resolve_club_evidence(player_id: int):
    """Return current-club evidence without confusing it with no data.

    API-Football uses competition-season labels: European 2025-26 leagues are
    represented by season 2025, while calendar-year competitions use 2026.
    A player can therefore have national-team data in 2026 and their latest
    club row in 2025. That is ``last_known``, not ``no club``.
    """
    verification_season = max(CURRENT_SEASON + 1, datetime.now(timezone.utc).year)
    seasons = list(dict.fromkeys([verification_season, CURRENT_SEASON]))
    last_known = None
    for season in seasons:
        try:
            data = await priority_api_football_request(
                "players", {"id": player_id, "season": season},
                force_refresh=True,
            )
        except Exception as exc:
            print(f"[CLUB VERIFY] pid={player_id} season={season} err={exc}")
            continue
        if not data:
            # api_football_request uses [] for quota exhaustion, timeout, and
            # provider errors.  None of those states authorizes a stale cache
            # row to be shown as current.
            continue

        # Prefer the most recent real club stat in the current season response.
        # Do not let an international fixture or Club Friendlies row win.
        stats = data[0].get("statistics") or {}
        if isinstance(stats, dict):
            stats = [stats]
        for stat in reversed(stats):
            league = stat.get("league") or {}
            team = stat.get("team") or {}
            team_id = team.get("id")
            team_name = (team.get("name") or "").strip()
            league_id = league.get("id")
            if (
                team_id
                and team_name
                and league_id
                and league_id not in _CLUB_LEAGUE_EXCLUDES
            ):
                club = {
                    "teamId": int(team_id),
                    "teamName": team_name,
                    "leagueId": int(league_id),
                    "verifiedSeason": season,
                }
                if season == verification_season:
                    return {"status": "verified", "club": club}
                if last_known is None:
                    last_known = club
                break
    if last_known and await _is_player_on_current_squad(player_id, last_known["teamId"]):
        # The current squad feed is stronger evidence than the season label.
        # This matters during offseason: a player may have current squad status
        # while their latest competition statistics are stored under 2025.
        last_known["verificationSource"] = "current_squad"
        return {"status": "verified", "club": last_known}
    if last_known:
        return {"status": "last_known", "club": last_known}
    return {"status": "unavailable", "club": None}


@router.get("/players/{player_id}/contexts")
async def player_contexts(player_id: int):
    """Return all team contexts (club + national) for a given player ID.

    Results are cached for 12 h to survive transient API-Football failures.
    The national-team entry is the most important: if an earlier call found it,
    subsequent calls return it instantly even if the live API is slow/down.
    """
    now = datetime.now(timezone.utc)

    # ── Cache read ────────────────────────────────────────────────────────────
    # Only a recently provider-verified cache may be reused.  Older cache
    # records are search hints, not current-team evidence.
    cached = await db[COL_PLAYER_CTX_CACHE].find_one(
        {"playerId": player_id},
        {"_id": 0, "contexts": 1, "cachedAt": 1, "ttlHours": 1, "clubVerifiedAt": 1}
    )
    if cached:
        ttl_h = min(
            float(cached.get("ttlHours", _CONTEXT_CACHE_TTL_H)),
            _CLUB_VERIFY_CACHE_TTL_H,
        )
        cached_at = cached.get("cachedAt")
        club_verified_at = cached.get("clubVerifiedAt")
        if cached_at and club_verified_at:
            # Normalise: make cached_at timezone-aware if MongoDB returned naive
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            if club_verified_at.tzinfo is None:
                club_verified_at = club_verified_at.replace(tzinfo=timezone.utc)
            if (
                (now - cached_at).total_seconds() < ttl_h * 3600
                and (now - club_verified_at).total_seconds() < _CLUB_VERIFY_CACHE_TTL_H * 3600
            ):
                return {
                    "contexts": _dedupe_contexts(cached["contexts"]),
                    "teamVerified": True,
                    "verificationStatus": "verified",
                }

    # Never serve a stale club row while the provider is unavailable.  This is
    # intentionally synchronous on selection: displaying the old club creates a
    # false next-match identity and can produce a completely wrong prediction.
    club_evidence = await _resolve_club_evidence(player_id)
    verified_club = club_evidence.get("club") if club_evidence.get("status") == "verified" else None
    last_known_club = club_evidence.get("club") if club_evidence.get("status") == "last_known" else None

    # ── Live build ────────────────────────────────────────────────────────────
    # Load national team IDs from cache
    national_ids: set = set()
    async for n in db[COL_NATIONAL].find({}, {"teamId": 1, "_id": 0}):
        if n.get("teamId"):
            national_ids.add(n["teamId"])

    seen: set = set()
    contexts = []

    # Step 1 — national contexts from cache_players. These are separate from
    # the current club and may still be offered as an explicit "predict as"
    # choice, but cached club rows are never copied into the current context.
    docs = await db[COL_PLAYERS].find(
        {"playerId": player_id},
        {"_id": 0, "playerId": 1, "teamId": 1, "teamName": 1, "leagueId": 1}
    ).to_list(10)
    for d in docs:
        tid = d.get("teamId", 0)
        if not tid or tid in seen:
            continue
        if d.get("leagueId") not in _CLUB_LEAGUE_EXCLUDES:
            continue
        seen.add(tid)
        contexts.append({
            "teamId": tid,
            "teamName": d.get("teamName", ""),
            "leagueId": d.get("leagueId", 0),
            "isNational": tid in national_ids,
            "verified": False,
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
                        "verified": True,
                    })
        # Once we found a season with national-team data, don't try older seasons
        if found_national:
            break

    # The live club is the only authoritative current-team context. A prior
    # season club can be shown as last-known context, but never auto-filled.
    if verified_club:
        old_club_ids = {
            d.get("teamId")
            for d in docs
            if d.get("teamId") and d.get("leagueId") not in _CLUB_LEAGUE_EXCLUDES
        }
        club_context = {
            **verified_club,
            "isNational": False,
            "verified": True,
        }
        # A player can have several competition rows for the same club
        # (domestic league, cup, continental, friendlies). Keep one canonical
        # current-club context instead of exposing duplicate team buttons.
        contexts = [
            c for c in contexts
            if c.get("isNational") or c.get("teamId") != verified_club["teamId"]
        ]
        contexts.insert(0, club_context)
        if old_club_ids and verified_club["teamId"] not in old_club_ids:
            print(
                f"[CLUB CHANGE] pid={player_id}: "
                f"cached={sorted(old_club_ids)} → "
                f"new={verified_club['teamId']} ({verified_club['teamName']})"
            )

        # Correct only club rows.  Persistence is an optimization, not part
        # of identity verification: Atlas can be write-blocked while the
        # provider result is still valid. Never turn that storage condition
        # into a 500 on the player-selection path.
        try:
            await db[COL_PLAYERS].update_many(
                {
                    "playerId": player_id,
                    "leagueId": {"$nin": list(_CLUB_LEAGUE_EXCLUDES)},
                },
                {"$set": {
                    "teamId": verified_club["teamId"],
                    "teamName": verified_club["teamName"],
                    "leagueId": verified_club["leagueId"],
                    "_cachedAt": now.timestamp(),
                }},
            )
            await db[COL_PLAYERS].update_one(
                {
                    "playerId": player_id,
                    "teamId": verified_club["teamId"],
                    "leagueId": verified_club["leagueId"],
                },
                {"$set": {
                    "playerId": player_id,
                    "teamId": verified_club["teamId"],
                    "teamName": verified_club["teamName"],
                    "leagueId": verified_club["leagueId"],
                    "_cachedAt": now.timestamp(),
                }},
                upsert=True,
            )
        except Exception as exc:
            print(f"[CLUB VERIFY] persistence skipped; serving verified club pid={player_id}: {exc}")
    elif last_known_club:
        # Remove an old cached copy of the same club before adding the
        # explicitly-labelled last-known row.
        contexts = [
            c for c in contexts
            if c.get("teamId") != last_known_club["teamId"]
        ]
        contexts.insert(0, {
            **last_known_club,
            "isNational": False,
            "verified": False,
            "lastKnown": True,
        })

    # ── Cache write ───────────────────────────────────────────────────────────
    # An unavailable provider must not refresh the timestamp on stale club
    # data.  Cache only a verified club response; national-only results remain
    # short-lived and cannot become a current club by accident.
    has_national = any(c.get("isNational") for c in contexts)
    effective_ttl_h = _CONTEXT_CACHE_TTL_H if verified_club and has_national else 1
    if verified_club:
        try:
            await db[COL_PLAYER_CTX_CACHE].update_one(
                {"playerId": player_id},
                {"$set": {
                    "playerId": player_id,
                    "contexts": contexts,
                    "cachedAt": now,
                    "ttlHours": effective_ttl_h,
                    "clubVerifiedAt": now,
                }},
                upsert=True,
            )
        except Exception as exc:
            print(f"[PLAYER CONTEXT] cache write skipped; serving verified contexts pid={player_id}: {exc}")

    if not verified_club:
        # Keep national-only options if they were discovered, but explicitly
        # tell the client that no current club is verified.
        return {
            "contexts": _dedupe_contexts(contexts),
            "teamVerified": False,
            "verificationStatus": club_evidence.get("status", "unavailable"),
            "lastKnownClub": last_known_club,
        }
    return {
        "contexts": _dedupe_contexts(contexts),
        "teamVerified": True,
        "verificationStatus": "verified",
    }


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
            cached_result = cached["result"]
            # "found: false" results are cached only for 2 min so a transient
            # empty API response (e.g. right after a server restart) doesn't
            # block auto-fill for the full 15-minute window.
            _effective_ttl = _NEXT_MATCH_TTL_H if cached_result.get("found") else (2 / 60)
            if age_h < _effective_ttl:
                # A cached active matchup is safe only while its fixture is
                # still future/live.  Old cache records without a status are
                # intentionally rejected once their kickoff has passed.
                # Older cache entries predate canonical fixture-side fields.
                # Re-fetch those once so the UI never has to reconstruct
                # home/away labels from the player's effective venue.
                _has_fixture_sides = (
                    not cached_result.get("found")
                    or (
                        isinstance(cached_result.get("homeTeam"), dict)
                        and isinstance(cached_result.get("awayTeam"), dict)
                    )
                )
                if _has_fixture_sides and _cached_match_is_active(cached_result, now):
                    return cached_result
    except Exception:
        pass

    # Leagues to skip — pre-season club friendlies / test events only.
    # 666 = Club Friendlies (International). Do NOT add competitive tournaments
    # like Leagues Cup (667) here — MLS/Liga MX teams play it Aug–Oct and
    # blocking it leaves the next-match auto-fill blank for the whole tournament.
    _SKIP_LEAGUES = {666}

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
            "rawIsHome":  raw_is_home,
            "playerTeam": {"id": team_id, "name": (home_team if raw_is_home else away_team).get("name", "")},
            "opponent":   {"id": opponent.get("id", 0), "name": opponent.get("name", "")},
            "homeTeam":   {"id": home_team.get("id", 0), "name": home_team.get("name", "")},
            "awayTeam":   {"id": away_team.get("id", 0), "name": away_team.get("name", "")},
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
