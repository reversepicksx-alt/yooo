"""
Sports configuration endpoint — /api/sports/config
Server-side sport label and availability management.
No App Store update needed to change labels.
"""
import os
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from config import db

log    = logging.getLogger("sports_config")
router = APIRouter(prefix="/api/sports", tags=["sports_config"])

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")

# Canonical default configuration — seeded on first access
_DEFAULTS = [
    {"sport": "soccer", "displayName": "Soccer",     "icon": "football",          "label": None,         "available": True,  "hidden": False},
    {"sport": "mlb",    "displayName": "MLB",         "icon": "baseball",          "label": None,         "available": True,  "hidden": False},
    {"sport": "nfl",    "displayName": "NFL",         "icon": "american-football", "label": "Off Season", "available": False, "hidden": False},
    {"sport": "nba",    "displayName": "NBA",         "icon": "basketball",        "label": "Off Season", "available": False, "hidden": False},
    {"sport": "nhl",    "displayName": "NHL",         "icon": "snow",              "label": "Off Season", "available": False, "hidden": False},
    # CS2 hidden entirely from the sport picker — engine data source (BDL /cs/v1)
    # lost its map-stats endpoints, so predictions can't be graded reliably.
    {"sport": "cs2",    "displayName": "CS2",         "icon": "game-controller",   "label": None,         "available": False, "hidden": True},
    {"sport": "wta",    "displayName": "WTA Tennis",  "icon": "tennisball",        "label": None,         "available": False, "hidden": True},
]
_SPORT_ORDER = ["soccer", "mlb", "nfl", "nba", "nhl", "cs2"]


async def _ensure_seeded() -> None:
    """Insert defaults for any sport not yet in the collection.
    Also force-updates the `available` flag so backend changes propagate
    without needing a DB wipe."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        for cfg in _DEFAULTS:
            await db.sports_config.update_one(
                {"sport": cfg["sport"]},
                {
                    "$set": {
                        "available": cfg["available"],
                        "updatedAt": now,
                        # Clear stale "Unavailable" labels when a sport becomes available
                        "label": cfg["label"],
                        "hidden": cfg.get("hidden", False),
                    },
                    "$setOnInsert": {k: v for k, v in cfg.items() if k not in ("available", "label", "hidden")},
                },
                upsert=True,
            )
    except Exception as e:
        log.warning(f"[SPORTS CONFIG] seed error: {e}")


@router.get("/config")
async def get_sports_config():
    """Return all sport configs.  Mobile fetches this on startup to build the sport picker."""
    try:
        await _ensure_seeded()
        docs = await db.sports_config.find({}, {"_id": 0}).to_list(20)
        present = {d["sport"] for d in docs}
        merged  = list(docs)
        for cfg in _DEFAULTS:
            if cfg["sport"] not in present:
                merged.append(cfg)
        # Hidden sports are removed entirely — mobile never sees them.
        merged = [d for d in merged if not d.get("hidden")]
        merged.sort(key=lambda d: _SPORT_ORDER.index(d["sport"]) if d["sport"] in _SPORT_ORDER else 99)
        return merged
    except Exception as e:
        log.error(f"[SPORTS CONFIG] get failed: {e}")
        return [d for d in _DEFAULTS if not d.get("hidden")]


class SportConfigUpdate(BaseModel):
    sport:       str
    label:       Optional[str]  = None
    available:   Optional[bool] = None
    displayName: Optional[str]  = None


@router.post("/config/update")
async def update_sport_config(
    body: SportConfigUpdate,
    x_admin_secret: Optional[str] = Header(None),
):
    """Admin: update a sport's label, availability, or display name.
    Requires X-Admin-Secret header."""
    if not ADMIN_SECRET or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Admin access required")
    valid = {d["sport"] for d in _DEFAULTS}
    if body.sport not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown sport: {body.sport}")

    update: dict = {"updatedAt": datetime.now(timezone.utc).isoformat()}
    if body.label is not None:
        update["label"] = body.label or None
    if body.available is not None:
        update["available"] = body.available
    if body.displayName is not None:
        update["displayName"] = body.displayName

    await db.sports_config.update_one(
        {"sport": body.sport},
        {"$set": update},
        upsert=True,
    )
    log.info(f"[SPORTS CONFIG] updated {body.sport}: {update}")
    doc = await db.sports_config.find_one({"sport": body.sport}, {"_id": 0})
    return {"ok": True, "sport": body.sport, "config": doc}
