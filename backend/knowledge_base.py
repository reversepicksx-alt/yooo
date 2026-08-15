"""
knowledge_base.py — Stage 1 of the app-owned knowledge layer.

Compiles durable tactical facts from existing cache collections into two
purpose-built MongoDB collections:
  • knowledge_teams   — team possession profile and build-up style
  • knowledge_players — player role, pass-volume by venue, prop tendencies

Design constraints (based on what is actually available in the DB):
  • team_avg_poss: season-average possession keyed by
      team_avg_poss_{team_id}_{league_id}_{season}; reliable source.
  • team_fixture_history: keyed by teamId only, contains multi-competition
      raw API-Football fixtures (fixture{}, league{}, teams{}, goals{},
      score{}).  Does NOT include per-fixture statistics (shots/passes/
      possession) — those require a separate fixtures/statistics call.
      Filter by league.id to get league-scoped fixture counts.
  • player_positions: stores specificPosition + genericPosition (not
      "position"); also stores role.
  • player_season_stats: stores API-Football players?team=… response;
      statistics[] has passes/shots/games sub-dicts.

Public API
----------
compile_team_style(team_id, league_id, season)  → dict | None
compile_player_profile(player_id, league_id)    → dict | None
fire_and_forget_compile(...)                    → None (schedules background)
get_team_kb(team_id, league_id, season)         → dict | None
get_player_kb(player_id, league_id)             → dict | None
kb_stats()                                      → dict
"""

from __future__ import annotations

import asyncio as aio
from datetime import datetime, timezone
from typing import Optional

from config import db, CURRENT_SEASON

# ── TTL: recompile if older than 24 hours ────────────────────────────────────
_KB_TTL_SECONDS: int = 24 * 3600

# ── Minimum data thresholds ───────────────────────────────────────────────────
_MIN_APPEARANCES_FOR_TENDENCIES: int = 3  # need ≥3 appearances for per-90 flags

# ── Build-up style thresholds (possession %) ─────────────────────────────────
_POSSESSION_HIGH: float = 55.0   # >55% = possession-dominant
_POSSESSION_LOW: float  = 42.0   # <42% = counter-attacking / low-block


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _ts_now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _is_stale(doc: dict) -> bool:
    return (_ts_now() - (doc.get("_ts") or 0)) > _KB_TTL_SECONDS


def _safe_mean(values: list) -> Optional[float]:
    vals = [v for v in values if isinstance(v, (int, float)) and v >= 0]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def _parse_pct(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace("%", "").strip())
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Team style compiler
# ═══════════════════════════════════════════════════════════════════════════════

