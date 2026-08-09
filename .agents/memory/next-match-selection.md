---
name: Next-match fixture selection
description: Rules for choosing a soccer player's active or next matchup when API responses, caches, and quota failures are unreliable.
---

Active matchup identity must come from one verified fixture: a currently live fixture takes precedence, otherwise choose the earliest strictly future kickoff. Finished, postponed, cancelled, abandoned, and past/unknown-status fixtures are historical context only, never an opponent or venue fallback.

**Why:** API-Football responses are not guaranteed to be chronologically ordered, today's list can contain completed games, and quota exhaustion previously caused the next-match endpoint to return an older cached opponent. That can make the prediction mathematically coherent but about the wrong game.

**How to apply:** Centralize fixture eligibility and ordering. Validate cached fixture status and kickoff before reuse, use priority only to bypass the local background soft budget (not the provider's real quota breaker), and return an unresolved state or retry error when no active/upcoming fixture can be verified. Keep historical fixtures limited to league metadata.

Cached next-match records must also contain canonical `homeTeam` and `awayTeam` identity; invalidate older successful cache shapes instead of reconstructing fixture sides from effective venue.

**Why:** A valid cached fixture without canonical sides forced the client to infer home/away labels from player venue, which can be a separate effective-market concept and produced stale or inverted matchup displays.

**How to apply:** Any response-shape expansion for fixture identity needs a cache-shape compatibility check and one-time re-fetch before returning the cached result.