---
name: AI venue-possession mismatch
description: When user-selected venue contradicts fixture reality, AI narrative inverts because prompt and possession data disagree.
---

## The Bug

When a user selects HOME for a player whose team is actually the AWAY side in the fixture (per API-Football), the AI synthesis prompt contains contradictory signals:
- Header says: `{player} ({team}) HOME vs {opponent}`
- `dom_context` says: `Expected possession for {team}: 35%`

The AI correctly infers the team must be away (35% possession = away team), and writes "As {player}'s team is away..." — contradicting the UI badge and possession bar.

## Root Cause

Two venue variables in `backend/routes/predict.py` can diverge:
1. `player_venue` = `req.venue.lower()` (user input)
2. `_is_home` = `match_odds.playerIsHome` (fixture reality)

`match_dominance` / `dom_context` uses `_is_home` for possession computation, but the AI prompt uses `player_venue` for the header and `player_venue` for venue-filtered game logs. When they mismatch, the AI receives a split-brain signal.

## The Fix

After `match_odds` is resolved and after neutral-venue normalization, override `player_venue` to match `match_odds.playerIsHome`:

```python
_pih_after_odds = match_odds.get("playerIsHome") if match_odds else None
if _pih_after_odds is not None:
    _fixture_venue = "home" if _pih_after_odds else "away"
    if player_venue != _fixture_venue:
        print(f"[VENUE ALIGN] user={player_venue} → fixture={_fixture_venue} ...")
        player_venue = _fixture_venue
```

This ensures a SINGLE source of truth (fixture-assigned venue) for:
- Game log venue filtering
- Possession computation
- AI prompt header and `dom_context`
- Situation engine inputs

## Why This Matters

The AI is explicitly instructed "MUST match the computed possession numbers above" in `dom_context`. If the header says HOME but possession is 35%, the AI has no choice but to reason the team is away. Aligning the venue before any downstream logic removes this contradiction.