async def compile_team_style(
    team_id: int,
    league_id: int,
    season: int = CURRENT_SEASON,
) -> Optional[dict]:
    """
    Compile team style from available data sources and upsert into
    knowledge_teams.

    Data sources used:
    - team_avg_poss: season-average possession % (reliable, API-computed)
    - team_fixture_history: raw fixture list keyed by teamId — filtered to
      league_id for counts only; no per-fixture stats are parsed since the
      fixtures cache does not include a statistics[] field.

    Returns the compiled public doc (Mongo internals excluded) on success,
    None on any error.
    """
    if not team_id or not league_id:
        return None

    doc_key = f"{team_id}_{league_id}_{season}"

    # Skip recompile if fresh
    try:
        existing = await db.knowledge_teams.find_one({"_key": doc_key}, {"_id": 0})
        if existing and not _is_stale(existing):
            return {k: v for k, v in existing.items() if not k.startswith("_")}
    except Exception:
        pass

    try:
        # ── 1. Season-average possession from dedicated cache ─────────────────
        # team_avg_poss is keyed exactly by team+league+season so data is
        # already league-scoped and reliable.
        poss_cache_key = f"team_avg_poss_{team_id}_{league_id}_{season}"
        poss_doc = await db.team_avg_poss.find_one(
            {"_key": poss_cache_key}, {"_id": 0, "value": 1}
        )
        season_avg_poss: Optional[float] = (poss_doc or {}).get("value")

        # ── 2. League-scoped fixture count from team_fixture_history ──────────
        # team_fixture_history is keyed by teamId only; it may contain
        # multi-competition history.  Filter by league.id to get a count that
        # is meaningful for this specific league.
        # We deliberately do NOT parse per-fixture stats here: fixtures/ API
        # responses do not include a statistics[] field — that requires a
        # separate fixtures/statistics endpoint call.
        tfh = await db.team_fixture_history.find_one(
            {"teamId": team_id},
            {"_id": 0, "fixtures": 1},
        )
        raw_fixtures: list = (tfh or {}).get("fixtures") or []

        finished_terminal = {"FT", "AET", "PEN"}
        league_fixtures = [
            fx for fx in raw_fixtures
            if (
                (fx.get("league") or {}).get("id") == league_id
                and (fx.get("fixture", {}).get("status", {}).get("short") or "") in finished_terminal
            )
        ]

        # ── 3. Classify build-up style from possession % alone ────────────────
        build_up_style = _classify_build_up(season_avg_poss, len(league_fixtures))
        def_line       = _classify_defensive_line(season_avg_poss)

        doc = {
            "_key": doc_key,
            "teamId": team_id,
            "leagueId": league_id,
            "season": season,
            # Possession signal — from league-specific season-average cache
            "seasonAvgPoss": season_avg_poss,
            # Derived classifications (possession-based; extend when per-fixture
            # stats become available via a fixtures/statistics pull)
            "buildUpStyle": build_up_style,
            "defensiveLineHeight": def_line,
            # Provenance
            "leagueFixtureCount": len(league_fixtures),
            "hasPossessionData": season_avg_poss is not None,
            "_ts": _ts_now(),
            "_compiled": datetime.now(timezone.utc).isoformat(),
        }

        try:
            await db.knowledge_teams.update_one(
                {"_key": doc_key},
                {"$set": doc},
                upsert=True,
            )
        except Exception as e:
            print(f"[KB] knowledge_teams write error team={team_id} league={league_id}: {e}")

        return {k: v for k, v in doc.items() if not k.startswith("_")}

    except Exception as e:
        print(f"[KB] compile_team_style error team={team_id} league={league_id}: {e}")
        return None


def _classify_build_up(avg_poss: Optional[float], n_league_fixtures: int) -> str:
    """
    Classify build-up style from season possession average.
    Requires at least one league fixture count to confirm this is meaningful data.
    """
    if avg_poss is None:
        return "unknown"
    if avg_poss >= _POSSESSION_HIGH:
        return "possession_dominant"
    if avg_poss <= _POSSESSION_LOW:
        return "counter_attacking"
    return "balanced"


def _classify_defensive_line(avg_poss: Optional[float]) -> str:
    """Infer defensive line height from season possession only."""
    if avg_poss is None:
        return "unknown"
    if avg_poss >= 55:
        return "high_line"
    if avg_poss <= 42:
        return "deep_block"
    return "mid_block"


# ═══════════════════════════════════════════════════════════════════════════════
#  Player profile compiler
# ═══════════════════════════════════════════════════════════════════════════════

