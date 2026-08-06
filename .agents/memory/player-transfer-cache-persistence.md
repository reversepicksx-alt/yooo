---
name: Player transfer cache persistence
description: Transfer detection worked but never persisted to the player cache, so repeated lookups returned the old club
---

# Player transfer cache persistence and verification

## Problem

Users reported players like Lewandowski and Griezman still showing old clubs. The app already had a live team-refresh step in `routes/scan.py` that called `players?id={pid}&season={s}` and detected when the live team differed from the cached team. However, it only updated the in-memory `resolved_player` dict used for that one prediction. It never wrote the new club back to `db.players`, so the cache stayed stale for every subsequent lookup and pick.

## Fix

When the live season query returns a domestic-club team that differs from the cached `teamId`, persist the transfer immediately to the player cache:

- Update `db.players` with the new `teamId`, `teamName`, `leagueId`, `_dt`, and `_cachedAt`.
- Log the transfer so it is visible in logs (`[TEAM REFRESH] ...` + `[TEAM REFRESH-DB] ...`).
- The in-memory dict is also updated for the current prediction, so the immediate pick uses the correct club.

## Why

Detecting a transfer at predict-time is only useful if the detection is durable. Without writing to the cache, the same stale club is served to the next user, the next pick, and the live-tracking settlement. The 3-day squad sync (`SQUAD_TTL_SECONDS`) is too slow for mid-window transfers and does not help if the user is actively searching for a player. Persisting the transfer at the moment of detection makes the cache self-healing.

## How to apply

- Any code path that detects a club mismatch between a live API response and the local cache must also write the correction to the cache.
- Do not rely only on background squad syncs for transfer freshness; real-time lookups should self-correct.
- Keep a transfers audit trail (or at least structured logging) so users can verify why a club changed.

Cached club rows are not current-team evidence. Search may use them to identify a
player, but selection must verify the club from the current operational season
before displaying it or fetching a next match. If the provider is unavailable or
has not published the transfer, return an explicit unavailable state rather than
showing the old club. Explicit national-team contexts remain separate and may be
selected intentionally.

During offseason, a prior competition-season club row can still be valid: API-
Football may place club statistics under 2025 while national-team data appears
under 2026. Confirm that prior-season club through the provider's current
`players/squads` feed before promoting it to current-club status.

**Why:** A background refresh or cached profile can lag a transfer by days. The
old behavior turned that lag into a false Liverpool/old-club matchup and made the
wrong team look authoritative.

**How to apply:** Treat `teamVerified` as required for club predictions, keep
search team fields blank until verification, use current-squad confirmation when
season labels span calendar years, and re-check the player/team pair at
prediction time so stale clients cannot bypass the guard.
