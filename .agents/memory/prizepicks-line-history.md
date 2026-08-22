---
name: PrizePicks line history
description: Durable contract for JARVIS PrizePicks board snapshots and line movement.
---

JARVIS PrizePicks board refreshes must persist one stable record per event/player/stat market and return `previous_line`, `current_line`, `first_seen`, `last_seen`, `movement`, and bounded timestamped `line_history`.

**Why:** A single latest board row cannot distinguish a stable market from a chased line such as 49.5 → 56.5 → 60.5. JARVIS needs movement context before ranking or downgrading candidates.

**How to apply:** Treat SportsGameOdds as the market-reference source, preserve the exact provider market identity, use the PrizePicks bookmaker line for movement, and keep history observations bounded. Prediction math and settlement remain authoritative elsewhere.