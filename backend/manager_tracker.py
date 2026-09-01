"""
Manager Tracker — detects recent coaching changes, splits game logs at the tactical break,
and surfaces team possession drift as a structural-shift signal.

Public API
----------
  get_team_coach_info(team_id, db, api_fn) → dict
  detect_log_split(game_logs, coach_start_date_str) → (post_logs, pre_logs, post_n, pre_n)
  compute_possession_drift(team_fixture_stats) → dict
"""

import time
from datetime import datetime

_MANAGER_CACHE_TTL  = 7 * 24 * 3600   # 7 days — coach tenures rarely change faster
_RECENT_MANAGER_DAYS = 120             # flag as "recent" if < 120 days (≈ half a season)
_POSS_SHIFT_THRESHOLD = 8.0            # ≥8 pp swing in last-5 vs season = tactical identity shift


# ─────────────────────────────────────────────────────────────────────────────
# 1. Coach Detection
# ─────────────────────────────────────────────────────────────────────────────

async def get_team_coach_info(team_id: int, db, api_fn) -> dict:
    """
    Fetch and cache the coaching history for *team_id*.

    Returns
    -------
    {
        coachName:     str | None,
        coachStartDate: "YYYY-MM-DD" | None,
        prevCoachName:  str | None,
        daysElapsed:    int | None,
        isRecent:       bool,   # True when coach appointed < _RECENT_MANAGER_DAYS ago
        recentChange:   bool,   # alias for isRecent (convenience)
    }
    Empty dict on any failure.
    """
    if not team_id:
        return {}

    # ── Cache read ─────────────────────────────────────────────────────────
    try:
        cached = await db.manager_cache.find_one({"teamId": team_id}, {"_id": 0})
        if cached and (time.time() - cached.get("_ts", 0)) < _MANAGER_CACHE_TTL:
            return cached.get("data", {})
    except Exception:
        pass

    # ── API call ────────────────────────────────────────────────────────────
    try:
        # API-Football spells it "coachs" (intentional quirk of their API)
        data = await api_fn("coachs", {"team": team_id})
        if not data:
            return {}

        # Build a flat list of (coachName, startDate, endDate) stints for this team
        stints = []
        for coach_obj in (data if isinstance(data, list) else []):
            coach_name = coach_obj.get("name") or ""
            for stint in (coach_obj.get("career") or []):
                team_info = stint.get("team") or {}
                if isinstance(team_info, dict) and team_info.get("id") == team_id:
                    start_raw = stint.get("start") or ""
                    end_raw   = stint.get("end")        # None = currently in charge
                    stints.append({
                        "coachName": coach_name,
                        "start":     start_raw[:10] if start_raw else "",
                        "end":       end_raw[:10]   if end_raw   else None,
                    })

        if not stints:
            return {}

        # Sort by start date descending — most recent first
        stints.sort(key=lambda x: x["start"] or "", reverse=True)

        current  = stints[0]
        previous = stints[1] if len(stints) > 1 else None

        days_elapsed = None
        if current["start"]:
            try:
                start_dt     = datetime.strptime(current["start"], "%Y-%m-%d")
                days_elapsed = (datetime.utcnow() - start_dt).days
            except Exception:
                pass

        result = {
            "coachName":      current["coachName"],
            "coachStartDate": current["start"] or None,
            "prevCoachName":  previous["coachName"] if previous else None,
            "daysElapsed":    days_elapsed,
            "isRecent":       days_elapsed is not None and days_elapsed <= _RECENT_MANAGER_DAYS,
            "recentChange":   days_elapsed is not None and days_elapsed <= _RECENT_MANAGER_DAYS,
        }

        # ── Cache write ─────────────────────────────────────────────────────
        try:
            await db.manager_cache.update_one(
                {"teamId": team_id},
                {"$set": {"teamId": team_id, "data": result, "_ts": time.time()}},
                upsert=True,
            )
        except Exception:
            pass

        return result

    except Exception as exc:
        print(f"[MANAGER TRACKER] Error for teamId={team_id}: {exc}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Log Splitter
# ─────────────────────────────────────────────────────────────────────────────

def detect_log_split(game_logs: list, coach_start_date: str) -> tuple:
    """
    Split *game_logs* (newest-first, each with a "date" field "YYYY-MM-DD") at
    the coaching-change boundary.

    Returns
    -------
    (post_change_logs, pre_change_logs, post_n, pre_n)

    post_change_logs — games played ON OR AFTER coach_start_date (new system)
    pre_change_logs  — games played BEFORE coach_start_date (old system)
    """
    if not coach_start_date or not game_logs:
        return game_logs, [], len(game_logs), 0

    post, pre = [], []
    try:
        for g in game_logs:
            game_date = (g.get("date") or "")[:10]
            # ISO date string comparison is lexicographic and correct for YYYY-MM-DD
            if game_date and game_date >= coach_start_date:
                post.append(g)
            else:
                pre.append(g)
    except Exception as exc:
        print(f"[MANAGER TRACKER] detect_log_split error: {exc}")
        return game_logs, [], len(game_logs), 0

    return post, pre, len(post), len(pre)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Possession Drift
# ─────────────────────────────────────────────────────────────────────────────

def compute_possession_drift(team_fixture_stats: list) -> dict:
    """
    Compare the team's last-5-game average possession vs their season average.
    A drift of ≥ _POSS_SHIFT_THRESHOLD pp signals a tactical identity change.

    *team_fixture_stats* — list of per-fixture stat dicts, each optionally containing
    a "possession" field (numeric string or float, e.g. "58" or 58.0) and a "date"
    field for ordering.

    Returns
    -------
    {seasonAvg, last5Avg, drift, isShift, direction}
    or {} if insufficient data.
    """
    if not team_fixture_stats:
        return {}

    try:
        # Sort newest-first by date so last5 = most recent
        sorted_stats = sorted(
            team_fixture_stats,
            key=lambda s: (s.get("date") or s.get("fixture_date") or ""),
            reverse=True,
        )

        poss_vals = []
        for s in sorted_stats:
            p = s.get("possession")
            if p is not None:
                try:
                    poss_vals.append(float(str(p).replace("%", "").strip()))
                except (ValueError, TypeError):
                    pass

        # Need at least 6 entries: 5 for last-5 + 1 more so season != last5
        if len(poss_vals) < 6:
            return {}

        season_avg = round(sum(poss_vals) / len(poss_vals), 1)
        last5_vals = poss_vals[:5]
        last5_avg  = round(sum(last5_vals) / len(last5_vals), 1)
        drift      = round(last5_avg - season_avg, 1)
        is_shift   = abs(drift) >= _POSS_SHIFT_THRESHOLD

        return {
            "seasonAvg": season_avg,
            "last5Avg":  last5_avg,
            "drift":     drift,
            "isShift":   is_shift,
            "direction": "up" if drift > 0 else "down",
            "sampleSize": len(poss_vals),
        }
    except Exception as exc:
        print(f"[MANAGER TRACKER] compute_possession_drift error: {exc}")
        return {}
