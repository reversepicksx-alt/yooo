---
name: Position accuracy system
description: 5 defects fixed in position resolution — version bump, lookup preference, re-squeeze, prompt upgrade, admin endpoint.
---

## Position resolution rules

The resolver now keeps provider categories broad when exact evidence is unavailable:
`Midfielder` remains `Midfielder` with no tactical role, rather than becoming
`CM · Box-to-Box`. Exact positions require grounded/manual evidence or verified
fixture/history observations. Tactical roles inferred from statistics are
explanatory metadata only and cannot admit comparison rows.

**Why:** Generic M/MID rows were admitting wingers, attacking midfielders, and
unrelated midfielders into Rodri-like comparison cohorts, while the old default
silently presented a guessed Box-to-Box role as fact.

**How to apply:** Use exact observed positions for cohort matching. Show broad
categories or an unavailable state when exact evidence is missing, and label
inferred roles with their provenance.

## The 5 Earlier Defects and Fixes

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
The player's API-Football generic category is a hard safety boundary for cached and fallback resolution. A player marked Defender must never inherit an ST/Poacher cache entry; when no trustworthy specific defender position is available, retain the broad Defender category with no guessed exact position or role.

**Why:** Jonathan de Jesus Alves was correctly identified by the player cache as a Defender, but a versioned AI cache entry incorrectly labeled him ST/Poacher. With Gemini disabled, that stale entry could not safely self-correct.

**How to apply:** Validate cached specific positions against the generic category before returning them from role resolution or using them in prediction math. Keep known corrections keyed by playerId. Generic fallback values must not be persisted as `specificPosition`.

## Lineup-grid midfield evidence

Verified or predicted API-Football lineup grids may provide exact midfield evidence
when the formation makes the tactical band unambiguous. For example, row 3 in a
4-3-3 is `CM`, row 3/4 in a 4-2-3-1 are `CDM`/`CAM`, row 3/4 in a 4-1-4-1
are `CDM`/`CM`, and 3-1-4-2 is `CDM` followed by `LM/CM/CM/RM`.
Ambiguous rows still remain `MID`.

**Why:** A generic `MID` row left valid exact-position cohorts empty for players
such as Rodri, while promoting every midfielder would recreate the original
false-role problem.

**How to apply:** Use formation-aware grid evidence only for the listed
unambiguous bands, preserve provider-category fallbacks elsewhere, and continue
to admit comparison rows by exact position rather than inferred tactical role.
Formation screenshots can validate the mapping, but identity still requires the
backend's player ID/name/grid join; a formation label alone must not assign a
position to the requested player.

## API-Sports lineup identity joins

Normalize provider player IDs before joining lineup, player-stat, and request
payloads; API-Sports IDs can arrive as strings in one response and integers in
another. A strict type-sensitive join can leave the pitch diagram correct while
the analysis layer loses the target's exact grid position.

**Why:** The rendered projected XI and the exact-position resolver consume
separate API-Sports payloads. When `"44"` was compared directly with `44`, the
target marker disappeared from the resolver even though the player was visibly
on the pitch.

**How to apply:** Use one normalized provider-ID helper for lineup status,
projected-XI target marking, historical comparison lineup maps, and any future
cross-response player joins.

## Durable grounded player profiles

Grounded/manual player-ID position profiles are durable identity evidence, not a
seven-day prediction cache. If the bounded Gemini resolver times out, use the
already-read trusted profile directly and never overwrite it with an empty
provider-category fallback.

**Why:** Position resolution is optional enrichment, but same-position
comparison admission depends on it. A Gemini timeout otherwise turns a known
player into generic `MID` and removes all similar-player rows.

**How to apply:** Fixture grid/confirmed observation remains highest priority;
trusted player-ID profile is the fallback; generic API-Sports categories remain
broad and cannot authorize exact-position cohorts.

## Identity-keyed exact fallback

When a provider exposes only a broad category and grounded lookup times out, an authoritative exact profile may be used only when keyed to the verified provider player ID and labeled `manual_override`. It must include visible provenance; it is not a lineup observation.

**Why:** Facundo Torres' current fixture responses had no usable lineup grid, but authoritative club/profile sources identify him as a right winger. Treating him as generic Attacker left the position evidence empty and removed valid exact-position context.

**How to apply:** Keep fixture/history observations and trusted cached profiles higher priority. Use an identity-keyed manual profile only for an explicitly verified player, and never let calibration or tactical inference relabel the identity.

## How prediction cache interacts with position
The prediction cache (`soc|{playerId}|{prop}|{line}|{opp}|{date}`) stores only the Grok AI synthesis text for reuse. The Bayesian math reruns fresh on every request — so even a "cache hit" still uses the correct (fresh) position for the quantitative projection.

## Selection-time grounded role precedence

When the mobile selector has a trusted grounded/manual exact position, carry that
identity and its evidence into the prediction request. A predicted lineup or a
confirmed lineup that reports only broad `DEF`/`MID`/`FWD` evidence cannot replace
it; only a confirmed exact current position may win.

**Why:** Provider lineups can be predicted or category-only. Letting those
observations replace a grounded winger/midfielder identity made the final card
show an invented role such as `Complete Forward` and caused the tactical
explanation to use the wrong mechanism.

**How to apply:** Keep exact position/role/source/confidence/evidence together
through `player`, `positionEvidence`, `tacticalContext`, and saved analysis.
Treat broad categories as supplementary evidence and keep Understat coverage
explicitly unavailable when the competition is not supported.

## Inferred fixture-history fallback at selection

When grounded resolution is unavailable, an exact positive-minutes fixture-history
profile may be surfaced immediately as an explicitly inferred selection role,
even when it crosses the search provider's broad category (for example,
Midfielder → ST). That inferred role must not act as a manual override: a
confirmed exact current lineup still wins.

**Why:** Mario Soriano's durable fixture evidence said ST while the provider
search category said Midfielder. Returning the broad category at selection
made the first role card disagree with the prediction, while treating the
inferred role as manual could hide a confirmed current role change.

**How to apply:** Return the exact history position with its inferred source and
evidence, carry it through the request, and keep the override guard provenance-aware.
Never silently promote this fallback to grounded/manual identity or quantitative
projection input.
