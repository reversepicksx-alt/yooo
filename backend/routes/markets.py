"""Optional market-discovery endpoints.

SportsGameOdds supplies the available-market inventory only. Prediction,
fixture verification, historical stats, and settlement remain separate.
"""

from fastapi import APIRouter, Query

from sportsgameodds_client import list_soccer_market_board

router = APIRouter(prefix="/api/markets", tags=["markets"])


@router.get("/soccer")
async def get_soccer_market_board(
    hours: int = Query(72, ge=6, le=168),
    league_id: int | None = Query(None, ge=1),
    limit: int = Query(60, ge=1, le=100),
):
    markets = await list_soccer_market_board(hours=hours, league_id=league_id, limit=limit)
    return {
        "markets": markets,
        "source": "SportsGameOdds",
        "mode": "available_market_reference",
    }