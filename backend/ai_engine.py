"""Data and settlement engine for Reverse Picks."""
import json
import asyncio
import os as _os
from datetime import datetime, timezone, timedelta
from config import db
from settlement_invariants import settle_numeric_result


def _line_allows_push(line) -> bool:
    """Only whole-number player-prop lines can push."""
    try:
        return float(line).is_integer()
    except (TypeError, ValueError):
        return False


def _settle_numeric_result(actual, line, recommendation):
    """Compatibility wrapper around the canonical settlement invariant."""
    return settle_numeric_result(actual, line, recommendation)


def _parse_json(raw: str) -> dict | list | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        raw = raw.rsplit("```", 1)[0]
    try:
        return json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return None

async def auto_settlement_loop():
    """Background loop: check and settle finished games every 15 minutes.
    Each run fires 6+ API calls per unique team in pending picks, so frequent
    runs burn quota fast. 15 min is plenty since picks resolve after the match.
    """
    # RACE GUARD: only ONE settlement bot may run against the shared Atlas DB.
    # The deployed production server is the canonical settler. The dev
    # workspace backend connects to the SAME Atlas database — if its bot also
    # runs, two bots (possibly on different code versions) race to settle the
    # same picks, which caused the Thiago-Martins wrong-settlement (one bot on
    # stale code matched a 10-week-old fixture). Set ENABLE_SETTLE_BOT=1 to
    # force-enable in development for testing.
    _is_deployment = bool(_os.environ.get("REPLIT_DEPLOYMENT"))
    if not _is_deployment and _os.environ.get("ENABLE_SETTLE_BOT") != "1":
        print("[AI ENGINE] Auto-settlement bot DISABLED (dev workspace — prod deployment is the canonical settler; set ENABLE_SETTLE_BOT=1 to override)")
        return
    await asyncio.sleep(5)   # Short delay then run immediately on startup
    print("[AI ENGINE] Auto-settlement bot started (15 min interval)")

    while True:
        try:
            await _run_auto_settlement()
        except Exception as e:
            print(f"[AUTO-SETTLE] Error: {e}")
        await asyncio.sleep(900)  # Check every 15 minutes — shared BDL key needs breathing room


async def _try_settle_mlb(pick: dict) -> bool:
    """
    Settle an MLB pick using game log data.

    Handles both ID spaces:
      • Stats API IDs (≥ _STATSAPI_ID_THRESHOLD, ~100k) → mlb_client fetches
        from statsapi.mlb.com directly with proper field names.
      • BDL IDs (< _STATSAPI_ID_THRESHOLD) → mlb_client fetches from
        BallDontLie and _transform_bdl_log normalises the field schema to
        Stats-API shape (p_k, hits, rbi, ip, etc.) before caching/returning.
      • Composite props (hitter_fantasy_points, hits_runs_rbis, etc.) are
        detected via _COMPOSITE_HANDLERS and computed from sub-fields.

    Game matching is done by date proximity to pick creation; BDL game IDs
    are unreliable on picks so we never filter by ID.

    Called from _run_auto_settlement() for picks with sport='mlb'.
    Returns True when a settlement was written.
    """
    try:
        import mlb_client
        from mlb_engine import (
            ALL_PROP_FIELDS, PITCHER_PROPS,
            _compute_fantasy_pts, _compute_hits_runs_rbis,
            _compute_pitcher_fantasy, _compute_pitching_outs,
        )
    except ImportError as _ie:
        print(f"[MLB SETTLE] Import error: {_ie}")
        return False

    # Composite props are stored in ALL_PROP_FIELDS as placeholder strings like
    # "__fantasy_pts__".  We detect those and call the real compute function.
    _COMPOSITE_HANDLERS = {
        "__fantasy_pts__":      _compute_fantasy_pts,
        "__hits_runs_rbis__":   _compute_hits_runs_rbis,
        "__pitcher_fantasy__":  _compute_pitcher_fantasy,
        "__pitching_outs__":    _compute_pitching_outs,
    }

    player_id = pick.get("playerId")
    prop_type  = (pick.get("propType") or "").lower()
    line       = pick.get("line")
    rec        = (pick.get("recommendation") or "over").upper()

    if not player_id or not prop_type or line is None:
        return False

    field = ALL_PROP_FIELDS.get(prop_type)
    if not field:
        print(f"[MLB SETTLE] Unknown prop_type={prop_type}, skipping")
        return False

    # Only settle picks that are 4+ hours old (baseball games ~3–4 h)
    pick_created = None
    for ts_key in ("timestamp", "createdAt"):
        raw = pick.get(ts_key)
        if raw:
            try:
                pick_created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                break
            except Exception:
                pass

    if pick_created:
        hours_old = (datetime.now(timezone.utc) - pick_created).total_seconds() / 3600
        if hours_old < 4:
            return False  # Too early — game might still be in progress

    # Always resolve against the current calendar year
    current_year = datetime.now(timezone.utc).year

    # ── Fetch game logs via Stats API (correct ID space) ──────────────────────
    try:
        logs = await mlb_client.get_player_game_logs(int(player_id), current_year)
    except Exception as _e:
        print(f"[MLB SETTLE] Stats API log fetch failed player={player_id}: {_e}")
        return False

    if not logs:
        print(f"[MLB SETTLE] No game logs for player {player_id} season {current_year}")
        return False

    # ── Match game log by date proximity to pick creation ─────────────────────
    # Pick is created before the game. Find the first game played on pick_date
    # or within 2 days after (handles late-night / next-day situations).
    target_log = None
    if pick_created:
        from datetime import date as _date, timedelta as _td
        target_date = pick_created.date()
        window_end  = target_date + _td(days=2)
        # logs are newest-first — iterate reversed (oldest-first) to find earliest match
        for log in reversed(logs):
            log_date_str = (log.get("date") or "")[:10]
            if not log_date_str:
                continue
            try:
                log_date = _date.fromisoformat(log_date_str)
                if target_date <= log_date <= window_end:
                    target_log = log
                    break
            except Exception:
                pass
        if not target_log:
            # No game found in window — pick might be old; use most recent completed game
            target_log = logs[0]
    else:
        target_log = logs[0]

    # ── Composite props: compute from multiple fields ─────────────────────────
    _composite_fn = _COMPOSITE_HANDLERS.get(field)
    if _composite_fn:
        raw_val = _composite_fn(target_log)
        if raw_val is None:
            print(f"[MLB SETTLE] Composite '{field}' returned None for player {player_id} "
                  f"date={target_log.get('date','?')} — missing sub-fields")
            return False
    else:
        raw_val = target_log.get(field)
        if raw_val is None:
            print(f"[MLB SETTLE] Field '{field}' not in log for player {player_id} "
                  f"date={target_log.get('date','?')} — may be wrong group (hit vs pitch)")
            return False

    try:
        if prop_type == "innings_pitched":
            # Convert "5.2" BDL fractional IP → float outs representation
            parts = str(raw_val).split(".")
            whole = int(parts[0])
            frac  = int(parts[1]) if len(parts) > 1 else 0
            actual: float = whole + frac / 3.0
        else:
            actual = float(raw_val)
    except Exception:
        return False

    # ── DNP guard for pitcher props ───────────────────────────────────────────
    # If a pitcher was scratched / did not appear, BDL returns ip=0 and all
    # counting stats as 0.  Settling an UNDER with actual=0 in that case is a
    # false hit — the player never took the mound.  Detect by checking IP: if
    # IP == 0 and the prop value is also 0, void the pick (push) so it doesn't
    # inflate the hit-rate ledger.
    _PITCHER_PROP_SET = {
        "pitcher_strikeouts", "hits_allowed", "earned_runs",
        "walks_allowed", "pitches_thrown", "batters_faced",
        "pitcher_fantasy_score", "pitching_outs",
    }
    if prop_type in _PITCHER_PROP_SET and actual == 0.0:
        ip_raw = target_log.get("ip")
        if ip_raw is not None:
            try:
                ip_parts = str(ip_raw).split(".")
                ip_float = int(ip_parts[0]) + (int(ip_parts[1]) / 3.0 if len(ip_parts) > 1 else 0)
                if ip_float == 0.0:
                    print(f"[MLB SETTLE] DNP detected for {pick.get('playerName')} {prop_type} "
                          f"(IP=0, stat=0) — discarding")
                    from routes.picks import _discard_dnp_pick
                    await _discard_dnp_pick(
                        pick,
                        pick.get("email"),
                        "Player did not pitch (IP=0, stat=0)",
                    )
                    return True
            except Exception:
                pass

    line_f = float(line)
    result = _settle_numeric_result(actual, line_f, rec)

    await db.picks.update_one(
        {"pickId": pick["pickId"]},
        {"$set": {
            "actualValue":  round(actual, 1),
            "result":       result,
            "status":       "settled",
            "matchStatus":  "final",
            "settledAt":    datetime.now(timezone.utc).isoformat(),
            "settledBy":    "mlb_auto",
        }},
    )
    try:
        from routes.push import _notify_pick_settled
        await _notify_pick_settled(pick, result)
    except Exception as _pe:
        print(f"[MLB SETTLE] push error: {_pe}")
    print(f"[MLB SETTLE] ✓ {pick.get('playerName')} {prop_type} actual={actual:.2f} line={line_f} rec={rec} → {result}")
    return True


async def _try_settle_bdl(pick: dict, sport: str) -> bool:
    """
    Generic BDL settler for NBA / NFL / NHL / WNBA.
    Fetches game logs from the matching client, finds the game by date,
    reads the prop field, and writes the result to MongoDB.
    """
    try:
        if sport == "nba":
            import nba_client as bdl_client
            import nba_engine as bdl_engine
            PROP_MAP = bdl_engine.NBA_PROPS
            min_hours = 3
        elif sport == "nfl":
            import nfl_client as bdl_client
            import nfl_engine as bdl_engine
            PROP_MAP = bdl_engine.NFL_PROPS
            min_hours = 5
        elif sport == "nhl":
            import nhl_client as bdl_client
            import nhl_engine as bdl_engine
            PROP_MAP = bdl_engine.NHL_PROPS
            min_hours = 3
        elif sport == "wnba":
            import wnba_client as bdl_client
            import wnba_engine as bdl_engine
            PROP_MAP = bdl_engine.WNBA_PROPS
            min_hours = 3
        else:
            return False
    except ImportError as e:
        print(f"[{sport.upper()} SETTLE] Import error: {e}")
        return False

    player_id = pick.get("playerId")
    prop_type = (pick.get("propType") or "").lower()
    line      = pick.get("line")
    rec       = (pick.get("recommendation") or "over").upper()

    if not player_id or not prop_type or line is None:
        return False

    field = PROP_MAP.get(prop_type)
    if not field:
        return False

    # Age gate
    pick_created = None
    for ts_key in ("timestamp", "createdAt"):
        raw = pick.get(ts_key)
        if raw:
            try:
                pick_created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                break
            except Exception:
                pass

    if pick_created:
        hours_old = (datetime.now(timezone.utc) - pick_created).total_seconds() / 3600
        if hours_old < min_hours:
            return False

    # Fetch game logs from the season and exact game identity stored with the
    # prediction. New NBA picks must not be settled against the newest row from
    # a different season or game.
    current_year = datetime.now(timezone.utc).year
    stored_game_id = pick.get("gameId") or pick.get("fixtureId")
    stored_season = pick.get("season")
    try:
        if sport == "nhl":
            season = f"{current_year - 1}{current_year}"
            logs = await bdl_client.get_player_game_logs(int(player_id), season)
        elif sport == "wnba":
            logs = await bdl_client.get_player_game_logs(int(player_id), stored_season or current_year)
        else:
            season = stored_season or getattr(bdl_client, "CURRENT_NBA_SEASON", current_year)
            logs = await bdl_client.get_player_game_logs(int(player_id), season)
    except Exception as e:
        print(f"[{sport.upper()} SETTLE] Log fetch failed player={player_id}: {e}")
        return False

    if not logs:
        return False

    # Match the exact game whenever a prediction stored one. For NBA, refusing
    # an identity-less settlement is safer than guessing from a date window.
    target_log = None
    if sport == "nba":
        if stored_game_id is None:
            return False
        target_log = next(
            (log for log in logs if str(log.get("game_id")) == str(stored_game_id)),
            None,
        )
        if not target_log:
            return False
    elif pick_created:
        from datetime import date as _date, timedelta as _td
        target_date = pick_created.date()
        window_end  = target_date + _td(days=2)
        for log in reversed(logs):
            log_date_str = (log.get("date") or "")[:10]
            if not log_date_str:
                continue
            try:
                log_date = _date.fromisoformat(log_date_str)
                if target_date <= log_date <= window_end:
                    target_log = log
                    break
            except Exception:
                pass
        if not target_log:
            target_log = logs[0]
    else:
        target_log = logs[0]

    raw_val = target_log.get(field)
    if raw_val is None:
        return False

    try:
        actual = float(raw_val)
    except Exception:
        return False

    line_f = float(line)
    result = _settle_numeric_result(actual, line_f, rec)

    await db.picks.update_one(
        {"pickId": pick["pickId"]},
        {"$set": {
            "actualValue":  round(actual, 2),
            "result":       result,
            "status":       "settled",
            "matchStatus":  "final",
            "settledAt":    datetime.now(timezone.utc).isoformat(),
            "settledBy":    f"{sport}_auto",
        }},
    )
    try:
        from routes.push import _notify_pick_settled
        await _notify_pick_settled(pick, result)
    except Exception as _pe:
        print(f"[{sport.upper()} SETTLE] push error: {_pe}")
        print(f"[{sport.upper()} SETTLE] ✓ {pick.get('playerName')} {prop_type} actual={actual:.2f} line={line_f} rec={rec} → {result}")
    return True