async def compile_player_profile(
    player_id: int,
    league_id: int,
    season: int = CURRENT_SEASON,
) -> Optional[dict]:
    """
    Read player_season_stats + player_positions, derive prop tendency flags
    and per-venue pass-volume split, and upsert into knowledge_players.

    Schema notes:
    - player_positions uses specificPosition + genericPosition (not "position")
    - player_season_stats.statistics[] holds per-league API-Football stat blobs

    Returns the compiled doc (Mongo internals excluded) on success, None on error.
    """
    if not player_id:
        return None

    doc_key = f"{player_id}_{league_id}"

    # Skip recompile if fresh
    try:
        existing = await db.knowledge_players.find_one({"_key": doc_key}, {"_id": 0})
        if existing and not _is_stale(existing):
            return {k: v for k, v in existing.items() if not k.startswith("_")}
    except Exception:
        pass

    try:
        # ── 1. Season stats ───────────────────────────────────────────────────
        stats_doc = await db.player_season_stats.find_one(
            {"playerId": player_id, "season": season},
            {"_id": 0, "statistics": 1},
        )
        if not stats_doc:
            stats_doc = await db.player_season_stats.find_one(
                {"playerId": player_id, "season": season - 1},
                {"_id": 0, "statistics": 1},
            )

        stats_list: list = (stats_doc or {}).get("statistics") or []
        # Prefer the entry whose league matches; fall back to first entry
        stat_entry = next(
            (s for s in stats_list if (s.get("league") or {}).get("id") == league_id),
            stats_list[0] if stats_list else {},
        )

        games   = stat_entry.get("games")   or {}
        passing = stat_entry.get("passes")  or {}
        shots   = stat_entry.get("shots")   or {}

        appearances  = games.get("appearences") or 0   # API-Football typo
        minutes      = games.get("minutes")      or 0
        passes_total = passing.get("total")
        passes_acc   = passing.get("accuracy")
        passes_key   = passing.get("key")
        shots_total  = shots.get("total")
        shots_on     = shots.get("on")

        # Per-90 normalisation
        per90        = (minutes / 90.0) if minutes and minutes > 0 else None
        passes_per90 = round(passes_total / per90, 1) if passes_total and per90 else None
        shots_per90  = round(shots_total  / per90, 1) if shots_total  and per90 else None

        # ── 2. Position / role from player_positions ──────────────────────────
        # Schema: specificPosition (e.g. "CB"), genericPosition ("D"), role
        pos_doc = await db.player_positions.find_one(
            {"playerId": player_id},
            {"_id": 0, "specificPosition": 1, "genericPosition": 1, "role": 1},
        )
        specific_pos = (pos_doc or {}).get("specificPosition")   # e.g. "CB", "CM", "LW"
        generic_pos  = (pos_doc or {}).get("genericPosition")    # e.g. "D", "M", "F", "GK"
        role         = (pos_doc or {}).get("role")

        # ── 3. Prop tendency flags ────────────────────────────────────────────
        tendencies = _derive_tendencies(
            specific_pos=specific_pos,
            generic_pos=generic_pos,
            role=role,
            passes_per90=passes_per90,
            shots_per90=shots_per90,
            appearances=appearances,
        )

        # ── 4. Venue pass-volume split from settled picks ─────────────────────
        home_pass_avg, away_pass_avg = await _compute_venue_pass_split(player_id)

        doc = {
            "_key": doc_key,
            "playerId": player_id,
            "leagueId": league_id,
            "season": season,
            # Identity — using exact field names from player_positions
            "specificPosition": specific_pos,
            "genericPosition": generic_pos,
            "role": role,
            # Season aggregates (per-90 where meaningful)
            "passesTotal": passes_total,
            "passesPer90": passes_per90,
            "passAccuracyPct": _parse_pct(passes_acc),
            "keyPassesSeason": passes_key,
            "shotsPer90": shots_per90,
            "shotsOnPer90": round(shots_on / per90, 2) if shots_on and per90 else None,
            "appearances": appearances,
            "minutesSeason": minutes,
            # Venue split (derived from settled pick history — pass props only)
            "homePassAvg": home_pass_avg,
            "awayPassAvg": away_pass_avg,
            # Tendency flags (presence = True; absence = not applicable)
            "tendencies": tendencies,
            "_ts": _ts_now(),
            "_compiled": datetime.now(timezone.utc).isoformat(),
        }

        try:
            await db.knowledge_players.update_one(
                {"_key": doc_key},
                {"$set": doc},
                upsert=True,
            )
        except Exception as e:
            print(f"[KB] knowledge_players write error player={player_id}: {e}")

        return {k: v for k, v in doc.items() if not k.startswith("_")}

    except Exception as e:
        print(f"[KB] compile_player_profile error player={player_id} league={league_id}: {e}")
        return None


