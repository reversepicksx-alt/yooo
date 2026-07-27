---
name: Hierarchical calibration by league, position, and role
description: Confidence and prop safety now slice settled picks by league, position, and role with hierarchical shrinkage
---

# Hierarchical calibration by league, position, and role

## Problem

The global confidence calibration mixed picks from all leagues and positions. A Liga MX midfielder UNDER and a Premier League midfielder UNDER were treated as the same bucket. South American and Mexican leagues often have lower pass volume, so the global average was wrong for them. This caused clusters of misses in specific leagues and roles.

## Fix

`confidence_calibration.py` now builds hierarchical buckets:

1. `propType|DIRECTION|lineBand|leagueId|position|role`
2. `propType|DIRECTION|lineBand|leagueId|position`
3. `propType|DIRECTION|lineBand|leagueId`
4. `propType|DIRECTION|lineBand`
5. `propType|DIRECTION`

Lookup walks from most specific to least specific. A child bucket only needs 15-50 samples to fire (depending on depth), and the result is blended with the raw score via James-Stein shrinkage so thin buckets do not slam the confidence.

`prop_safety_cache.py` also builds league-aware and position-aware buckets and falls back to global when child buckets are too small.

The prediction flow passes `leagueId`, `position`, and `role` into both `calibrate()` and `get_prop_safety()`.

## Why

More dimensions means more accurate predictions, but it also makes each bucket thinner. Hierarchical shrinkage solves both: specific leagues/positions get their own signal when enough data exists, and rare situations borrow strength from the parent bucket instead of being ignored or overfit.

## How to apply

- When changing calibration, always pass all available dimensions (league, position, role) from the prediction flow.
- Keep the shrinkage constant high enough that a child bucket with only 15-50 samples cannot dominate the raw score.
- Persist `position` and `role` on every pick document so the calibration pipeline can bucket by them.