async def _repair_pending_review_soccer_batch(
    limit: int = 24,
    *,
    include_legacy: bool = False,
    pick_ids: list[str] | None = None,
    ignore_cooldown: bool = False,
) -> dict:
    """Repair a bounded batch of legacy soccer pending-review picks.

    These records are intentionally kept out of the normal live/pending
    settlement query.  They still need the same deterministic exact-fixture
    settlement path, but must never be sent through external generation or silently counted
    as settled while their source is unverified.
    """
    from routes.picks import _settle_soccer_pick
    from utils import set_api_request_priority, reset_api_request_priority
    _retry_before = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()

    review_query = {
        "$or": [
            {"status": "pending_review"},
            {"result": "pending_review"},
            {"settlementReview": {"$exists": True}},
        ]
    }
    if include_legacy:
        review_query["$or"].append({
            "status": "settled",
            "result": {"$in": ["hit", "miss", "push", "dnp", "pass"]},
            "settlementSource.verified": {"$ne": True},
            "correctedManually": {"$ne": True},
            # Do not re-select rows that this repair already reconciled from
            # a legacy stored final. Without this exclusion, the force-drain
            # repeatedly picked the same first legacy row forever.
            "settlementSource.verificationMethod": {
                "$ne": "legacy_numeric_reconciliation",
            },
        })

    candidate_filters = [
        review_query,
        {
            "$or": [
                {"sport": "soccer"},
                {"sport": {"$exists": False}},
            ]
        },
    ]
    # Legacy rows are normalized by the user-picks consistency pass. Once
    # that marker exists, never send the same row back through provider repair
    # or the force-drain can repeatedly select it while it is being read.
    if include_legacy:
        candidate_filters.append({
            "settlementSource.verificationMethod": {
                "$ne": "legacy_numeric_reconciliation",
            },
        })
    # A specifically requested pick is an explicit retry/debug operation and
    # must bypass the normal deferred-record cooldown.
    if not pick_ids and not ignore_cooldown:
        candidate_filters.append({
            "$or": [
                {"settlementRepairLastAttemptAt": {"$exists": False}},
                {"settlementRepairLastAttemptAt": {"$lt": _retry_before}},
            ]
        })
    candidate_query = {"$and": candidate_filters}
    if pick_ids:
        candidate_query["$and"].append({"pickId": {"$in": [str(v) for v in pick_ids]}})

    candidates = await db.picks.find(
        candidate_query,
        {"_id": 0},
    ).sort([("fixtureId", -1), ("createdAt", 1), ("timestamp", 1)]).to_list(
        max(1, min(int(limit or 24), 40))
    )

    if not candidates:
        print("[REVIEW REPAIR] Batch complete: found=0 repaired=0 deferred=0 errors=0")
        return {"found": 0, "repaired": 0, "deferred": 0, "errors": 0}

    repaired = deferred = errors = 0
    for pick_doc in candidates:
        pick_id = pick_doc.get("pickId")
        player_id = pick_doc.get("playerId") or 0
        if not pick_id or not player_id:
            deferred += 1
            if pick_id:
                await db.picks.update_one(
                    {"pickId": pick_id},
                    {"$set": {
                        "settlementRepairLastAttemptAt": datetime.now(timezone.utc).isoformat(),
                        "settlementRepairLastAttemptReason": "missing playerId or pickId",
                    }},
                )
            continue

        # A local maintenance soft budget must not mask available provider
        # quota for this explicit repair operation. The batch itself remains
        # bounded, and API-Football's real 429/quota breaker still applies.
        _priority_token = set_api_request_priority(True)
        try:
            result = await _settle_soccer_pick(
                {
                    **pick_doc,
                    "id": pick_id,
                    "status": "live",
                    "_settlement_repair": True,
                },
                pick_doc.get("teamId") or 0,
                player_id,
                pick_doc.get("opponentName", ""),
                pick_doc.get("propType", ""),
                pick_doc.get("leagueId") or 0,
            )
        except Exception as exc:
            errors += 1
            print(f"[REVIEW REPAIR] {pick_doc.get('playerName', '?')} error: {exc}")
            await db.picks.update_one(
                {"pickId": pick_id},
                {"$set": {
                    "settlementRepairLastAttemptAt": datetime.now(timezone.utc).isoformat(),
                    "settlementRepairLastAttemptReason": str(exc)[:300],
                }},
            )
            continue
        finally:
            reset_api_request_priority(_priority_token)

        source = (result or {}).get("settlementSource") or {}
        if (
            not result
            or source.get("verified") is not True
            or (result.get("actualValue") is None and not result.get("voidReason"))
        ):
            # Legacy review rows already contain a stored final numeric value.
            # They were stranded when the source-audit field was introduced:
            # the consistency guard kept resetting their explicit result to
            # pending_review, even though no live match lookup was needed.
            # Reconcile that stored final deterministically instead of
            # leaving the owner with an endless review badge. This is
            # explicitly marked unverified so it remains distinguishable in
            # calibration audits; it never fabricates a provider stat.
            stored_actual = pick_doc.get("actualValue")
            try:
                stored_actual = float(stored_actual) if stored_actual is not None else None
                stored_line = float(pick_doc.get("line", 0))
            except (TypeError, ValueError):
                stored_actual = None
                stored_line = 0.0
            if stored_actual is not None:
                from routes.picks import _settle_pick_result
                stored_result, stored_pass_outcome = _settle_pick_result(
                    stored_actual, stored_line, pick_doc
                )
                now_iso = datetime.now(timezone.utc).isoformat()
                legacy_source = {
                    "provider": "legacy-stored-final",
                    "fixtureId": pick_doc.get("fixtureId"),
                    "playerId": pick_doc.get("playerId"),
                    "propType": pick_doc.get("propType"),
                    "statPath": "stored.actualValue",
                    "fixtureStatus": "FT",
                    "verified": False,
                    "verificationMethod": "legacy_numeric_reconciliation",
                    "recordedAt": now_iso,
                }
                legacy_update = {
                    "status": "settled",
                    "result": stored_result,
                    "actualValue": stored_actual,
                    "hitPct": (
                        50 if stored_result in {"push", "dnp", "pass"}
                        else 100 if stored_result == "hit"
                        else 0
                    ),
                    "settledAt": pick_doc.get("settledAt") or now_iso,
                    "resettledAt": now_iso,
                    "settledBy": "legacy_final_reconciliation",
                    "settlementSource": legacy_source,
                    "settlementRepairAudit": {
                        "previous": {
                            "status": pick_doc.get("status"),
                            "result": pick_doc.get("result"),
                            "actualValue": pick_doc.get("actualValue"),
                            "settlementSource": pick_doc.get("settlementSource"),
                            "settlementReview": pick_doc.get("settlementReview"),
                        },
                        "replacement": {
                            "result": stored_result,
                            "actualValue": stored_actual,
                            "settlementSource": legacy_source,
                        },
                        "correctedBy": "legacy_final_reconciliation",
                        "correctedAt": now_iso,
                    },
                }
                if stored_pass_outcome:
                    legacy_update["passOutcome"] = stored_pass_outcome
                reconciled = await db.picks.update_one(
                    {"pickId": pick_id},
                    {
                        "$set": legacy_update,
                        "$unset": {
                            "settlementReview": "",
                            "settlementRepairLastAttemptAt": "",
                            "settlementRepairLastAttemptReason": "",
                        },
                    },
                )
                if reconciled.modified_count:
                    repaired += 1
                    print(
                        f"[REVIEW REPAIR] {pick_doc.get('playerName', '?')} "
                        f"{pick_doc.get('propType', '?')} → {stored_result} "
                        f"actual={stored_actual} (legacy stored final)"
                    )
                    continue
            deferred += 1
            _reason = (
                "no verified settlement"
                if not result
                else "unverified settlement source"
                if source.get("verified") is not True
                else "missing actual value"
            )
            await db.picks.update_one(
                {"pickId": pick_id},
                {"$set": {
                    "settlementRepairLastAttemptAt": datetime.now(timezone.utc).isoformat(),
                    "settlementRepairLastAttemptReason": _reason,
                }},
            )
            continue

        now_iso = datetime.now(timezone.utc).isoformat()
        new_result = "dnp" if result.get("voidReason") else result.get("result")
        update_fields = {
            "status": "settled",
            "result": new_result,
            "actualValue": result.get("actualValue"),
            "hitPct": (
                50 if new_result in {"push", "dnp", "pass"}
                else 100 if new_result == "hit"
                else 0
            ),
            "settledAt": now_iso,
            "resettledAt": now_iso,
            "settledBy": "auto_pending_review_repair",
            "settlementSource": source,
            "settlementRepairAudit": {
                "previous": {
                    "status": pick_doc.get("status"),
                    "result": pick_doc.get("result"),
                    "actualValue": pick_doc.get("actualValue"),
                    "settlementSource": pick_doc.get("settlementSource"),
                    "settlementReview": pick_doc.get("settlementReview"),
                },
                "replacement": {
                    "result": new_result,
                    "actualValue": result.get("actualValue"),
                    "settlementSource": source,
                },
                "correctedBy": "auto_pending_review_repair",
                "correctedAt": now_iso,
            },
        }
        for key in (
            "fixtureId", "fixtureDate", "matchScore", "homeTeam", "awayTeam",
            "finalHomeGoals", "finalAwayGoals", "homePoss", "awayPoss",
            "minutesPlayed", "voidReason", "passOutcome",
        ):
            if result.get(key) is not None:
                update_fields[key] = result[key]

        updated = await db.picks.update_one(
            {
                "pickId": pick_id,
                "$and": [
                    {
                        "$or": [
                            {"status": "pending_review"},
                            {"result": "pending_review"},
                            {"settlementReview": {"$exists": True}},
                            {
                                "status": "settled",
                                "result": {"$in": ["hit", "miss", "push", "dnp", "pass"]},
                                "settlementSource.verified": {"$ne": True},
                                "correctedManually": {"$ne": True},
                            },
                        ]
                    },
                ],
            },
            {
                "$set": update_fields,
                "$unset": {
                    "settlementReview": "",
                    "settlementRepairLastAttemptAt": "",
                    "settlementRepairLastAttemptReason": "",
                },
            },
        )
        if updated.modified_count:
            repaired += 1
            print(
                f"[REVIEW REPAIR] {pick_doc.get('playerName', '?')} "
                f"{pick_doc.get('propType', '?')} → {new_result} "
                f"actual={result.get('actualValue')} "
                f"fixture={result.get('fixtureId') or pick_doc.get('fixtureId')}"
            )
        else:
            deferred += 1
            await db.picks.update_one(
                {"pickId": pick_id},
                {"$set": {
                    "settlementRepairLastAttemptAt": datetime.now(timezone.utc).isoformat(),
                    "settlementRepairLastAttemptReason": "record changed before repair write",
                }},
            )

    summary = {
        "found": len(candidates),
        "repaired": repaired,
        "deferred": deferred,
        "errors": errors,
    }
    print(
        f"[REVIEW REPAIR] Batch complete: found={summary['found']} "
        f"repaired={repaired} deferred={deferred} errors={errors}"
    )
    return summary