def _derive_tendencies(
    specific_pos: Optional[str],
    generic_pos: Optional[str],
    role: Optional[str],
    passes_per90: Optional[float],
    shots_per90: Optional[float],
    appearances: int,
) -> dict:
    """
    Return boolean tendency flags for Stage 2 prompt injection.

    Uses specificPosition for precise thresholds; falls back to genericPosition.
    Flags are only set when evidence is sufficient (≥ _MIN_APPEARANCES_FOR_TENDENCIES).
    """
    t: dict = {}
    spos = (specific_pos or "").upper()   # e.g. "CB", "CM", "LW"
    gpos = (generic_pos  or "").upper()   # e.g. "D", "M", "F", "GK"
    rl   = (role         or "").lower()

    # Position bucket using specificPosition first, genericPosition as fallback
    is_gk  = spos == "GK" or gpos == "GK"
    is_def = gpos == "D"  or spos in ("CB", "LB", "RB", "LWB", "RWB", "WB")
    is_mid = gpos == "M"  or spos in ("CM", "CDM", "CAM", "DM", "AM")
    is_fwd = gpos == "F"  or spos in ("ST", "LW", "RW", "CF", "SS")

    # Only derive per-90 flags when we have enough samples
    if appearances >= _MIN_APPEARANCES_FOR_TENDENCIES and passes_per90 is not None:
        # Position-specific high/low pass-volume thresholds
        if is_gk and passes_per90 >= 40:
            t["highVolumePasser"] = True
        elif is_gk and passes_per90 < 25:
            t["lowVolumePasser"] = True
        elif is_def and passes_per90 >= 55:
            t["highVolumePasser"] = True
        elif is_def and passes_per90 < 30:
            t["lowVolumePasser"] = True
        elif is_mid and passes_per90 >= 65:
            t["highVolumePasser"] = True
        elif is_mid and passes_per90 < 35:
            t["lowVolumePasser"] = True
        elif is_fwd and passes_per90 >= 40:
            t["highVolumePasser"] = True
        elif is_fwd and passes_per90 < 20:
            t["lowVolumePasser"] = True

    if appearances >= _MIN_APPEARANCES_FOR_TENDENCIES and shots_per90 is not None:
        if shots_per90 >= 2.5:
            t["frequentShooter"] = True

    # Role-based flags (from cached Gemini-resolved role — no threshold required)
    if rl:
        if "pressing" in rl or "engine" in rl:
            t["pressingRole"] = True
        if "deep" in rl or "pivot" in rl or "anchor" in rl:
            t["deepLyingRole"] = True
        if "false 9" in rl or "false9" in rl:
            t["false9"] = True
        if "winger" in rl or "wide" in rl:
            t["wingerRole"] = True
        if "sweeper" in rl or "libero" in rl:
            t["ballPlayingDefender"] = True

    if appearances < _MIN_APPEARANCES_FOR_TENDENCIES:
        t["thinSample"] = True

    return t


async def _compute_venue_pass_split(player_id: int) -> tuple[Optional[float], Optional[float]]:
    """
    Derive average passes at home vs away from settled pick history.
    Only uses pass-prop picks where actualValue is confirmed.
    """
    try:
        home_passes: list[float] = []
        away_passes: list[float] = []

        async for pick in db.picks.find(
            {
                "playerId": player_id,
                "status": {"$in": ["settled", "SETTLED", "HIT", "MISS"]},
                "propType": {"$regex": "pass", "$options": "i"},
                "actualValue": {"$ne": None},
            },
            {"_id": 0, "actualValue": 1, "venue": 1, "playerIsHome": 1},
        ).limit(40):
            av = pick.get("actualValue")
            if av is None:
                continue
            # Determine venue: prefer explicit playerIsHome, fall back to venue string
            is_home = pick.get("playerIsHome")
            if is_home is None:
                venue = (pick.get("venue") or "").lower()
                is_home = venue == "home"
            try:
                val = float(av)
                if val > 0:
                    if is_home:
                        home_passes.append(val)
                    else:
                        away_passes.append(val)
            except (ValueError, TypeError):
                pass

        return _safe_mean(home_passes), _safe_mean(away_passes)
    except Exception:
        return None, None


