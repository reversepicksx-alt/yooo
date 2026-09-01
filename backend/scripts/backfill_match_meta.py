"""
Backfill script: enrich settled picks with home/away team names, final goals,
and ball possession.

Strategy: group picks by (teamId, opponentName, settledAt date). Each unique
fixture is hit at most once for /fixtures and once for /fixtures/statistics,
not once per pick. Many picks share fixtures (multiple players from the same
match), so this minimizes API quota use.

Run with:
    cd backend && python -m scripts.backfill_match_meta
    cd backend && python -m scripts.backfill_match_meta --dry-run
    cd backend && python -m scripts.backfill_match_meta --limit 50
"""
import asyncio
import argparse
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, ".")

from config import db
from utils import api_football_request
from routes.picks import _fetch_fixture_possession


API_SLEEP_SECONDS = 0.15


async def _api(endpoint: str, params: dict):
    """Wrapper that throttles after every external API call to avoid quota spikes."""
    try:
        result = await api_football_request(endpoint, params)
    finally:
        await asyncio.sleep(API_SLEEP_SECONDS)
    return result


async def main(dry_run: bool = False, limit: int | None = None):
    query = {
        "status": "settled",
        "$or": [
            {"homeTeam": {"$exists": False}}, {"homeTeam": ""},
            {"awayTeam": {"$exists": False}}, {"awayTeam": ""},
            {"homePoss": {"$exists": False}},
            {"awayPoss": {"$exists": False}},
            {"finalHomeGoals": {"$exists": False}},
            {"finalAwayGoals": {"$exists": False}},
        ],
    }
    projection = {
        "pickId": 1, "email": 1,
        "teamId": 1, "teamName": 1,
        "opponentId": 1, "opponentName": 1,
        "venue": 1, "settledAt": 1, "timestamp": 1,
        "matchScore": 1,
        "leagueId": 1,
        "_id": 0,
    }
    cursor = db.picks.find(query, projection)
    if limit:
        cursor = cursor.limit(limit)
    picks = await cursor.to_list(None)
    print(f"[BACKFILL META] {len(picks)} picks need enrichment")
    if not picks:
        return

    # Group by (team_id, opponent_lower, date_str). Picks for the same team-opp
    # on the same day are the same fixture.
    groups = defaultdict(list)
    for p in picks:
        team_id = p.get("teamId") or 0
        opp = (p.get("opponentName") or "").lower().strip()
        ts = p.get("settledAt") or p.get("timestamp")
        try:
            if isinstance(ts, str):
                d = datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat()
            elif isinstance(ts, (int, float)):
                d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat()
            else:
                d = "unknown"
        except Exception:
            d = "unknown"
        groups[(team_id, opp, d)].append(p)

    print(f"[BACKFILL META] grouped into {len(groups)} unique fixtures")

    updated = 0
    skipped = 0
    api_calls = 0

    for (team_id, opp_lower, date_str), group in groups.items():
        if not team_id or not opp_lower or date_str == "unknown":
            skipped += len(group)
            continue

        # Look up fixture: try date-specific window (settled date ± 1 day)
        try:
            base_date = datetime.fromisoformat(date_str)
        except Exception:
            skipped += len(group)
            continue

        # API-Football requires {date, league, season} together (team-only queries
        # demand season too). League is on the pick. Season = (year - 1) if month <= 6,
        # else year (handles the Aug→May club football cycle for European leagues; for
        # MLS/single-year leagues we also try year as a fallback).
        league_id = group[0].get("leagueId") or 0
        if not league_id:
            skipped += len(group)
            continue
        seasons_to_try = []
        if base_date.month <= 6:
            seasons_to_try.append(base_date.year - 1)
            seasons_to_try.append(base_date.year)
        else:
            seasons_to_try.append(base_date.year)
            seasons_to_try.append(base_date.year - 1)

        candidates = []
        # Strategy A: date + league + season (cheap, covers most picks)
        for delta in (0, -1, 1):
            d = (base_date + timedelta(days=delta)).date().isoformat()
            for season in seasons_to_try:
                try:
                    fxs = await _api(
                        "fixtures",
                        {"date": d, "league": league_id, "season": season},
                    )
                    api_calls += 1
                    if fxs:
                        candidates.extend(fxs)
                        break
                except Exception:
                    continue

        # Match by team_id (exact) on home or away
        fixture = None
        for f in candidates:
            hid = f.get("teams", {}).get("home", {}).get("id")
            aid = f.get("teams", {}).get("away", {}).get("id")
            if hid != team_id and aid != team_id:
                continue
            status = f.get("fixture", {}).get("status", {}).get("short", "")
            if status in ("FT", "AET", "PEN"):
                fixture = f
                break
            if fixture is None:
                fixture = f

        # Strategy B: fall back to head-to-head if we have opponentId. Cup matches
        # often live in a different league_id than the pick's league_id.
        if not fixture:
            opp_id = group[0].get("opponentId") or 0
            if opp_id:
                for season in seasons_to_try:
                    try:
                        fxs = await _api(
                            "fixtures",
                            {"h2h": f"{team_id}-{opp_id}", "season": season},
                        )
                        api_calls += 1
                        if not fxs:
                            continue
                        # Pick the one closest to settledAt within ±2 days
                        for f in fxs:
                            try:
                                fdt = datetime.fromisoformat(
                                    f.get("fixture", {}).get("date", "").replace("Z", "+00:00")
                                ).date()
                                if abs((fdt - base_date.date()).days) <= 2:
                                    status = f.get("fixture", {}).get("status", {}).get("short", "")
                                    if status in ("FT", "AET", "PEN"):
                                        fixture = f
                                        break
                            except Exception:
                                continue
                        if fixture:
                            break
                    except Exception:
                        continue

        # Strategy C: team's recent fixtures (covers picks with no opponentId
        # and cup matches outside the pick's league)
        if not fixture:
            for season in seasons_to_try:
                try:
                    fxs = await _api(
                        "fixtures",
                        {"team": team_id, "last": 15, "season": season},
                    )
                    api_calls += 1
                    if not fxs:
                        continue
                    for f in fxs:
                        try:
                            fdt = datetime.fromisoformat(
                                f.get("fixture", {}).get("date", "").replace("Z", "+00:00")
                            ).date()
                            if abs((fdt - base_date.date()).days) > 2:
                                continue
                            home = (f.get("teams", {}).get("home", {}).get("name", "") or "").lower()
                            away = (f.get("teams", {}).get("away", {}).get("name", "") or "").lower()
                            if (opp_lower in home or home in opp_lower
                                or opp_lower in away or away in opp_lower):
                                status = f.get("fixture", {}).get("status", {}).get("short", "")
                                if status in ("FT", "AET", "PEN"):
                                    fixture = f
                                    break
                        except Exception:
                            continue
                    if fixture:
                        break
                except Exception:
                    continue

        if not fixture:
            skipped += len(group)
            continue

        fid = fixture.get("fixture", {}).get("id")
        home_id = fixture.get("teams", {}).get("home", {}).get("id")
        away_id = fixture.get("teams", {}).get("away", {}).get("id")
        home_name = fixture.get("teams", {}).get("home", {}).get("name", "") or ""
        away_name = fixture.get("teams", {}).get("away", {}).get("name", "") or ""
        home_goals = fixture.get("goals", {}).get("home", 0) or 0
        away_goals = fixture.get("goals", {}).get("away", 0) or 0

        # Possession (one extra call per fixture)
        try:
            home_poss, away_poss = await _fetch_fixture_possession(fid, home_id, away_id)
            api_calls += 1
        except Exception:
            home_poss, away_poss = (None, None)

        update_set = {
            "homeTeam": home_name,
            "awayTeam": away_name,
            "finalHomeGoals": home_goals,
            "finalAwayGoals": away_goals,
        }
        if home_poss is not None:
            update_set["homePoss"] = home_poss
        if away_poss is not None:
            update_set["awayPoss"] = away_poss

        sample = group[0]
        print(f"  → {team_id} vs {opp_lower} on {date_str}: "
              f"{home_name} {home_goals}-{away_goals} {away_name} "
              f"poss {home_poss}-{away_poss}  ({len(group)} picks, sample: {sample.get('pickId')})")

        if not dry_run:
            for p in group:
                await db.picks.update_one(
                    {"pickId": p["pickId"], "email": p["email"]},
                    {"$set": update_set}
                )
                updated += 1
        else:
            updated += len(group)

    print(f"\n[BACKFILL META] done. picks updated={updated}, skipped={skipped}, "
          f"api_calls={api_calls}{' (dry-run)' if dry_run else ''}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run, limit=args.limit))