async def _refresh_recent_soccer_settlements(
    *,
    limit: int = 24,
    hours: int = 48,
    pick_ids: list[str] | None = None,
    audit_overrides: dict | None = None,
    dry_run: bool = False,
) -> dict:
    """Recheck recent settled soccer picks against the exact final player row.

    API-Football can revise fixture player totals after the first FT response.
    A settled record is therefore not immutable until it has been rechecked
    against the exact fixture/player endpoint.  This refresh deliberately
    bypasses the normal "already settled" guard, but never writes unless the
    provider returns a verified exact-fixture result.
    """
    from routes.picks import _settle_soccer_pick

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=max(1, min(hours, 168)))).isoformat()
    retry_before = (now - timedelta(minutes=12)).isoformat()
    query: dict = {
        "sport": "soccer",
        "status": "settled",
        "fixtureId": {"$exists": True, "$ne": None},
        "correctedManually": {"$ne": True},
        "settlementLocked": {"$ne": True},
    }
    if pick_ids:
        query["pickId"] = {"$in": [str(value) for value in pick_ids if value]}
    else:
        query["$and"] = [
            {
                "$or": [
                    {"settledAt": {"$gte": cutoff}},
                    {"settledAt": {"$exists": False}},
                    {"settledAt": None},
                ]
            },
            {
                "$or": [
                    {"settlementFinalCheckedAt": {"$exists": False}},
                    {"settlementFinalCheckedAt": {"$lt": retry_before}},
                ]
            },
        ]

    candidates = await db.picks.find(query, {"_id": 0}).sort("settledAt", 1).to_list(
        max(1, min(limit, 80))
    )
    entries = []
    checked = repaired = deferred = errors = 0

    for pick_doc in candidates:
        pick_id = str(pick_doc.get("pickId") or "")
        entry = {
            "pickId": pick_id,
            "playerName": pick_doc.get("playerName"),
            "propType": pick_doc.get("propType"),
            "fixtureId": pick_doc.get("fixtureId"),
            "previousResult": pick_doc.get("result"),
            "previousActualValue": pick_doc.get("actualValue"),
        }
        if not pick_id:
            entry["action"] = "skipped: missing pickId"
            entries.append(entry)
            deferred += 1
            continue

        try:
            result = await _settle_soccer_pick(
                {
                    **pick_doc,
                    "id": pick_id,
                    "status": "live",
                    "_settlement_repair": True,
                },
                pick_doc.get("teamId") or 0,
                pick_doc.get("playerId") or 0,
                pick_doc.get("opponentName", ""),
                pick_doc.get("propType", ""),
                pick_doc.get("leagueId") or 0,
            )
            source = dict((result or {}).get("settlementSource") or {})
            if not result or source.get("verified") is not True:
                entry["action"] = "deferred: exact final stat unavailable"
                entries.append(entry)
                deferred += 1
                continue

            checked += 1
            source["verificationMethod"] = "final_stat_refresh"
            source["checkedAt"] = now.isoformat()
            new_result = result.get("result")
            new_actual = result.get("actualValue")
            previous_result = pick_doc.get("result")
            previous_actual = pick_doc.get("actualValue")
            audit_override = (audit_overrides or {}).get(pick_id) or {}
            audit_backfill = bool(
                audit_override and not pick_doc.get("settlementCorrection")
            )
            changed = (
                previous_result != new_result
                or previous_actual != new_actual
                or pick_doc.get("minutesPlayed") != result.get("minutesPlayed")
            )
            entry.update({
                "verifiedResult": new_result,
                "verifiedActualValue": new_actual,
                "verifiedMinutesPlayed": result.get("minutesPlayed"),
                "changed": changed,
            })

            if dry_run:
                entry["action"] = (
                    "would-repair" if changed
                    else "would-backfill-audit" if audit_backfill
                    else "verified-no-change"
                )
                entries.append(entry)
                continue

            if new_result == "dnp":
                from routes.picks import _discard_dnp_pick
                reviewed = await _discard_dnp_pick(
                    pick_doc,
                    pick_doc.get("email"),
                    result.get("voidReason") or "final settlement refresh",
                )
                entry["action"] = "settlement-review" if reviewed else "review-update-failed"
                if reviewed:
                    repaired += 1
                entries.append(entry)
                continue

            update_fields = {
                "status": "settled",
                "result": new_result,
                "actualValue": new_actual,
                "hitPct": (
                    100 if new_result == "hit"
                    else 0 if new_result in {"miss", "dnp"}
                    else 50
                ),
                "minutesPlayed": result.get("minutesPlayed"),
                "settlementSource": source,
                "settlementFinalCheckedAt": now.isoformat(),
                "settlementFinalVerifiedAt": source.get("recordedAt") or now.isoformat(),
                "resettledAt": now.isoformat(),
                "settledBy": "api_football_final_refresh",
            }
            for field in (
                "fixtureId", "fixtureDate", "matchScore", "homeTeam",
                "awayTeam", "finalHomeGoals", "finalAwayGoals",
                "homePoss", "awayPoss", "oppAvgPoss",
            ):
                if result.get(field) is not None:
                    update_fields[field] = result[field]

            update_doc: dict = {"$set": update_fields}
            if new_result == "dnp":
                if result.get("voidReason"):
                    update_fields["voidReason"] = result["voidReason"]
            else:
                update_doc["$unset"] = {"voidReason": ""}

            if changed or audit_backfill:
                update_fields["settlementCorrection"] = {
                    "previousResult": audit_override.get(
                        "previousResult", previous_result
                    ),
                    "previousActualValue": audit_override.get(
                        "previousActualValue", previous_actual
                    ),
                    "previousMinutesPlayed": audit_override.get(
                        "previousMinutesPlayed", pick_doc.get("minutesPlayed")
                    ),
                    "previousSettlementSource": audit_override.get(
                        "previousSettlementSource", pick_doc.get("settlementSource")
                    ),
                    "replacementResult": new_result,
                    "replacementActualValue": new_actual,
                    "replacementMinutesPlayed": result.get("minutesPlayed"),
                    "correctedBy": (
                        "api_football_final_refresh"
                        if changed
                        else "api_football_final_refresh_audit_backfill"
                    ),
                    "correctedAt": now.isoformat(),
                }

            updated = await db.picks.update_one(
                {
                    "pickId": pick_id,
                    "status": "settled",
                    "settlementLocked": {"$ne": True},
                },
                update_doc,
            )
            if updated.modified_count:
                repaired += 1
                entry["action"] = (
                    "repaired" if changed
                    else "audit-backfilled" if audit_backfill
                    else "verified-no-change"
                )
                print(
                    f"[FINAL REFRESH] {pick_doc.get('playerName', '?')} "
                    f"{pick_doc.get('propType', '?')} "
                    f"{previous_actual} → {new_actual} ({new_result})"
                )
            else:
                entry["action"] = "deferred: record changed before write"
                deferred += 1
            entries.append(entry)
        except Exception as exc:
            errors += 1
            entry["action"] = f"error: {str(exc)[:180]}"
            entries.append(entry)

    return {
        "found": len(candidates),
        "checked": checked,
        "repaired": repaired,
        "deferred": deferred,
        "errors": errors,
        "dryRun": dry_run,
        "results": entries,
    }


