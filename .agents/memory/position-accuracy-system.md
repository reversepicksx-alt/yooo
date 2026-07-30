---
name: Position accuracy system
description: 5 defects fixed in position resolution — version bump, lookup preference, re-squeeze, prompt upgrade, admin endpoint.
---

## The 5 Defects and Fixes

**1. POSITION_PROMPT_VERSION (config.py)**
Increment this number whenever position resolution logic or prompt changes. Any cache entry with `promptVersion < POSITION_PROMPT_VERSION` is treated as stale and re-resolved on next predict call. Currently v4.

**Why:** Stale entries (e.g. Vitinha=CB from a batch call) never got invalidated. Bumping the version is the correct eviction mechanism.

**2. Early lookup prefers playerId-keyed entries (predict.py ~line 2492)**
Changed from `$or [playerId, playerName]` to: try `{playerId: X}` first, then `{playerName: X, playerId: {$exists: true}}` as fallback.

**Why:** `grok_positions.py` batch resolver stores entries by playerName only (no playerId). These polluted the early lookup with wrong positions. The stats-aware full resolver in predict.py always stores by playerId → it should be preferred.

**3. Position-corrected baseline re-squeeze (predict.py after line ~3686)**
After full AI position resolution (`specific_position`), if it differs from `_bayes_position` (early cache lookup), re-run `get_positional_baseline` + `apply_positional_squeeze` with the correct position. ALWAYS apply the result (even when squeeze doesn't fire) — restores posteriorMean that was incorrectly squeezed by the wrong position's ceiling.

**Why:** The first baseline runs at line ~2866 with `_bayes_position` (early cache, may be wrong). Full AI resolution runs at line ~3490 — too late for the first baseline. The re-squeeze block corrects the current prediction, not just the next one.

**Key bug in re-squeeze:** Must update `early_bayes["posteriorMean"]` unconditionally (not just `if _pos_note2:`). When no squeeze fires for the correct position, the wrong-position squeeze result must still be reversed.

**4. grok_positions.py prompt upgraded**
`_grok_resolve_batch` now uses full role vocabulary matching `_role_variant()` keywords: Deep-Lying Playmaker, Ball Winner, Anchor, Box-to-Box, Mezzala, Inverted Winger, Traditional Winger, Inside Forward, Poacher, etc. Also adds `promptVersion` and `updatedAt` to stored entries.

**Why:** The old prompt just said "short tactical role" → model returned generic strings that didn't match any `_role_variant` keyword → all positions fell to "standard" variant.

**5. Admin endpoint POST /api/admin/positions/clear-player**
Accepts `{email, token, playerName?, playerId?}`. Deletes entries matching either field. On next predict the stats-aware AI resolver re-resolves with current stats evidence.

**Why:** Existing `/positions/invalidate` sets `promptVersion=0` but can't target playerName-only entries. Clear-player endpoint handles both keying schemes.

## Key evidence that the fixes work
- Vitinha: CDM/Deep-Lying Playmaker (p50=89.7) vs old wrong W/standard (p50=38)
- CDM league calibration: n=121 hits vs old LW: n=22 — far better sample
- Robertson: LB/Wing-Back (correct from cache hit v4)

## Category safety boundary
The player's API-Football generic category is a hard safety boundary for cached and fallback resolution. A player marked Defender must never inherit an ST/Poacher cache entry; when no trustworthy specific defender position is available, use the conservative CB/Stopper fallback rather than an attacking role.

**Why:** Jonathan de Jesus Alves was correctly identified by the player cache as a Defender, but a versioned AI cache entry incorrectly labeled him ST/Poacher. With Gemini disabled, that stale entry could not safely self-correct.

**How to apply:** Validate cached specific positions against the generic category before returning them from role resolution or using them in prediction math. Keep known corrections keyed by playerId.

## How prediction cache interacts with position
The prediction cache (`soc|{playerId}|{prop}|{line}|{opp}|{date}`) stores only the Grok AI synthesis text for reuse. The Bayesian math reruns fresh on every request — so even a "cache hit" still uses the correct (fresh) position for the quantitative projection.
