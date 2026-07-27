---
name: Soccer live tracking fallback
description: Why South American/Mexican soccer picks fail to track live and how to fix it permanently
---

# Soccer live tracking fallback

## Problem

The iOS app shows soccer picks as `LIVE` but never populates `currentValue` or live stats. The matches exist in API-Football, so the failure is in the lookup pipeline, not the data source.

Root causes:

1. `fixtures?live=all` returns only a handful of global fixtures. A South American match is unlikely to appear in that tiny list.
2. The date fallback used exact `date=today` with `CURRENT_SEASON=2025`. Many South American/Mexican leagues are in season `2026`, and kickoffs often cross UTC midnight, so the fixture lives on a different calendar date than the user's local "today".
3. The fallback did not persist a discovered `fixtureId`, so every poll repeated the same fuzzy search instead of using the bulletproof `fixtures?id=X` path.
4. Auto-settlement used a weaker name matcher than live tracking, so names like `S. Montiel` vs `E. Montiel` could fail to settle after the match.

## Fix

- Team and league lookups use a 3-day `from`/`to` window and try both `2025` and `2026` seasons in one call. This catches matches that are live, finished, or on a different UTC date.
- Pre-store `fixtureId` for any match discovered in the lookup window, not only `NS` fixtures. Once stored, subsequent calls use the direct `fixtures?id=X` path.
- Auto-settlement name matching uses the same robust rules as live tracking: full/substring, last-name (>=4 chars), and initial+last (e.g. `S. Montiel` matches `E. Montiel`).
- Add explicit logging (`[LIVE-MISS]`) when a pick still cannot be matched so future failures are diagnosable.

## Why

API-Football's live coverage is tiered by region and rate-limit budget. South American and Mexican fixtures are often in the "live=all" tier that is too small to be useful. The exact-date, exact-season lookup was too fragile for cross-timezone kickoffs and the South American season split. A stored fixture ID is the only reliable long-term anchor; fuzzy lookups are a fallback for one-time resolution, not the polling loop.

## How to apply

- Any future expansion to new leagues should use the stored-`fixtureId` path as the primary lookup and the 3-day window as the fallback.
- Never rely on `fixtures?live=all` as the primary source for live tracking; treat it as a last-resort cross-check.
- Keep player-name matching identical across live tracking, settlement, and pick-creation to avoid inconsistent matching.