async def _run_auto_settlement():
    """Check all live picks and settle any finished games."""
    from utils import api_football_request, is_quota_exhausted
    from config import CURRENT_SEASON, NWSL_LEAGUE_ID, NWSL_SEASON
    from routes.picks import _settle_soccer_pick

    if is_quota_exhausted():
        return  # Don't burn quota on settlement checks when there's nothing left

    # A provider's first FT player snapshot can be revised later. Recheck
    # recently settled rows before processing new live picks so stale values
    # never remain in calibration.
    await _refresh_recent_soccer_settlements(limit=24, hours=48)

    # pending_review records were previously invisible to this bot, leaving
    # large legacy backlogs dependent on the owner's six-item UI refresh.
    await _repair_pending_review_soccer_batch()

    # A finished fixture can briefly expose an empty player row immediately
    # after FT. The old live path then permanently stamped the pick DNP, and
    # the normal live/pending query below never revisited it. Recheck recent
    # soccer DNPs with an exact fixture anchor for a bounded recovery window.
    # A verified positive stat is conclusive participation evidence and is
    # re-settled normally; legitimate DNPs simply receive a cooldown marker.
    _dnp_retry_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    _dnp_retry_before = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    _dnp_recheck_candidates = await db.picks.find(
        {
            "sport": "soccer",
            "status": "settled",
            "result": "dnp",
            "fixtureId": {"$exists": True, "$ne": None},
            "settledAt": {"$gte": _dnp_retry_cutoff},
            "$or": [
                {"actualValue": {"$gt": 0}},
                {
                    "voidReason": {
                        "$regex": "not in matchday squad|missing|unpopulated|min \\(min",
                        "$options": "i",
                    }
                },
            ],
            "$and": [{
                "$or": [
                    {"settlementDnpRetryAt": {"$exists": False}},
                    {"settlementDnpRetryAt": {"$lt": _dnp_retry_before}},
                ],
            }],
        },
        {"_id": 0},
    ).sort("settledAt", 1).to_list(24)

    for _dnp_pick in _dnp_recheck_candidates:
        _dnp_pick_id = _dnp_pick.get("pickId")
        if not _dnp_pick_id or not _dnp_pick.get("playerId"):
            continue
        _dnp_retry_at = datetime.now(timezone.utc).isoformat()
        try:
            _dnp_result = await _settle_soccer_pick(
                {
                    **_dnp_pick,
                    "id": _dnp_pick_id,
                    "status": "live",
                    "_settlement_repair": True,
                },
                _dnp_pick.get("teamId") or 0,
                _dnp_pick.get("playerId") or 0,
                _dnp_pick.get("opponentName", ""),
                _dnp_pick.get("propType", ""),
                _dnp_pick.get("leagueId") or 0,
            )
            _dnp_actual = (_dnp_result or {}).get("actualValue")
            _dnp_source = (_dnp_result or {}).get("settlementSource") or {}
            if (
                _dnp_result
                and _dnp_source.get("verified") is True
                and isinstance(_dnp_actual, (int, float))
                and _dnp_actual > 0
                and (_dnp_result.get("result") or "") != "dnp"
            ):
                _dnp_now = datetime.now(timezone.utc).isoformat()
                _dnp_update = {
                    "status": "settled",
                    "result": _dnp_result.get("result"),
                    "actualValue": _dnp_actual,
                    "minutesPlayed": _dnp_result.get("minutesPlayed"),
                    "hitPct": (
                        100 if _dnp_result.get("result") == "hit"
                        else 0 if _dnp_result.get("result") == "miss"
                        else 50
                    ),
                    "settledAt": _dnp_now,
                    "settlementSource": _dnp_source,
                    "settlementDnpRetryAt": _dnp_retry_at,
                    "settlementCorrection": {
                        "reason": "post-FT player-stat recovery overrode premature DNP",
                        "previousResult": "dnp",
                        "previousActualValue": _dnp_pick.get("actualValue"),
                        "actualValue": _dnp_actual,
                        "correctedBy": "auto_dnp_recheck",
                        "correctedAt": _dnp_now,
                    },
                }
                for _field in (
                    "fixtureId", "fixtureDate", "matchScore", "homeTeam",
                    "awayTeam", "finalHomeGoals", "finalAwayGoals",
                    "homePoss", "awayPoss", "passOutcome",
                ):
                    if _dnp_result.get(_field) is not None:
                        _dnp_update[_field] = _dnp_result[_field]
                await db.picks.update_one(
                    {"pickId": _dnp_pick_id, "status": "settled", "result": "dnp"},
                    {"$set": _dnp_update, "$unset": {"voidReason": ""}},
                )
                print(
                    f"[DNP RECHECK] Recovered {_dnp_pick.get('playerName', '?')} "
                    f"{_dnp_pick.get('propType', '?')} actual={_dnp_actual} "
                    f"→ {_dnp_result.get('result')}"
                )
                try:
                    await _notify_pick_settled(_dnp_pick, _dnp_result.get("result"))
                except Exception as _dnp_push_err:
                    print(f"[DNP RECHECK] push error: {_dnp_push_err}")
            else:
                await db.picks.update_one(
                    {"pickId": _dnp_pick_id},
                    {"$set": {
                        "settlementDnpRetryAt": _dnp_retry_at,
                        "settlementDnpRetryReason": (
                            "No verified positive stat yet"
                            if not _dnp_result
                            else "Verified fixture still has no positive stat"
                        ),
                    }},
                )
        except Exception as _dnp_retry_err:
            print(f"[DNP RECHECK] {_dnp_pick.get('playerName', '?')} failed: {_dnp_retry_err}")
            try:
                await db.picks.update_one(
                    {"pickId": _dnp_pick_id},
                    {"$set": {
                        "settlementDnpRetryAt": _dnp_retry_at,
                        "settlementDnpRetryReason": str(_dnp_retry_err)[:300],
                    }},
                )
            except Exception:
                pass

    # Settle "live" picks AND soccer "pending" picks older than 90 min (match duration).
    # MLB pending picks are intentionally excluded from the timestamp-cutoff path —
    # an MLB game can be scheduled 8+ hours after the pick is saved, so 90 min would
    # fire settlement long before the first pitch.  MLB picks only enter here once the
    # live loop promotes them to "live" status (gameId confirmed, in-progress or final).
    _MLB_PENDING_PROPS = {
        "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
        "walks_allowed", "pitches_thrown", "batters_faced",
        "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
        "total_bases", "stolen_bases", "doubles", "plate_appearances",
        "hitter_fantasy_points", "hits_runs_rbis",
        "pitcher_fantasy_score", "pitching_outs",
    }
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    live_picks = await db.picks.find(
        {"$or": [
            {"status": "live"},
            # Soccer pending: 90-min cutoff is appropriate (match is over)
            {"status": "pending", "sport": {"$ne": "mlb"},
             "propType": {"$nin": list(_MLB_PENDING_PROPS)}, "timestamp": {"$lt": cutoff}},
            {"status": "pending", "sport": {"$ne": "mlb"},
             "propType": {"$nin": list(_MLB_PENDING_PROPS)}, "createdAt": {"$lt": cutoff}},
        ]},
        {"_id": 0}
    ).to_list(300)
    if not live_picks:
        return

    settled_count = 0

    # ── MLB settlement ────────────────────────────────────────────────────────
    # Detect by sport field OR by prop type (catches picks saved before sport-fix)
    _MLB_PROP_TYPES = {
        "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
        "walks_allowed", "pitches_thrown", "batters_faced",
        "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
        "total_bases", "stolen_bases", "doubles", "plate_appearances",
        "hitter_fantasy_points", "hits_runs_rbis",
        "pitcher_fantasy_score", "pitching_outs",
    }
    mlb_picks    = [p for p in live_picks if p.get("sport") == "mlb" or p.get("propType", "") in _MLB_PROP_TYPES]
    # Detect CS2 picks by sport field OR propType (catches picks saved before
    # the sport-field repair was deployed — same logic as picks.py repair block).
    _CS2_PROP_PREFIXES = ("map1_", "maps_1_2_", "map3_")
    cs2_picks = [
        p for p in live_picks
        if p.get("sport") == "cs2"
        or str(p.get("propType", "")).startswith(_CS2_PROP_PREFIXES)
    ]
    # WTA picks by sport field
    wta_picks  = [p for p in live_picks if p.get("sport") == "wta"]
    # BDL sports
    nba_picks  = [p for p in live_picks if p.get("sport") == "nba"]
    nfl_picks  = [p for p in live_picks if p.get("sport") == "nfl"]
    nhl_picks  = [p for p in live_picks if p.get("sport") == "nhl"]
    wnba_picks = [p for p in live_picks if p.get("sport") == "wnba"]
    _bdl_picks = set(id(p) for p in nba_picks + nfl_picks + nhl_picks + wnba_picks)
    # Re-partition: remove cs2/wta/bdl from mlb and soccer pools
    mlb_picks    = [p for p in mlb_picks if p not in cs2_picks and p not in wta_picks and id(p) not in _bdl_picks]
    soccer_picks = [p for p in live_picks if p not in mlb_picks and p not in cs2_picks and p not in wta_picks and id(p) not in _bdl_picks]

    for pick in mlb_picks:
        try:
            settled = await _try_settle_mlb(pick)
            if settled:
                settled_count += 1
        except Exception as _me:
            print(f"[MLB SETTLE] Error: {_me}")
            continue
        await asyncio.sleep(2.0)  # pace BDL calls — shared key across all sport clients

    # ── NBA / NFL / NHL / WNBA settlement ────────────────────────────────────
    for _sport, _picks in [("nba", nba_picks), ("nfl", nfl_picks), ("nhl", nhl_picks), ("wnba", wnba_picks)]:
        for pick in _picks:
            try:
                settled = await _try_settle_bdl(pick, _sport)
                if settled:
                    settled_count += 1
            except Exception as _be:
                print(f"[{_sport.upper()} SETTLE] Error: {_be}")
                continue

    # ── Soccer settlement ─────────────────────────────────────────────────────
    if soccer_picks:
        team_ids = list(set(p.get("teamId", 0) for p in soccer_picks if p.get("teamId")))
        for tid in team_ids:
            # Stop mid-run if we've hit the provider daily cap — otherwise a
            # single settlement pass can burn every remaining request against
            # 20+ teams and leave zero headroom for user predictions.
            if is_quota_exhausted():
                print(f"[AUTO-SETTLE] Quota exhausted mid-run — stopping after processing some teams")
                break
            try:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
                team_picks = [p for p in soccer_picks if p.get("teamId") == tid]
                _is_nwsl_team = any(p.get("leagueId") == NWSL_LEAGUE_ID for p in team_picks)
                _soccer_seasons = [NWSL_SEASON] if _is_nwsl_team else [CURRENT_SEASON, CURRENT_SEASON + 1]
                _soccer_league = NWSL_LEAGUE_ID if _is_nwsl_team else None

                # Also cover dates of the oldest pending pick for this team
                # so picks from 3+ days ago don't fall out of the "last 3" window
                oldest_pick = min(
                    (p for p in soccer_picks if p.get("teamId") == tid),
                    key=lambda p: p.get("timestamp") or p.get("createdAt") or "",
                    default=None
                )
                pick_dates = []
                if oldest_pick:
                    for tf in ("timestamp", "createdAt"):
                        raw = oldest_pick.get(tf)
                        if raw:
                            try:
                                pd = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                                pick_dates.append(pd.strftime("%Y-%m-%d"))
                                pick_dates.append((pd - timedelta(days=1)).strftime("%Y-%m-%d"))
                            except Exception:
                                pass
                            break

                # Use last:5 for each season (2 calls instead of 6).
                # last:5 covers ~2-3 weeks of matches which is enough to settle any pending pick.
                # Date-specific calls only added for picks older than yesterday to handle edge cases.
                date_fix_calls = []
                for pd in set(pick_dates):
                    if pd not in (today, yesterday):
                        date_fix_calls.append(
                            api_football_request(
                                "fixtures",
                                {
                                    "team": tid,
                                    "date": pd,
                                    "season": _soccer_seasons[0],
                                    **({"league": _soccer_league} if _soccer_league else {}),
                                },
                            )
                        )

                # 6-day window covers the stale-void cutoff (4d) with a 2-day buffer.
                # Picks older than 4 days are already voided — a 90-day window
                # was burning ~15× more fixture calls than needed every run.
                _fx_from = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%d")
                _fx_to   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                settle_batches = await asyncio.gather(
                    *[
                        api_football_request(
                            "fixtures",
                            {
                                "team": tid,
                                "from": _fx_from,
                                "to": _fx_to,
                                "season": season,
                                **({"league": _soccer_league} if _soccer_league else {}),
                            },
                        )
                        for season in _soccer_seasons
                    ],
                    *date_fix_calls,
                    return_exceptions=True,
                )

                all_fixtures = []
                seen = set()
                for batch in settle_batches:
                    if isinstance(batch, Exception) or not batch:
                        continue
                    for f in batch:
                        fid = f.get("fixture", {}).get("id")
                        if fid and fid not in seen:
                            seen.add(fid)
                            all_fixtures.append(f)

                # Also fetch WC fixtures for this team so WC picks flow through
                # the normal soccer settlement path (same FT gate, same zero-value
                # guards) instead of the broken AI fallback.
                _wc_picks = [p for p in team_picks if p.get("leagueId") == 1]
                if _wc_picks:
                    _wc_batch = await api_football_request(
                        "fixtures",
                        {"team": tid, "league": 1, "season": 2026,
                         "from": _fx_from, "to": _fx_to}
                    ) or []
                    for f in _wc_batch:
                        fid = f.get("fixture", {}).get("id")
                        if fid and fid not in seen:
                            seen.add(fid)
                            all_fixtures.append(f)

                for pick in team_picks:
                    result = await _try_settle_soccer(pick, all_fixtures)
                    if result:
                        settled_count += 1
                    else:
                        # Inline orphan-void: pick >48h, no opponent info → will never settle
                        _pick_ts_str = pick.get("timestamp") or pick.get("createdAt") or ""
                        try:
                            _pick_ts_dt = datetime.fromisoformat(str(_pick_ts_str).replace("Z", "+00:00"))
                            _pick_age_h = (datetime.now(timezone.utc) - _pick_ts_dt).total_seconds() / 3600
                        except Exception:
                            _pick_age_h = 0
                        _has_opp = bool(pick.get("opponentId") or pick.get("opponentName"))
                        if _pick_age_h >= 48 and not _has_opp:
                            from routes.picks import _discard_dnp_pick
                            await _discard_dnp_pick(
                                pick,
                                pick.get("email"),
                                "No opponent info on pick — cannot match fixture",
                            )
                            settled_count += 1
                            print(f"[ORPHAN-VOID] soccer {pick.get('playerName','?')} {pick.get('propType','?')} (no opponent)")
                            continue
            except Exception:
                continue

        # Also handle picks saved without teamId — look up team by name
        orphan_picks = [p for p in soccer_picks if not p.get("teamId") and p.get("teamName")]
        if orphan_picks:
            unique_team_names = list(set(p.get("teamName", "") for p in orphan_picks))
            for team_name in unique_team_names:
                if not team_name:
                    continue
                try:
                    teams_resp = await api_football_request("teams", {"search": team_name[:30]})
                    if not teams_resp:
                        continue
                    tid = teams_resp[0].get("team", {}).get("id")
                    if not tid:
                        continue

                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
                    _orphan_is_nwsl = any(
                        p.get("leagueId") == NWSL_LEAGUE_ID
                        for p in orphan_picks
                        if p.get("teamName") == team_name
                    )
                    _orphan_seasons = [NWSL_SEASON] if _orphan_is_nwsl else [CURRENT_SEASON, CURRENT_SEASON + 1]
                    _orphan_league = NWSL_LEAGUE_ID if _orphan_is_nwsl else None
                    _ofx_from = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%d")
                    _ofx_to   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    orphan_batches = await asyncio.gather(
                        *[
                            api_football_request(
                                "fixtures",
                                {
                                    "team": tid,
                                    "from": _ofx_from,
                                    "to": _ofx_to,
                                    "season": season,
                                    **({"league": _orphan_league} if _orphan_league else {}),
                                },
                            )
                            for season in _orphan_seasons
                        ],
                        return_exceptions=True
                    )
                    all_fixtures = []
                    seen = set()
                    for batch in orphan_batches:
                        if isinstance(batch, Exception) or not batch:
                            continue
                        for f in batch:
                            fid = f.get("fixture", {}).get("id")
                            if fid and fid not in seen:
                                seen.add(fid)
                                all_fixtures.append(f)

                    picks_for_team = [p for p in orphan_picks if p.get("teamName") == team_name]
                    for pick in picks_for_team:
                        await db.picks.update_one(
                            {"pickId": pick["pickId"]},
                            {"$set": {"teamId": tid}}
                        )
                        pick["teamId"] = tid
                        result = await _try_settle_soccer(pick, all_fixtures)
                        if result:
                            settled_count += 1
                except Exception:
                    continue

    # ── CS2 background settlement ──────────────────────────────────────────────
    # Settle CS2 picks that have been pending/live for > 30 min.  Uses the same
    # BDL cache layer as the on-demand path so the 15-min cron run only costs
    # a handful of API calls (all subsequent hits are served from cache).
    if cs2_picks:
        import cs2_client as _cs2_client_ge
        cs2_settle_cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)

        for pick in cs2_picks:
            team_id   = pick.get("teamId")
            player_id = pick.get("playerId")
            opp_name  = pick.get("opponentName", "")
            prop_type = pick.get("propType", "maps_1_2_kills")
            line      = pick.get("line", 0)
            rec       = pick.get("recommendation", "over")
            pick_id   = pick.get("pickId", "")
            email     = pick.get("email", "")

            if not team_id or not player_id:
                continue
            # opp_name may be empty — cs2_client will fall back to the most
            # recent finished match for the team when opponent_name is blank.

            # Skip picks saved in the last 30 min — match can't be over yet
            # Parse pick timestamp (may be Unix-ms int OR ISO string)
            pick_ts = None
            for tf in ("timestamp", "createdAt"):
                raw_ts = pick.get(tf)
                if not raw_ts:
                    continue
                try:
                    if isinstance(raw_ts, (int, float)) and raw_ts > 1_000_000_000:
                        # Unix milliseconds (common for CS2 picks saved from mobile)
                        pick_ts = datetime.fromtimestamp(raw_ts / 1000, tz=timezone.utc)
                    elif isinstance(raw_ts, datetime):
                        pick_ts = raw_ts if raw_ts.tzinfo else raw_ts.replace(tzinfo=timezone.utc)
                    else:
                        pick_ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                        if pick_ts.tzinfo is None:
                            pick_ts = pick_ts.replace(tzinfo=timezone.utc)
                    break
                except Exception:
                    continue

            # Skip picks saved in the last 30 min — match can't be over yet
            if pick_ts and pick_ts > cs2_settle_cutoff:
                continue

            ts_iso = pick.get("timestamp") or pick.get("createdAt", "")
            if isinstance(ts_iso, (int, float)) and ts_iso > 0:
                ts_iso = datetime.fromtimestamp(ts_iso / 1000, tz=timezone.utc).isoformat()

            try:
                result = await _cs2_client_ge.get_cs2_completed_match_result(
                    team_id=int(team_id),
                    player_id=int(player_id),
                    opponent_name=opp_name,
                    prop_type=prop_type,
                    after_iso=str(ts_iso),
                )
            except Exception as _ce:
                print(f"[CS2 AUTO-SETTLE] error for {pick.get('playerName','?')}: {_ce}")
                continue

            if not result or result.get("actualValue") is None:
                now_iso = datetime.now(timezone.utc).isoformat()
                pname = pick.get("playerName", "?")

                # Player DNP — finished match but player not in any map stats
                if result and result.get("playerDNP"):
                    void_reason = "Player did not appear in match stats (DNP)"
                    from routes.picks import _discard_dnp_pick
                    await _discard_dnp_pick(pick, email, void_reason)
                    settled_count += 1
                    print(f"[CS2 AUTO-SETTLE] DNP discarded: {pname} — {void_reason}")
                    continue

                # Map 3 wasn't played (match went 2-0 or 0-2)
                if result and result.get("noMap3"):
                    void_reason = f"Map 3 not played ({result.get('mapsPlayed', '?')} maps total) — voided as DNP"
                    from routes.picks import _discard_dnp_pick
                    await _discard_dnp_pick(pick, email, void_reason)
                    settled_count += 1
                    print(f"[CS2 AUTO-SETTLE] No-map3 DNP discarded: {pname} — {void_reason}")
                    continue

                # Stale-void: if pick is > 7 days old with no data, DNP it so it never hangs forever
                if pick_ts and (datetime.now(timezone.utc) - pick_ts).days >= 7:
                    from routes.picks import _discard_dnp_pick
                    await _discard_dnp_pick(
                        pick,
                        email,
                        "No match data found after 7 days",
                    )
                    settled_count += 1
                    print(f"[CS2 AUTO-SETTLE] Stale-void DNP discarded: {pname} (7d+ no data)")
                continue

            actual_value = result["actualValue"]
            # A half-line can never push; only whole-number lines can.
            result_str = _settle_numeric_result(actual_value, line, rec)

            hit_pct   = 100 if result_str == "hit" else (0 if result_str == "miss" else 50)
            now_iso   = datetime.now(timezone.utc).isoformat()
            settle_set = {
                "status":      "settled",
                "result":      result_str,
                "actualValue": actual_value,
                "hitPct":      hit_pct,
                "matchScore":  result.get("matchScore"),
                "settledAt":   now_iso,
                "settledBy":   "auto_cs2",
                "sport":       "cs2",
            }
            try:
                current = await db.picks.find_one({"pickId": pick_id, "email": email}, {"_id": 0, "status": 1, "sport": 1})
                if current and current.get("status") == "settled" and current.get("sport") == "cs2":
                    continue
                await db.picks.update_one(
                    {"pickId": pick_id, "email": email},
                    {"$set": settle_set},
                )
                settled_count += 1
                print(
                    f"[CS2 AUTO-SETTLE] {pick.get('playerName','?')} {prop_type} "
                    f"actual={actual_value} line={line} → {result_str}"
                )
                try:
                    from routes.push import _send_pick_settled_push
                    asyncio.create_task(_send_pick_settled_push(pick, result_str))
                except Exception as _pe:
                    print(f"[CS2 AUTO-SETTLE] push error: {_pe}")
                # ── In-app notification ──────────────────────────────────────
                try:
                    from routes.notifications import create_notification
                    _emoji = "✅" if result_str == "hit" else ("❌" if result_str == "miss" else ("🔔" if result_str == "dnp" else "↔️"))
                    _prop  = prop_type.replace("_", " ").title()
                    _label = "HIT" if result_str == "hit" else ("MISSED" if result_str == "miss" else ("DNP" if result_str == "dnp" else "PUSH"))
                    await create_notification(
                        email=email,
                        ntype="pick_settled",
                        title=f"{_emoji} {pick.get('playerName','?')} {_prop} — {_label}",
                        body=f"Actual: {actual_value} · Line: {line} · {rec.upper()}",
                        data={
                            "pickId":         pick_id,
                            "playerName":     pick.get("playerName"),
                            "propType":       prop_type,
                            "result":         result_str,
                            "actualValue":    actual_value,
                            "line":           line,
                            "recommendation": rec,
                            "sport":          "cs2",
                        },
                    )
                except Exception as _ne:
                    print(f"[CS2 AUTO-SETTLE] notification error: {_ne}")
            except Exception as _ue:
                print(f"[CS2 AUTO-SETTLE] DB write error: {_ue}")

    # ── WTA background settlement ───────────────────────────────────────────────
    # Settle WTA picks that have been live/pending for > 90 min (match duration).
    if wta_picks:
        import wta_client as _wta_client_ge
        wta_settle_cutoff = datetime.now(timezone.utc) - timedelta(minutes=90)

        for pick in wta_picks:
            player_id     = pick.get("playerId")
            opponent_id   = pick.get("opponentId")
            opponent_name = pick.get("opponentName", "")
            prop_type     = pick.get("propType", "total_games")
            line          = pick.get("line", 0)
            rec           = pick.get("recommendation", "over")
            pick_id       = pick.get("pickId", "")
            email         = pick.get("email", "")

            if not player_id or (not opponent_id and not opponent_name):
                # If pick is >48h old with no opponent info it will never settle → void now
                _orphan_ts = None
                for _tf in ("timestamp", "createdAt"):
                    _raw = pick.get(_tf)
                    if not _raw:
                        continue
                    try:
                        if isinstance(_raw, (int, float)) and _raw > 1_000_000_000:
                            _orphan_ts = datetime.fromtimestamp(_raw / 1000, tz=timezone.utc)
                        else:
                            _orphan_ts = datetime.fromisoformat(str(_raw).replace("Z", "+00:00"))
                            if _orphan_ts.tzinfo is None:
                                _orphan_ts = _orphan_ts.replace(tzinfo=timezone.utc)
                        break
                    except Exception:
                        pass
                if _orphan_ts and (datetime.now(timezone.utc) - _orphan_ts).total_seconds() >= 172800:
                    from routes.picks import _discard_dnp_pick
                    await _discard_dnp_pick(
                        pick,
                        email,
                        "No opponent info stored — WTA pick cannot be settled",
                    )
                    settled_count += 1
                    print(f"[WTA ORPHAN-VOID] {pick.get('playerName','?')} — no opponent info")
                continue

            # Skip picks saved in the last 90 min — match can't be over yet
            pick_ts = None
            for tf in ("timestamp", "createdAt"):
                raw_ts = pick.get(tf)
                if not raw_ts:
                    continue
                try:
                    if isinstance(raw_ts, (int, float)) and raw_ts > 1_000_000_000:
                        pick_ts = datetime.fromtimestamp(raw_ts / 1000, tz=timezone.utc)
                    else:
                        pick_ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                        if pick_ts.tzinfo is None:
                            pick_ts = pick_ts.replace(tzinfo=timezone.utc)
                    break
                except Exception:
                    continue

            if pick_ts and pick_ts > wta_settle_cutoff:
                continue

            ts_iso = pick.get("timestamp") or pick.get("createdAt", "")
            if isinstance(ts_iso, (int, float)) and ts_iso > 0:
                ts_iso = datetime.fromtimestamp(ts_iso / 1000, tz=timezone.utc).isoformat()

            try:
                result = await _wta_client_ge.get_wta_completed_match_result(
                    player_id=int(player_id),
                    opponent_id=int(opponent_id) if opponent_id else None,
                    opponent_name=opponent_name,
                    prop_type=prop_type,
                    after_iso=str(ts_iso),
                )
            except Exception as _we:
                print(f"[WTA AUTO-SETTLE] error for {pick.get('playerName','?')}: {_we}")
                continue

            if not result or result.get("actualValue") is None:
                # Stale-void: WTA matches are weekly so allow 14 days
                if pick_ts and (datetime.now(timezone.utc) - pick_ts).days >= 14:
                    from routes.picks import _discard_dnp_pick
                    await _discard_dnp_pick(
                        pick,
                        email,
                        "No match data found after 14 days",
                    )
                    settled_count += 1
                    print(f"[WTA AUTO-SETTLE] Stale-void DNP discarded: {pick.get('playerName','?')} (14d+ no data)")
                continue

            actual_value = result["actualValue"]
            result_str = _settle_numeric_result(actual_value, line, rec)

            hit_pct  = 100 if result_str == "hit" else (0 if result_str == "miss" else 50)
            now_iso  = datetime.now(timezone.utc).isoformat()
            settle_set = {
                "status":      "settled",
                "result":      result_str,
                "actualValue": actual_value,
                "hitPct":      hit_pct,
                "matchScore":  result.get("matchScore"),
                "settledAt":   now_iso,
                "sport":       "wta",
            }
            try:
                current = await db.picks.find_one({"pickId": pick_id, "email": email}, {"_id": 0, "status": 1})
                if current and current.get("status") == "settled":
                    continue
                await db.picks.update_one(
                    {"pickId": pick_id, "email": email},
                    {"$set": settle_set},
                )
                settled_count += 1
                print(
                    f"[WTA AUTO-SETTLE] {pick.get('playerName','?')} {prop_type} "
                    f"actual={actual_value} line={line} → {result_str}"
                )
                try:
                    from routes.push import _send_pick_settled_push
                    asyncio.create_task(_send_pick_settled_push(pick, result_str))
                except Exception as _pe:
                    print(f"[WTA AUTO-SETTLE] push error: {_pe}")
                try:
                    from routes.notifications import create_notification
                    _emoji = "✅" if result_str == "hit" else ("❌" if result_str == "miss" else ("🔔" if result_str == "dnp" else "↔️"))
                    _prop  = prop_type.replace("_", " ").title()
                    _label = "HIT" if result_str == "hit" else ("MISSED" if result_str == "miss" else ("DNP" if result_str == "dnp" else "PUSH"))
                    await create_notification(
                        email=email,
                        ntype="pick_settled",
                        title=f"{_emoji} {pick.get('playerName','?')} {_prop} — {_label}",
                        body=f"Actual: {actual_value} · Line: {line} · {rec.upper()}",
                        data={
                            "pickId":         pick_id,
                            "playerName":     pick.get("playerName"),
                            "propType":       prop_type,
                            "result":         result_str,
                            "actualValue":    actual_value,
                            "line":           line,
                            "recommendation": rec,
                            "sport":          "wta",
                        },
                    )
                except Exception as _ne:
                    print(f"[WTA AUTO-SETTLE] notification error: {_ne}")
            except Exception as _ue:
                print(f"[WTA AUTO-SETTLE] DB write error: {_ue}")

    # ── Global stale-void: void picks that can never settle ────────────────────
    # Soccer: matches end in 90 min; any pick >4d old that hasn't settled is
    #         orphaned (opponent matched wrong window, league not supported, etc.)
    # WTA:    14-day per-pick limit in loop above; 4d global backstop here catches
    #         any that slipped through opponentId=None guard.
    # CS2:    7-day per-pick limit in loop above; 4d global backstop here too.
    # MLB:    Excluded — the live-loop's stale-final escape handles those.
    # A pick with no sport field is assumed soccer.
    try:
        _now_sv = datetime.now(timezone.utc)
        _cutoff_4d = (_now_sv - timedelta(days=4)).isoformat()
        _stale_candidates = await db.picks.find(
            {"status": {"$in": ["pending", "live"]},
             "sport": {"$nin": ["mlb"]},
             # staleMuteUntil lets individual picks opt out of early stale-void
             # (used for leagues with delayed stat population, e.g. NWSL)
             "$and": [
                 {"$or": [
                     {"timestamp": {"$lt": _cutoff_4d}},
                     {"createdAt":  {"$lt": _cutoff_4d}},
                 ]},
                 {"$or": [
                     {"staleMuteUntil": {"$exists": False}},
                     {"staleMuteUntil": {"$lt": _now_sv.isoformat()}},
                 ]},
             ]},
            {"_id": 0, "pickId": 1, "playerName": 1, "propType": 1,
             "sport": 1, "timestamp": 1, "createdAt": 1,
             "email": 1, "line": 1, "recommendation": 1}
        ).to_list(500)

        _sv_count = 0
        for _sp in _stale_candidates:
            try:
                _sport = _sp.get("sport") or "soccer"
                from routes.picks import _discard_dnp_pick
                await _discard_dnp_pick(
                    _sp,
                    _sp.get("email"),
                    f"No data found after 7+ days ({_sport})",
                )
                _sv_count += 1
                print(f"[STALE-VOID] {_sp.get('playerName','?')} {_sp.get('propType','?')} ({_sport}) → discarded")
            except Exception:
                pass
        if _sv_count:
            settled_count += _sv_count
            print(f"[STALE-VOID] Voided {_sv_count} stale picks as push")
    except Exception as _sve:
        print(f"[STALE-VOID] Error: {_sve}")

    if settled_count > 0:
        print(f"[AUTO-SETTLE] Settled {settled_count} picks")

    # ── Post-save manager change check ────────────────────────────────────────
    # Runs after settlement so the coaching check doesn't burn API-Football
    # quota during the critical settlement window. Each pick is checked at most
    # once per 24 hours; the checker itself handles throttling.
    try:
        from manager_change_checker import run_manager_change_check
        from utils import api_football_request as _mgr_api_fn
        await run_manager_change_check(db, _mgr_api_fn)
    except Exception as _mgr_err:
        print(f"[MGR CHECK] Skipped this run: {_mgr_err}")


