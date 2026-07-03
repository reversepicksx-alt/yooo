---
name: Neutral venue elimination
description: Why "neutral" venue was removed as a product concept and how effective home/away is resolved for tournament fixtures.
---

Product decision: "neutral" venue is not a real state. Even at a neutral tournament site (World Cup, Copa America, etc.), one team always plays like the effective home side (bigger crowd support / bigger following) and the other like the away side. There is no genuine in-between.

**Why:** The engine had 30+ branches keyed off `_is_neutral` that either skipped home-field-advantage boosts entirely or blended both teams' stats into "overall" numbers instead of home/away splits — silently flattening real game-state signal (e.g. a Bayesian Game Script boost for a CB protecting a lead) for every neutral-venue fixture. The user (product owner) also pushed back on the premise itself: crowd support is never actually 50/50, so treating it as such throws away a real signal, not just an engine bug.

**How to apply:** The backend still *accepts* a legacy `venue="neutral"` request value (old clients may send it), but resolves it immediately to a definite `home`/`away` before any downstream logic runs, using this priority: (1) betting-market favorite as a proxy for "which team the world backs", (2) the fixture's own home/away designation, (3) a deterministic team-ID tiebreaker if neither signal exists. After resolution, `_is_neutral` is always `False` — the old `_is_neutral`-gated branches in `backend/routes/predict.py` are now dead code (not yet deleted, safe to remove in a follow-up cleanup pass). The mobile venue picker only offers HOME/AWAY, no NEUTRAL option.

One exception: club-log venue splitting for a player's international-tournament appearances must NOT use this national-team home/away resolution — a player's CLUB home/away logs have nothing to do with whether his NATIONAL team is the effective home side. That gate uses `league_id in INTERNATIONAL_LEAGUES` (from `config.py`) instead, independent of neutral-resolution.