# ═══════════════════════════════════════════════════════════════════════════════
#  Fire-and-forget wrapper (called from predict.py)
# ═══════════════════════════════════════════════════════════════════════════════

async def fire_and_forget_compile(
    *,
    player_id: int = 0,
    team_id: int = 0,
    league_id: int = 0,
    opponent_id: int = 0,
    season: int = CURRENT_SEASON,
) -> None:
    """
    Schedule KB compilation tasks for the entities just predicted.
    Runs in the background via asyncio.ensure_future; any failure is logged
    and swallowed — it must never affect the prediction response.
    """
    tasks = []
    if team_id and league_id:
        tasks.append(_safe_compile_team(team_id, league_id, season))
    if opponent_id and league_id:
        tasks.append(_safe_compile_team(opponent_id, league_id, season))
    if player_id and league_id:
        tasks.append(_safe_compile_player(player_id, league_id, season))
    if tasks:
        aio.ensure_future(aio.gather(*tasks, return_exceptions=True))


async def _safe_compile_team(team_id: int, league_id: int, season: int) -> None:
    try:
        await compile_team_style(team_id, league_id, season)
    except Exception as e:
        print(f"[KB fire-and-forget] team compile error team={team_id}: {e}")


async def _safe_compile_player(player_id: int, league_id: int, season: int) -> None:
    try:
        await compile_player_profile(player_id, league_id, season)
    except Exception as e:
        print(f"[KB fire-and-forget] player compile error player={player_id}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Readers (Stage 2 — fast, no API calls, no recompile)
# ═══════════════════════════════════════════════════════════════════════════════

async def get_team_kb(
    team_id: int,
    league_id: int,
    season: int = CURRENT_SEASON,
) -> Optional[dict]:
    """Return the knowledge_teams doc for a team (read-only, no recompile)."""
    if not team_id:
        return None
    doc_key = f"{team_id}_{league_id}_{season}"
    try:
        doc = await db.knowledge_teams.find_one({"_key": doc_key}, {"_id": 0})
        if doc:
            return {k: v for k, v in doc.items() if not k.startswith("_")}
    except Exception:
        pass
    return None


async def get_player_kb(
    player_id: int,
    league_id: int = 0,
) -> Optional[dict]:
    """
    Return the knowledge_players doc for a player (read-only, no recompile).
    Falls back to any-league entry if the league-specific one is absent.
    """
    if not player_id:
        return None
    doc_key = f"{player_id}_{league_id}"
    try:
        doc = await db.knowledge_players.find_one({"_key": doc_key}, {"_id": 0})
        if not doc and league_id:
            doc = await db.knowledge_players.find_one(
                {"playerId": player_id}, {"_id": 0}
            )
        if doc:
            return {k: v for k, v in doc.items() if not k.startswith("_")}
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Stats (owner dashboard)
# ═══════════════════════════════════════════════════════════════════════════════

async def kb_stats() -> dict:
    """Return collection-level counts and freshness metrics."""
    try:
        cutoff = _ts_now() - _KB_TTL_SECONDS
        results = await aio.gather(
            db.knowledge_teams.count_documents({}),
            db.knowledge_teams.count_documents({"_ts": {"$gt": cutoff}}),
            db.knowledge_players.count_documents({}),
            db.knowledge_players.count_documents({"_ts": {"$gt": cutoff}}),
            return_exceptions=True,
        )
        team_total, team_fresh, player_total, player_fresh = results
        return {
            "teamsTotal":   team_total   if isinstance(team_total,   int) else 0,
            "teamsFresh":   team_fresh   if isinstance(team_fresh,   int) else 0,
            "playersTotal": player_total if isinstance(player_total, int) else 0,
            "playersFresh": player_fresh if isinstance(player_fresh, int) else 0,
            "ttlHours":     _KB_TTL_SECONDS // 3600,
        }
    except Exception as e:
        return {
            "error":        str(e),
            "teamsTotal":   0,
            "teamsFresh":   0,
            "playersTotal": 0,
            "playersFresh": 0,
        }