async def _try_settle_wc_api_only(pick: dict) -> bool:
    """
    Settle a World Cup pick.

    Strategy:
      1. Fetch all finished WC 2026 fixtures directly (league=1, season=2026).
         The primary settlement path fails because WC picks store the player's
         CLUB teamId, so `fixtures?team={club_id}&season=2026` returns club
         matches, not WC fixtures. We bypass that by querying the WC league
         directly and matching by opponent name + pick date.
      2. Call fixtures/players for the matched WC fixture to get real stats.
    """
    from utils import api_football_request, strip_accents
    import re as _re

    player_name = pick.get("playerName", "")
    prop_type   = pick.get("propType", "")
    line        = pick.get("line", 0)
    pick_id     = pick.get("pickId", "")
    team_name   = pick.get("teamName", "")
    opp_name    = pick.get("opponentName", "")
    player_id   = pick.get("playerId", 0)
    rec         = pick.get("recommendation", "over")

    if not player_name or not prop_type or not pick_id:
        return False

    _PROP_LABELS = {
        "pass_attempts": "passes attempted",
        "passes": "passes completed",
        "shots": "shots",
        "shots_on_target": "shots on target",
        "saves": "saves",
        "goalie_saves": "goalkeeper saves",
        "tackles": "tackles",
        "key_passes": "key passes",
        "goals": "goals",
        "assists": "assists",
        "crosses": "crosses",
        "interceptions": "interceptions",
        "clearances": "clearances",
        "yellow_cards": "yellow cards",
        "minutes": "minutes played",
    }
    prop_label = _PROP_LABELS.get(prop_type, prop_type.replace("_", " "))
    match_desc = f"{team_name} vs {opp_name}" if opp_name else f"{team_name} match"

    # ── Stage 1: API-Football WC 2026 fixtures ────────────────────────────────
    try:
        from config import STAT_LAMBDA_MAP
        stat_fn = STAT_LAMBDA_MAP.get(prop_type)

        # Parse pick creation time for date-window matching
        pick_created_at = None
        for ts_field in ("timestamp", "createdAt"):
            raw_ts = pick.get(ts_field)
            if raw_ts:
                try:
                    pick_created_at = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                    break
                except Exception:
                    pass

        # Fetch all finished WC 2026 fixtures (league_id=1 in API-Football = World Cup)
        wc_fixtures = await api_football_request("fixtures", {
            "league": 1, "season": 2026, "status": "FT"
        }) or []

        # Match fixture by opponent name + created-after guard
        opp_lower = strip_accents((opp_name or "").lower().strip())
        matched_fid = None
        for fx in wc_fixtures:
            fix_date = fx.get("fixture", {}).get("date", "")
            if pick_created_at and fix_date:
                try:
                    fix_dt = datetime.fromisoformat(fix_date.replace("Z", "+00:00"))
                    fix_end = fix_dt + timedelta(hours=2)
                    if fix_end < pick_created_at:
                        continue
                except Exception:
                    pass
            if not opp_lower:
                continue
            home_n = strip_accents(fx.get("teams", {}).get("home", {}).get("name", "").lower())
            away_n = strip_accents(fx.get("teams", {}).get("away", {}).get("name", "").lower())
            if any([
                opp_lower in home_n, opp_lower in away_n,
                home_n in opp_lower, away_n in opp_lower,
            ]):
                matched_fid = fx.get("fixture", {}).get("id")
                break

        if matched_fid and stat_fn:
            players_data = await api_football_request("fixtures/players", {"fixture": matched_fid}) or []
            player_name_key = player_name.lower().strip()
            actual_value = None
            minutes_played = None
            for team_data in players_data:
                for p in team_data.get("players", []):
                    pid = p.get("player", {}).get("id")
                    api_nm = strip_accents((p.get("player", {}).get("name") or "").lower())
                    name_hit = player_name_key in api_nm or api_nm in player_name_key
                    if pid == player_id or (not player_id and name_hit):
                        stats = p.get("statistics", [{}])[0]
                        minutes_played = stats.get("games", {}).get("minutes") or 0
                        actual_value = stat_fn(stats)
                        break
                if actual_value is not None:
                    break

            if actual_value is not None:
                # Zero-value guard: counting stats can never be 0 for a player
                # who played 30+ minutes — API-Football marks FT but takes up to
                # 20 min to populate player rows; defer so next run gets real stats.
                _WC_COUNT_PROPS = {
                    "pass_attempts", "passes", "crosses", "tackles", "key_passes",
                    "shots", "shots_on_target", "interceptions", "blocks", "dribbles",
                    "clearances", "saves",
                }
                _wc_mp = minutes_played or 0
                if actual_value == 0 and prop_type in _WC_COUNT_PROPS and _wc_mp >= 30:
                    print(f"[WC SETTLE DEFER] {player_name} {prop_type} — stat=0 with {_wc_mp} min; API not populated yet, deferring")
                    return False
                # A positive provider stat proves participation even when the
                # minutes field is missing or stale.
                if (
                    minutes_played is not None
                    and minutes_played < 30
                    and not (actual_value is not None and actual_value > 0)
                ):
                    from routes.picks import _discard_dnp_pick
                    await _discard_dnp_pick(
                        pick,
                        pick.get("email"),
                        f"Player only played {minutes_played} min (min 30 required)",
                    )
                    print(f"[WC SETTLE] {player_name}/{prop_type} → DNP discarded ({minutes_played} min)")
                    return True
                result = "win" if (
                    (rec == "over" and actual_value > line) or
                    (rec == "under" and actual_value < line)
                ) else "loss"
                await db.picks.update_one(
                    {"pickId": pick_id},
                    {"$set": {
                        "status": "settled", "result": result,
                        "actualValue": actual_value,
                        "settledAt": datetime.now(timezone.utc).isoformat(),
                        "settledBy": "wc_api", "wcSettled": True,
                    }}
                )
                print(f"[WC SETTLE API] {player_name}/{prop_type} fid={matched_fid} actual={actual_value} → {result.upper()}")
                return True
            else:
                # Fixture is FT but player stats not populated yet — API-Football
                # sometimes takes 5-20 min post-match to fill player rows.
                # Return False so the next bot run (15 min) retries with fresh data.
                print(f"[WC SETTLE] {player_name}/{prop_type}: fixture {matched_fid} FT but no player stats yet — deferring")
                return False
        else:
            if not matched_fid:
                # No FT fixture found at all — match is not finished yet.
                # NEVER fall through to AI guessing; just wait for FT.
                print(f"[WC SETTLE] {player_name}/{prop_type}: no finished WC fixture for opponent='{opp_name}' — match not FT yet")
                return False
    except Exception as _api_err:
        print(f"[WC SETTLE] API-Football stage error: {_api_err}")
        return False

    return False


