"""Optional market-discovery endpoints.

SportsGameOdds supplies the available-market inventory only. Prediction,
fixture verification, historical stats, and settlement remain separate.
"""

from fastapi import APIRouter, Query

from sportsgameodds_client import list_market_board

router = APIRouter(prefix="/api/markets", tags=["markets"])


@router.get("/board")
async def get_market_board(
    hours: int = Query(72, ge=6, le=168),
    league_id: int | None = Query(None, ge=1),
    limit: int = Query(60, ge=1, le=100),
    sport_id: str | None = Query(None, max_length=32),
):
    markets = await list_market_board(
        hours=hours,
        league_id=league_id,
        limit=limit,
        sport_id=sport_id,
    )
    return {
        "markets": markets,
        "source": "SportsGameOdds",
        "mode": "available_market_reference",
    }


@router.get("/soccer")
async def get_soccer_market_board_legacy(
    hours: int = Query(72, ge=6, le=168),
    league_id: int | None = Query(None, ge=1),
    limit: int = Query(60, ge=1, le=100),
):
    """Backward-compatible alias for clients on the previous soccer board."""
    markets = await list_market_board(
        hours=hours,
        league_id=league_id,
        limit=limit,
        sport_id="SOCCER",
    )
    return {
        "markets": markets,
        "source": "SportsGameOdds",
        "mode": "available_market_reference",
    }