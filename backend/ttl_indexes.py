"""
TTL index setup for all cache collections.
Runs once at startup — idempotent (MongoDB ignores duplicate index creation).

Storage budget targets (Atlas M0 free tier = 512 MB):
  fixture_player_cache  → 21 days  (_ts  datetime)
  predictions           →  7 days  (_ts  datetime)
  first_goal_cache      →  7 days  (ts   datetime)
  mlb_cache             →  7 days  (ts   datetime)
  cs2_cache             → 14 days  (_ts  datetime)
  player_season_stats   → 45 days  (_dt  datetime)
  team_fixture_history  → 45 days  (_dt  datetime)
  cache_players         → 45 days  (_dt  datetime)
  team_stats            → 45 days  (_dt  datetime)
"""

DAY = 86400  # seconds

TTL_SPECS = [
    # (collection_name, field_name, ttl_seconds)
    ("fixture_player_cache", "_ts",  21 * DAY),
    ("predictions",          "_ts",   7 * DAY),
    ("first_goal_cache",     "ts",    7 * DAY),
    ("mlb_cache",            "ts",    7 * DAY),
    ("cs2_cache",            "_ts",  14 * DAY),
    ("player_season_stats",  "_dt",  45 * DAY),
    ("team_fixture_history", "_dt",  45 * DAY),
    ("cache_players",        "_dt",  45 * DAY),
    ("team_stats",           "_dt",  45 * DAY),
]


async def setup_ttl_indexes(db) -> None:
    """Create TTL indexes on all cache collections. Safe to call on every startup."""
    for coll_name, field, ttl_secs in TTL_SPECS:
        try:
            await db[coll_name].create_index(
                field,
                expireAfterSeconds=ttl_secs,
                background=True,
            )
        except Exception as e:
            print(f"[TTL] Index {coll_name}.{field}: {e}")
    print(f"[TTL] {len(TTL_SPECS)} TTL indexes verified across cache collections")