async def _try_settle_soccer(pick: dict, fixtures: list) -> bool:
    """Try to settle a single soccer pick from available fixtures."""
    from utils import api_football_request, strip_accents

    opponent = pick.get("opponentName", "")
    prop_type = pick.get("propType", "")
    player_id = pick.get("playerId", 0)
    player_name_key = pick.get("playerName", "").lower().strip()

    # Parse pick creation time for timestamp guard
    pick_created_at = None
    for ts_field in ("timestamp", "createdAt", "settledAt"):
        raw_ts = pick.get(ts_field)
        if raw_ts:
            try:
                pick_created_at = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                break
            except Exception:
                pass

    opponent_id = pick.get("opponentId", 0)

    # PERMANENT FIX: if we stored the exact fixtureId on the pick, match by
    # ID directly.  This skips all fuzzy opponent-name matching and is the
    # single source of truth for which match the pick belongs to.
    _stored_fid = pick.get("fixtureId")
    matched = None
    if _stored_fid:
        try:
            _stored_fid_int = int(_stored_fid)
        except (TypeError, ValueError):
            _stored_fid_int = None
        for f in fixtures:
            _f_id = f.get("fixture", {}).get("id")
            if _f_id == _stored_fid or (_stored_fid_int is not None and _f_id == _stored_fid_int):
                matched = f
                print(f"[AUTO-SETTLE] {pick.get('playerName','')} matched by stored fixtureId={_stored_fid}")
                break
        if not matched and _stored_fid_int is not None:
            # Stored fixtureId not in the fetched batch (e.g. different season
            # window) — fetch the exact fixture directly by ID. NEVER fall
            # through to fuzzy matching when the pick knows its fixture: fuzzy
            # matching against a months-old H2H fixture is how the
            # Thiago-Martins wrong-settlement bug happened (settled against a
            # 10-week-old NYCFC 3-0 Columbus match instead of the real one).
            try:
                _direct = await api_football_request(
                    "fixtures", {"id": _stored_fid_int}
                )
                if _direct:
                    matched = _direct[0]
                    print(f"[AUTO-SETTLE] {pick.get('playerName','')} fetched fixture {_stored_fid_int} directly by ID")
            except Exception as _dfe:
                print(f"[AUTO-SETTLE] direct fixture fetch failed for {_stored_fid_int}: {_dfe}")
            if not matched:
                # Fixture ID is known but unfetchable right now — defer to the
                # next bot run rather than risking a fuzzy mismatch.
                print(f"[AUTO-SETTLE] {pick.get('playerName','')} fixtureId={_stored_fid_int} unavailable — deferring (no fuzzy fallback)")
                return False

    if not matched:
        for f in fixtures:
            status = f.get("fixture", {}).get("status", {}).get("short", "")
            if status not in ("FT", "AET", "PEN"):
                continue
            # Timestamp guard: for finished fixtures, ensure the match ENDED after
            # the pick was saved.
            fix_date = f.get("fixture", {}).get("date", "")
            if fix_date and pick_created_at:
                try:
                    fix_dt = datetime.fromisoformat(fix_date.replace("Z", "+00:00"))
                    fix_end = fix_dt + timedelta(hours=2)
                    if fix_end < pick_created_at:
                        continue
                except Exception:
                    pass

            home_id = f.get("teams", {}).get("home", {}).get("id", 0)
            away_id = f.get("teams", {}).get("away", {}).get("id", 0)

            # Primary: match by opponentId (most reliable)
            if opponent_id and (home_id == opponent_id or away_id == opponent_id):
                matched = f
                break

        # Fallback: fuzzy name match (handles partial names like "Sporting KC" vs "Sporting Kansas City")
        if opponent and not matched:
            for f in fixtures:
                status = f.get("fixture", {}).get("status", {}).get("short", "")
                if status not in ("FT", "AET", "PEN"):
                    continue
                # Timestamp guard
                fix_date = f.get("fixture", {}).get("date", "")
                if fix_date and pick_created_at:
                    try:
                        fix_dt = datetime.fromisoformat(fix_date.replace("Z", "+00:00"))
                        fix_end = fix_dt + timedelta(hours=2)
                        if fix_end < pick_created_at:
                            continue
                    except Exception:
                        pass

                home_name = f.get("teams", {}).get("home", {}).get("name", "")
                away_name = f.get("teams", {}).get("away", {}).get("name", "")

                # Resolve common team abbreviations to canonical names
                _TEAM_ALIASES = {
                "lafc": "los angeles fc",
                "la galaxy": "los angeles galaxy",
                "nycfc": "new york city fc",
                "nyrb": "new york red bulls",
                "red bulls": "new york red bulls",
                "sporting kc": "sporting kansas city",
                "inter miami": "inter miami cf",
                "atl utd": "atlanta united",
                "dc united": "d.c. united",
                "cf montreal": "cf montreal",
                "ne revolution": "new england revolution",
                "psg": "paris saint-germain",
                "man city": "manchester city",
                "man utd": "manchester united",
                "spurs": "tottenham hotspur",
                "bvb": "borussia dortmund",
                "mgladbach": "borussia monchengladbach",
                "m'gladbach": "borussia monchengladbach",
                "hertha": "hertha berlin",
                "sociedad": "real sociedad",
                "betis": "real betis",
                }
                opp_raw = strip_accents(opponent.lower().strip())
                opp_lower = _TEAM_ALIASES.get(opp_raw, opp_raw)
                home_lower = strip_accents(home_name.lower())
                away_lower = strip_accents(away_name.lower())
            # Also resolve home/away canonical names through alias map (reverse lookup)
                home_resolved = _TEAM_ALIASES.get(home_lower, home_lower)
                away_resolved = _TEAM_ALIASES.get(away_lower, away_lower)
            # Substring both ways (try both raw and resolved)
                name_hit = any([
                    opp_lower in home_lower, opp_lower in away_lower,
                    home_lower in opp_lower, away_lower in opp_lower,
                    opp_raw in home_lower, opp_raw in away_lower,
                    home_lower in opp_raw, away_lower in opp_raw,
                ])
            # Also check first word match (e.g. "Sporting" in "Sporting Kansas City")
                if not name_hit:
                    opp_words = set(opp_lower.split())
                    home_words = set(home_lower.split())
                    away_words = set(away_lower.split())
                    stopwords = {"fc", "cf", "sc", "ac", "united", "city", "the", "de", "1.", "sv", "vfb"}
                    home_shared = (opp_words & home_words) - stopwords
                    away_shared = (opp_words & away_words) - stopwords
                    name_hit = len(home_shared) >= 2 or len(away_shared) >= 2
                if name_hit:
                    matched = f
                    break

    if not matched:
        return False

    # CRITICAL: Never settle a match that isn't finished — regardless of how
    # we matched (fixtureId direct or fuzzy). This is the single gate that
    # prevents halftime / in-progress settlements.
    _match_status = matched.get("fixture", {}).get("status", {}).get("short", "")
    if _match_status not in ("FT", "AET", "PEN"):
        print(f"[AUTO-SETTLE] SKIP {pick.get('playerName','?')} — status={_match_status} (not finished)")
        return False

    fid = matched.get("fixture", {}).get("id")
    if not fid:
        return False
    # If the pick contains team identity, the matched fixture must contain the
    # same teams. This prevents a name-based fallback from grading a different
    # fixture with a similar opponent label.
    _home_id = matched.get("teams", {}).get("home", {}).get("id")
    _away_id = matched.get("teams", {}).get("away", {}).get("id")
    if pick.get("teamId") and pick["teamId"] not in (_home_id, _away_id):
        print(f"[AUTO-SETTLE] SKIP {pick.get('playerName','?')} — team mismatch for fixture {fid}")
        return False
    if opponent_id and opponent_id not in (_home_id, _away_id):
        print(f"[AUTO-SETTLE] SKIP {pick.get('playerName','?')} — opponent mismatch for fixture {fid}")
        return False

    # Get player stats from the fixture
    try:
        players_data = await api_football_request("fixtures/players", {"fixture": fid})
        if not players_data:
            return False

        actual_value = None
        minutes_played = None
        _player_found = False
        from config import STAT_LAMBDA_MAP
        stat_fn = STAT_LAMBDA_MAP.get(prop_type)

        for team_data in players_data:
            for p in team_data.get("players", []):
                pid = p.get("player", {}).get("id")
                api_name = strip_accents((p.get("player", {}).get("name") or "").lower())

                # Robust name matching: full/substring, last name (>=4 chars),
                # and initial+last (e.g. "S. Montiel" matches "E. Montiel").
                pname_parts = player_name_key.split()
                pname_last = pname_parts[-1] if pname_parts else player_name_key
                pname_initial = (pname_parts[0][0] + ".") if pname_parts else ""
                name_match = bool(player_name_key) and (
                    player_name_key in api_name
                    or api_name in player_name_key
                    or (pname_last and len(pname_last) >= 4 and pname_last in api_name)
                    or (
                        pname_initial and pname_last
                        and (f"{pname_initial} {pname_last}" in api_name
                             or (api_name.startswith(pname_initial) and pname_last in api_name))
                    )
                )
                if pid == player_id or name_match:
                    _player_found = True
                    stats = p.get("statistics", [{}])[0]
                    minutes_played = stats.get("games", {}).get("minutes") or 0
                    if stat_fn:
                        actual_value = stat_fn(stats)
                    # If we matched by name but the pick lacks a playerId, store it
                    # so future lookups are ID-based and more reliable.
                    if _player_found and pid and pid != player_id:
                        await db.picks.update_one(
                            {"pickId": pick["pickId"]},
                            {"$set": {"playerId": pid}}
                        )
                    break
            if actual_value is not None or minutes_played is not None:
                break

        # DNP / not-in-squad settlement: if the match is finished and the player
        # does not appear in the fixtures/players response, they did not make the
        # matchday squad (injured, rested, transferred, etc.). Settle as push/DNP
        # so the pick does not stay stuck in "live" forever.
        if not _player_found:
            print(f"[AUTO-SETTLE-DNP] {pick.get('playerName','?')} not in finished fixture {fid} squad — settling as push/DNP")
            return await _settle_dnp_push(pick, matched, "Player not in matchday squad")

        if actual_value is None:
            return False

        # Zero-value guard: count stats should never be 0 for a player who played
        # 30+ minutes.  API-Football often populates fixture status=FT but leaves
        # all player stats at 0 for 10-30 minutes post-match.  Defer settlement
        # so the background loop retries with fresh data.
        _COUNT_PROPS_SETTLE = {
            "pass_attempts", "passes", "crosses", "tackles", "key_passes",
            "shots", "shots_on_target", "interceptions", "blocks", "dribbles",
            "dribbles_success", "fouls_drawn", "fouls_committed", "clearances",
            "duels_won", "saves", "goals", "assists", "yellow_cards", "red_cards",
            "offsides",
        }
        _mp = minutes_played or 0
        if actual_value == 0 and prop_type in _COUNT_PROPS_SETTLE and _mp >= 30:
            print(f"[AUTO-SETTLE-DEFER] {pick.get('playerName','')} {prop_type} — stat=0 with {_mp} min; likely unpopulated, deferring")
            return False

        # Minimum minutes threshold — if player played < 30 min, void as push
        # (benched, injured off, or DNP effectively — not enough data to fairly grade).
        # IMPORTANT: Some leagues (e.g. NWSL) return minutes=None/0 for players who
        # played the full game.  The `or 0` above converts None → 0.  A non-zero
        # actual_value is definitive proof of participation — skip DNP in that case.
        MIN_MINUTES = 30
        _has_stat_evidence_bg = actual_value is not None and actual_value > 0
        if minutes_played is not None and minutes_played < MIN_MINUTES and not _has_stat_evidence_bg:
            home_goals = matched.get("goals", {}).get("home", 0) or 0
            away_goals = matched.get("goals", {}).get("away", 0) or 0
            _venue = (pick.get("venue") or "home").lower()
            _player_goals = home_goals if _venue == "home" else away_goals
            _opp_goals    = away_goals if _venue == "home" else home_goals
            home_team_name = matched.get("teams", {}).get("home", {}).get("name", "") or ""
            away_team_name = matched.get("teams", {}).get("away", {}).get("name", "") or ""
            home_team_id   = matched.get("teams", {}).get("home", {}).get("id")
            away_team_id   = matched.get("teams", {}).get("away", {}).get("id")
            try:
                from routes.picks import _fetch_fixture_possession
                home_poss, away_poss = await _fetch_fixture_possession(fid, home_team_id, away_team_id)
            except Exception:
                home_poss, away_poss = None, None
            try:
                from game_script_engine import bucket_from_final_score
                _scen_bucket = bucket_from_final_score(home_goals, away_goals)
            except Exception:
                _scen_bucket = None
            from routes.picks import _discard_dnp_pick
            await _discard_dnp_pick(
                pick,
                pick.get("email"),
                f"Player only played {minutes_played} min (min {MIN_MINUTES} required)",
            )
            print(f"[AUTO-SETTLE] {pick.get('playerName','')} {prop_type} → DNP discarded (only {minutes_played} min played)")
            return True

        # Determine result
        line = pick.get("line", 0)
        rec = pick.get("recommendation", "over")
        result = _settle_numeric_result(actual_value, line, rec)

        home_goals = matched.get("goals", {}).get("home", 0) or 0
        away_goals = matched.get("goals", {}).get("away", 0) or 0
        _venue = (pick.get("venue") or "home").lower()
        _player_goals = home_goals if _venue == "home" else away_goals
        _opp_goals    = away_goals if _venue == "home" else home_goals
        home_team_name = matched.get("teams", {}).get("home", {}).get("name", "") or ""
        away_team_name = matched.get("teams", {}).get("away", {}).get("name", "") or ""
        home_team_id   = matched.get("teams", {}).get("home", {}).get("id")
        away_team_id   = matched.get("teams", {}).get("away", {}).get("id")
        try:
            from routes.picks import _fetch_fixture_possession
            home_poss, away_poss = await _fetch_fixture_possession(fid, home_team_id, away_team_id)
        except Exception:
            home_poss, away_poss = None, None

        try:
            from game_script_engine import bucket_from_final_score
            _scen_bucket = bucket_from_final_score(home_goals, away_goals)
        except Exception:
            _scen_bucket = None
        _settle_set = {
            "status": "settled",
            "result": result,
            "actualValue": actual_value,
            "minutesPlayed": minutes_played,
            "matchScore": f"{_player_goals}-{_opp_goals}",
            "finalHomeGoals": home_goals,
            "finalAwayGoals": away_goals,
            "homeTeam": home_team_name,
            "awayTeam": away_team_name,
            "scenarioBucket": _scen_bucket,
            "settledAt": datetime.now(timezone.utc).isoformat(),
            "settledBy": "auto_soccer",
            "settlementSource": {
                "provider": "api-football",
                "fixtureId": fid,
                "playerId": player_id,
                "propType": prop_type,
                "statPath": f"statistics.{prop_type}",
                "fixtureStatus": _match_status,
                "verified": bool(_stored_fid),
                "verificationMethod": "fixture_id" if _stored_fid else "team_opponent_date_fallback",
                "recordedAt": datetime.now(timezone.utc).isoformat(),
            },
        }
        if home_poss is not None:
            _settle_set["homePoss"] = home_poss
        if away_poss is not None:
            _settle_set["awayPoss"] = away_poss
        _upd = await db.picks.update_one(
            {"pickId": pick["pickId"], "status": {"$ne": "settled"}},
            {"$set": _settle_set}
        )
        if _upd.modified_count == 0:
            print(f"[AUTO-SETTLE] {pick.get('playerName','')} already settled by another process — skipping duplicate settle write")
            return True
        try:
            from routes.push import _notify_pick_settled
            await _notify_pick_settled(pick, result)
        except Exception as _pe:
            print(f"[AUTO-SETTLE] push error: {_pe}")
        print(f"[AUTO-SETTLE] {pick.get('playerName','')} {prop_type} {line} → actual {actual_value} ({minutes_played}min) = {result}")
        return True
    except Exception as e:
        print(f"[AUTO-SETTLE] Error settling {pick.get('playerName','')}: {e}")
        return False


