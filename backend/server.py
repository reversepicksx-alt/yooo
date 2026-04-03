import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import db, LIFETIME_SUB_EMAILS, OWNER_EMAIL, init_dynamic_settings

# ── Create App ──
app = FastAPI(title="ReversePicks API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Import and include routers ──
from routes.auth import router as auth_router
from routes.leagues import router as leagues_router
from routes.players import router as players_router
from routes.predict import router as predict_router
from routes.combo import router as combo_router
from routes.scan import router as scan_router
from routes.picks import router as picks_router
from routes.chat import router as chat_router
from routes.misc import router as misc_router
from routes.tactical import router as tactical_router
from routes.basketball_predict import router as basketball_router
from routes.square import router as square_router
from routes.admin import router as admin_router
from routes.miss_analysis import router as miss_router
from cache import seed_cache, background_refresh_loop
from basketball_cache import seed_bball_cache, bball_background_refresh, get_bball_cache_status

app.include_router(auth_router)
app.include_router(leagues_router)
app.include_router(players_router)
app.include_router(predict_router)
app.include_router(combo_router)
app.include_router(scan_router)
app.include_router(picks_router)
app.include_router(chat_router)
app.include_router(misc_router)
app.include_router(tactical_router)
app.include_router(basketball_router)
app.include_router(square_router)
app.include_router(admin_router)
app.include_router(miss_router)


# ── Startup: seed grants for lifetime VIPs ──
@app.on_event("startup")
async def seed_grants():
    # Load dynamic settings (API keys from MongoDB) before anything else
    await init_dynamic_settings()
    for email in LIFETIME_SUB_EMAILS:
        await db.manual_access_grants.update_one(
            {"email": email},
            {"$set": {"email": email, "access_type": "Lifetime"}},
            upsert=True
        )
    await db.manual_access_grants.update_one(
        {"email": OWNER_EMAIL},
        {"$set": {"email": OWNER_EMAIL, "access_type": "Owner"}},
        upsert=True
    )
    # Seed the API-Football lookup cache (non-blocking)
    import asyncio
    asyncio.create_task(seed_cache())
    # Build master team cache for smart opponent resolution
    from team_resolver import build_teams_cache
    asyncio.create_task(build_teams_cache())
    # Start 24h auto-refresh loop for transfers + data freshness
    asyncio.create_task(background_refresh_loop())
    # Seed basketball (NBA + WNBA) cache
    asyncio.create_task(seed_bball_cache())
    asyncio.create_task(bball_background_refresh())


# ── Legacy alias: /api/search-player ──
@app.get("/api/search-player")
async def search_player_alias(query: str = ""):
    """Legacy compatibility endpoint — redirects to /api/players/search."""
    from routes.players import search_players
    from models import PlayerSearchRequest
    return await search_players(PlayerSearchRequest(query=query))
