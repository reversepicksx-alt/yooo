"""
BACKFILL ODDS TIERS FROM PROJECTED POSSESSION

For picks missing moneyline data, compute odds tier from the model's own
pre-match projected possession (projHomePoss/projAwayPoss) and save it back
to the pick as a synthetic moneyline + oddsTier field.

This enables full (odds-tier × position × prop × direction) backtesting.
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone

def _odds_tier_from_possession(proj_home, proj_away, venue):
    """
    Map projected possession to odds tier using the same thresholds
    as the moneyline-based classifier.
    """
    if venue == "home":
        poss = float(proj_home) if proj_home is not None else 50.0
    elif venue == "away":
        poss = float(proj_away) if proj_away is not None else 50.0
    else:
        # For neutral venue: try to infer from home vs away gap
        if proj_home is not None and proj_away is not None:
            gap = abs(float(proj_home) - float(proj_away))
            poss = 50.0 + gap / 2  # approximate dominant side
        else:
            poss = 50.0

    if poss >= 72:
        return "heavy_favorite"
    elif poss >= 65:
        return "strong_favorite"
    elif poss >= 60:
        return "moderate_favorite"
    elif poss >= 52:
        return "slight_favorite"
    elif poss >= 48:
        return "close"
    elif poss >= 40:
        return "slight_underdog"
    elif poss >= 33:
        return "moderate_underdog"
    else:
        return "heavy_underdog"


def _synthetic_moneyline_from_possession(poss):
    """
    Convert possession % to approximate American odds for the synthetic moneyline.
    75% possession ≈ -300 (heavy favorite), 50% ≈ +100 (even), 25% ≈ +300 (heavy underdog)
    """
    if poss >= 75:
        return -300
    elif poss >= 70:
        return -233
    elif poss >= 65:
        return -186
    elif poss >= 60:
        return -150
    elif poss >= 55:
        return -122
    elif poss >= 52:
        return -108
    elif poss >= 48:
        return -104
    elif poss >= 45:
        return +122
    elif poss >= 40:
        return +150
    elif poss >= 35:
        return +186
    elif poss >= 30:
        return +233
    else:
        return +300


async def backfill():
    url = os.environ.get("MONGO_URL")
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
    db = client.reversepicks

    cursor = db.picks.find(
        {"status": "settled", "sport": "soccer",
         "$or": [
             {"moneyline": {"$exists": False}},
             {"moneyline": None},
         ]},
        {"_id": 1, "projHomePoss": 1, "projAwayPoss": 1, "venue": 1,
         "homePoss": 1, "awayPoss": 1, "moneyline": 1}
    )

    total_updated = 0
    total_skipped = 0
    async for doc in cursor:
        _id = doc["_id"]
        venue = doc.get("venue", "home")
        proj_home = doc.get("projHomePoss")
        proj_away = doc.get("projAwayPoss")

        # If no projected possession, try to infer from actual possession
        if proj_home is None and proj_away is None:
            home_poss = doc.get("homePoss")
            away_poss = doc.get("awayPoss")
            if home_poss is not None and away_poss is not None:
                proj_home = float(home_poss)
                proj_away = float(away_poss)
            else:
                total_skipped += 1
                continue

        tier = _odds_tier_from_possession(proj_home, proj_away, venue)

        if venue == "home":
            poss_for_odds = float(proj_home) if proj_home is not None else 50.0
        else:
            poss_for_odds = float(proj_away) if proj_away is not None else 50.0

        player_ml = _synthetic_moneyline_from_possession(poss_for_odds)
        opp_poss = 100.0 - poss_for_odds
        opp_ml = _synthetic_moneyline_from_possession(opp_poss)

        # Build synthetic moneyline dict
        synthetic_ml = {
            "home": str(player_ml) if venue == "home" else str(opp_ml),
            "draw": "+330",
            "away": str(opp_ml) if venue == "home" else str(player_ml),
            "_source": "projection",
            "_poss": round(poss_for_odds, 1),
        }

        await db.picks.update_one(
            {"_id": _id},
            {"$set": {
                "moneyline": synthetic_ml,
                "oddsTier": tier,
                "oddsTierSource": "proj_possession",
            }}
        )
        total_updated += 1

    print(f"[BACKFILL] Updated {total_updated} picks with synthetic moneyline + oddsTier")
    print(f"[BACKFILL] Skipped {total_skipped} picks (no possession data)")

    # Also set oddsTier on picks that already HAVE real moneyline
    cursor2 = db.picks.find(
        {"status": "settled", "sport": "soccer", "moneyline": {"$exists": True, "$ne": None},
         "$or": [{"oddsTier": {"$exists": False}}, {"oddsTier": None}]},
        {"_id": 1, "moneyline": 1, "venue": 1, "projHomePoss": 1, "projAwayPoss": 1}
    )
    updated2 = 0
    async for doc in cursor2:
        ml = doc.get("moneyline")
        venue = doc.get("venue", "home")
        # Use real moneyline if available
        if isinstance(ml, dict):
            home_str = str(ml.get("home", "")).strip()
            away_str = str(ml.get("away", "")).strip()
            try:
                home_odds = int(home_str) if home_str and home_str.replace("-", "").replace("+", "").isdigit() else None
                away_odds = int(away_str) if away_str and away_str.replace("-", "").replace("+", "").isdigit() else None
            except ValueError:
                home_odds = away_odds = None

            if venue == "home" and home_odds is not None:
                ml_for_tier = home_odds
            elif venue == "away" and away_odds is not None:
                ml_for_tier = away_odds
            else:
                ml_for_tier = None

            if ml_for_tier is not None:
                if ml_for_tier < 0:
                    prob = -ml_for_tier / (-ml_for_tier + 100)
                else:
                    prob = 100 / (ml_for_tier + 100)

                if prob >= 0.75:
                    tier = "heavy_favorite"
                elif prob >= 0.667:
                    tier = "strong_favorite"
                elif prob >= 0.565:
                    tier = "moderate_favorite"
                elif prob >= 0.524:
                    tier = "slight_favorite"
                elif prob >= 0.476:
                    tier = "close"
                elif prob >= 0.4:
                    tier = "slight_underdog"
                elif prob >= 0.286:
                    tier = "moderate_underdog"
                else:
                    tier = "heavy_underdog"

                await db.picks.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"oddsTier": tier, "oddsTierSource": "moneyline"}}
                )
                updated2 += 1

    print(f"[BACKFILL] Updated {updated2} existing-moneyline picks with oddsTier")


if __name__ == "__main__":
    asyncio.run(backfill())