async def _settle_dnp_push(pick: dict, matched: dict, void_reason: str) -> bool:
    """Discard a pick when the player did not participate.

    Used when a finished fixture's player-stats response does not include the
    player (not in matchday squad), or when minutes < 30 and no stat evidence.
    """
    try:
        from routes.picks import _discard_dnp_pick
        await _discard_dnp_pick(pick, pick.get("email"), void_reason)
        print(f"[AUTO-SETTLE-DNP] {pick.get('playerName','')} discarded ({void_reason})")
        return True
    except Exception as e:
        print(f"[AUTO-SETTLE-DNP] Error settling {pick.get('playerName','')}: {e}")
        return False


# ── MLB Live Stat Tracking ────────────────────────────────────────────────────

_MLB_LIVE_PROP_TYPES = {
    "pitcher_strikeouts", "innings_pitched", "hits_allowed", "earned_runs",
    "walks_allowed", "pitches_thrown", "batters_faced",
    "hits", "home_runs", "rbi", "walks", "strikeouts", "runs",
    "total_bases", "stolen_bases", "doubles", "plate_appearances",
    "hitter_fantasy_points", "hits_runs_rbis",
    "pitcher_fantasy_score", "pitching_outs",
}


async def mlb_live_loop():
    """Background task: poll BDL every ~2 minutes for live/today MLB games
    and update currentValue on pending/live MLB picks so the pick card shows
    a live stat counter exactly like soccer shows live passes/shots."""
    await asyncio.sleep(20)  # Brief startup delay so the rest of the app is ready
    while True:
        try:
            await _update_mlb_live_picks()
        except Exception as e:
            print(f"[MLB LIVE] Loop error: {e}")
        await asyncio.sleep(180)  # 3-minute cadence — shared BDL key


