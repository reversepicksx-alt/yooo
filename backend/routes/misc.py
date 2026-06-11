import json
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter
from emergentintegrations.llm.chat import LlmChat, UserMessage

from config import db, EMERGENT_LLM_KEY, CURRENT_SEASON
from utils import api_football_request
from cache import COL_PLAYERS, COL_NATIONAL

router = APIRouter(prefix="/api", tags=["misc"])


@router.get("/players/{player_id}/contexts")
async def player_contexts(player_id: int):
    """Return all team contexts (club + national) for a given player ID."""
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

    # Step 2 — national team discovery via API-Football
    # National squads are never stored in cache_players, so we must query the
    # player profile.  Try 2026 first (covers current WC), then 2025.
    for season in [2026, 2025]:
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
                        "leagueId": lg.get("id", 0),
                        "isNational": True,
                    })
        # Once we found a season with national-team data, don't try older seasons
        if found_national:
            break

    return {"contexts": contexts}


@router.get("/teams/{team_id}/next-match")
async def team_next_match(team_id: int):
    """Fetch a team's next scheduled competitive fixture from API-Football.

    Skips club friendlies (leagueId 667) so that off-season clubs don't
    auto-populate the league picker with an irrelevant pre-season match.
    Fetches up to 5 upcoming fixtures and returns the first competitive one.
    """
    # Leagues to skip — pre-season club friendlies, not useful for props
    _SKIP_LEAGUES = {667, 666}

    try:
        fixtures = await api_football_request("fixtures", {"team": team_id, "next": 5})
    except Exception:
        fixtures = None

    if not fixtures:
        return {"found": False}

    # Pick the first fixture that isn't a friendly / non-competitive match
    fx = None
    for candidate in fixtures:
        lid = candidate.get("league", {}).get("id", 0)
        if lid not in _SKIP_LEAGUES:
            fx = candidate
            break

    if fx is None:
        return {"found": False}

    home_team = fx.get("teams", {}).get("home", {})
    away_team = fx.get("teams", {}).get("away", {})
    league = fx.get("league", {})

    is_home = home_team.get("id") == team_id
    opponent = away_team if is_home else home_team

    return {
        "found": True,
        "isHome": is_home,
        "opponent": {"id": opponent.get("id", 0), "name": opponent.get("name", "")},
        "leagueId": league.get("id", 0),
        "leagueName": league.get("name", ""),
        "date": fx.get("fixture", {}).get("date", ""),
        "fixtureId": fx.get("fixture", {}).get("id", 0),
    }


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
