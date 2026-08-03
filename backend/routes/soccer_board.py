"""
Native Soccer Board API
GET /api/soccer/board-native  — serves cached board instantly; triggers
                                background rebuild when cache is > 6h old.
POST /api/soccer/board-native/refresh — owner-only manual rebuild trigger.
"""
from fastapi import APIRouter, BackgroundTasks, Request
from datetime import datetime, timezone
import logging

router = APIRouter()
log    = logging.getLogger("soccer_board")


def _db():
    from server import db as _d
    return _d


@router.get("/api/soccer/board-native")
async def get_native_soccer_board(background_tasks: BackgroundTasks):
    db     = _db()
    cached = await db.soccer_board_cache.find_one({"_id": "main"}, {"_id": 0})

    if cached:
        # Trigger a background refresh if cache is stale (> 6 h)
        updated_str = cached.get("updatedAt")
        if updated_str:
            try:
                updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                age_h = (datetime.now(timezone.utc) - updated_at).total_seconds() / 3600
                if age_h > 6:
                    background_tasks.add_task(_bg_rebuild, db)
            except Exception:
                pass
        return cached

    # No cache yet — build synchronously (first request after deploy)
    from soccer_board_builder import build_soccer_board
    result = await build_soccer_board(db)
    return result


@router.post("/api/soccer/board-native/refresh")
async def refresh_native_soccer_board(request: Request):
    """Owner-only: force an immediate rebuild of the board cache."""
    import os
    owner_pin = os.environ.get("OWNER_PIN", "")
    body = await request.json()
    if body.get("pin") != owner_pin:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")

    db = _db()
    from soccer_board_builder import build_soccer_board
    result = await build_soccer_board(db)
    total  = sum(len(f.get("players", [])) for f in result.get("fixtures", []))
    return {
        "ok": True,
        "fixtures": len(result.get("fixtures", [])),
        "players": total,
        "updatedAt": result.get("updatedAt"),
    }


async def _bg_rebuild(db):
    from soccer_board_builder import build_soccer_board
    try:
        await build_soccer_board(db)
    except Exception as exc:
        log.error(f"[BOARD] Background rebuild failed: {exc}")
