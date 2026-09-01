"""Deterministic selection of owner-only player media.

``cache_players`` may contain one row per player/club/competition context.  A
player ID is the identity key, while the pick's team ID is the best context
key for choosing between stale cache rows.
"""


def select_player_photo(
    rows: list[dict],
    *,
    player_id: int | str | None,
    team_id: int | str | None = None,
) -> str:
    """Return the best photo for one verified player/context.

    Never select a row for another player.  Prefer the exact fixture team,
    then prefer the freshest row with a usable photo.  Empty rows are kept as
    a final fallback only so a background refresh can still be scheduled by
    the caller.
    """
    if player_id in (None, "", 0, "0"):
        return ""

    def _same_id(value, expected) -> bool:
        try:
            return int(value) == int(expected)
        except (TypeError, ValueError):
            return str(value or "") == str(expected or "")

    player_rows = [
        row for row in rows
        if _same_id(row.get("playerId"), player_id)
    ]
    if not player_rows:
        return ""

    def _freshness(row: dict) -> float:
        value = row.get("_cachedAt") or 0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _rank(row: dict) -> tuple[int, int, float]:
        has_team_context = (
            team_id not in (None, "", 0, "0")
            and _same_id(row.get("teamId"), team_id)
        )
        has_photo = bool((row.get("photo") or "").strip())
        # Exact team context dominates freshness; a usable photo dominates an
        # empty row within the same context.
        return (
            0 if has_team_context else 1,
            0 if has_photo else 1,
            -_freshness(row),
        )

    selected = min(player_rows, key=_rank)
    return (selected.get("photo") or "").strip()