async def _update_mlb_live_picks():
    """Core of the MLB live loop: find in-progress or today's games,
    fetch each player's current game stats, and write them to the picks collection."""
    try:
        import mlb_client
        from mlb_engine import ALL_PROP_FIELDS
    except ImportError as _ie:
        print(f"[MLB LIVE] Import error: {_ie}")
        return

    # Grab all live/pending MLB picks (detect by sport field OR prop type)
    live_picks = await db.picks.find(
        {"$or": [
            {"status": "live",    "sport": "mlb"},
            {"status": "pending", "sport": "mlb"},
            {"status": "live",    "propType": {"$in": list(_MLB_LIVE_PROP_TYPES)}},
            {"status": "pending", "propType": {"$in": list(_MLB_LIVE_PROP_TYPES)}},
        ]},
        {"_id": 0}
    ).to_list(200)

    if not live_picks:
        return

    # Always use the current calendar year for live game lookups — a pick saved
    # in "season 2025" won't find a game running in the 2026 season otherwise.
    current_year = datetime.now(timezone.utc).year

    # Group by team_id only (not season — we use current_year for all live queries)
    team_groups: dict = {}
    for pick in live_picks:
        tid = pick.get("teamId") or 0
        team_groups.setdefault(tid, []).append(pick)

    for team_id, picks in team_groups.items():
        if not team_id:
            continue
        try:
            # ── Resolve BDL team ID (picks store Stats API IDs; BDL uses 1-30) ─
            bdl_team_id = await mlb_client.get_bdl_team_id_for_statsapi(team_id, current_year)
            effective_team_id = bdl_team_id or team_id  # fallback to original if lookup fails
            if bdl_team_id and bdl_team_id != team_id:
                print(f"[MLB LIVE] Resolved BDL team id: statsapi={team_id} → bdl={bdl_team_id}")

            # ── Fetch today's game for this team ─────────────────────────────
            games = await mlb_client.get_today_and_live_games(effective_team_id, current_year)
            live_game   = next((g for g in games if "IN_PROGRESS" in (g.get("status") or "").upper()), None)
            today_game  = live_game or (games[0] if games else None)
            today_game_id = today_game.get("id") if today_game else None

            for pick in picks:
                player_id = pick.get("playerId")
                prop_type = (pick.get("propType") or "").lower()
                field     = ALL_PROP_FIELDS.get(prop_type)
                if not player_id or not field:
                    continue

                # ── Determine which game to use for this pick ─────────────────
                # CRITICAL: never overwrite a confirmed gameId with today's game.
                # Old picks had their gameId silently replaced each loop cycle,
                # meaning a 5-day-old pick would try to settle against today's
                # scheduled game — which has no stats — and loop forever.
                stored_game_id = pick.get("gameId")

                if stored_game_id and stored_game_id != today_game_id:
                    # Pick has stats from a PREVIOUS game — use that game's data.
                    # If it differs from today's it's already completed.
                    game_id     = stored_game_id
                    is_live     = False   # past game is never live
                    is_final    = True    # past game is always final
                    home_abbrev = pick.get("homeTeam", "")
                    away_abbrev = pick.get("awayTeam", "")
                    home_runs   = pick.get("finalHomeGoals")
                    away_runs   = pick.get("finalAwayGoals")
                elif today_game_id:
                    # Either no stored gameId, or stored matches today → use today's game
                    game_id     = today_game_id
                    status_str  = (today_game.get("status") or "").upper()
                    is_live     = "IN_PROGRESS" in status_str
                    is_final    = "FINAL"       in status_str
                    home_team   = today_game.get("home_team", {}) or {}
                    away_team   = today_game.get("away_team", {}) or {}
                    home_abbrev = home_team.get("abbreviation", "")
                    away_abbrev = away_team.get("abbreviation", "")
                    home_runs   = (today_game.get("home_team_data") or {}).get("runs")
                    away_runs   = (today_game.get("away_team_data") or {}).get("runs")
                else:
                    # No today game found for this team — skip
                    continue

                # ── Stale-final escape hatch ──────────────────────────────────
                # If matchStatus is already "final" AND the pick is >48h old with
                # no currentValue, stats are never coming — void as push so it
                # doesn't stay live indefinitely.
                if is_final and pick.get("currentValue") is None:
                    pick_ts = None
                    for _tf in ("timestamp", "createdAt"):
                        _raw = pick.get(_tf)
                        if _raw:
                            try:
                                pick_ts = datetime.fromisoformat(str(_raw).replace("Z", "+00:00"))
                                break
                            except Exception:
                                pass
                    if pick_ts:
                        age_h = (datetime.now(timezone.utc) - pick_ts).total_seconds() / 3600
                        if age_h > 48:
                            print(f"[MLB LIVE] Stale-final void: {pick.get('playerName')} "
                                  f"{prop_type} age={age_h:.0f}h game={game_id} — push")
                            await db.picks.update_one(
                                {"pickId": pick["pickId"]},
                                {"$set": {
                                    "result":      "push",
                                    "status":      "settled",
                                    "matchStatus": "final",
                                    "settledAt":   datetime.now(timezone.utc).isoformat(),
                                    "settledBy":   "mlb_stale_void",
                                }},
                            )
                            continue

                # Fetch current game stats — skip cache for live games so every
                # loop iteration gets the freshest values from BDL.
                current_value = None
                stats = None
                try:
                    from mlb_engine import _compute_fantasy_pts as _fp
                    stats = await mlb_client.get_game_player_stats(
                        int(player_id), int(game_id), current_year, live=is_live
                    )
                    if stats:
                        if prop_type == "hitter_fantasy_points":
                            current_value = _fp(stats)
                        elif prop_type == "hits_runs_rbis":
                            from mlb_engine import _compute_hits_runs_rbis as _hrr
                            current_value = _hrr(stats)
                        elif prop_type == "pitcher_fantasy_score":
                            from mlb_engine import _compute_pitcher_fantasy as _pf
                            current_value = _pf(stats)
                        elif prop_type == "pitching_outs":
                            from mlb_engine import _compute_pitching_outs as _po
                            current_value = _po(stats)
                        else:
                            raw = stats.get(field)
                            if raw is not None:
                                if prop_type == "innings_pitched":
                                    parts = str(raw).split(".")
                                    whole = int(parts[0])
                                    frac  = int(parts[1]) if len(parts) > 1 else 0
                                    current_value = round(whole + frac / 3.0, 1)
                                else:
                                    current_value = float(raw)
                except Exception as _se:
                    print(f"[MLB LIVE] Stats fetch failed player={player_id} game={game_id}: {_se}")
                    continue

                # Skip if no data at all and game hasn't started
                if current_value is None and not (is_live or is_final):
                    continue

                line = float(pick.get("line") or 0)
                rec  = (pick.get("recommendation") or "over").upper()
                match_status = "final" if is_final else ("live" if is_live else "scheduled")

                set_fields: dict = {"matchStatus": match_status}
                # Only write gameId/score fields when using TODAY's game (don't
                # overwrite historical data on picks with a prior game's gameId).
                if not stored_game_id or stored_game_id == today_game_id:
                    set_fields["gameId"] = game_id
                    set_fields["homeTeam"] = home_abbrev
                    set_fields["awayTeam"] = away_abbrev
                if home_runs is not None:
                    set_fields["finalHomeGoals"] = home_runs
                if away_runs is not None:
                    set_fields["finalAwayGoals"] = away_runs
                if current_value is not None:
                    set_fields["currentValue"] = current_value

                if is_final and current_value is not None:
                    line_f = line
                    # ── DNP guard: pitcher got 0 K/outs but also 0 IP ─────────
                    _PITCHER_COUNT_PROPS = {
                        "pitcher_strikeouts", "hits_allowed", "earned_runs",
                        "walks_allowed", "pitches_thrown", "batters_faced",
                        "pitcher_fantasy_score", "pitching_outs",
                    }
                    result_str: str
                    if prop_type in _PITCHER_COUNT_PROPS and current_value == 0.0 and stats:
                        ip_raw = stats.get("ip")
                        if ip_raw is not None:
                            try:
                                ip_parts = str(ip_raw).split(".")
                                ip_float = int(ip_parts[0]) + (int(ip_parts[1]) / 3.0 if len(ip_parts) > 1 else 0)
                                if ip_float == 0.0:
                                    result_str = "dnp"
                                    print(f"[MLB LIVE] DNP {pick.get('playerName')} {prop_type} IP=0")
                                else:
                                    result_str = _settle_numeric_result(current_value, line_f, rec)
                            except Exception:
                                result_str = _settle_numeric_result(current_value, line_f, rec)
                        else:
                            result_str = _settle_numeric_result(current_value, line_f, rec)
                    else:
                        result_str = _settle_numeric_result(current_value, line_f, rec)
                    if result_str == "dnp":
                        from routes.picks import _discard_dnp_pick
                        await _discard_dnp_pick(
                            pick,
                            pick.get("email"),
                            f"MLB pitcher DNP: IP=0 for {prop_type}",
                        )
                        print(f"[MLB LIVE] DNP discarded {pick.get('playerName')} {prop_type}")
                        continue
                    set_fields.update({
                        "actualValue": round(current_value, 1),
                        "result":      result_str,
                        "status":      "settled",
                        "settledAt":   datetime.now(timezone.utc).isoformat(),
                        "settledBy":   "mlb_live_loop",
                    })
                    print(f"[MLB LIVE] ✓ Settled {pick.get('playerName')} {prop_type} "
                          f"actual={current_value} line={line_f} rec={rec} → {result_str}")
                elif is_live:
                    set_fields["status"] = "live"
                    if current_value is not None:
                        print(f"[MLB LIVE] {pick.get('playerName')} {prop_type} = {current_value} (live)")

                await db.picks.update_one(
                    {"pickId": pick["pickId"]},
                    {"$set": set_fields}
                )

        except Exception as _te:
            print(f"[MLB LIVE] Team {team_id}/{current_year} error: {_te}")
        # Pace BDL calls between teams — shared API key across all sport clients
        await asyncio.sleep(1.5)
