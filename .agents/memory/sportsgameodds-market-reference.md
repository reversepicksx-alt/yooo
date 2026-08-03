---
name: SportsGameOdds market reference
description: How SportsGameOdds is used for PrizePicks and Underdog soccer lines.
---

SportsGameOdds is an optional market-reference provider for soccer PrizePicks/Underdog lines. It must never become the source of truth for fixture identity, player game logs, projection math, or settlement; API-Football remains authoritative for those concerns.

**Why:** DFS market lines can be delayed, unavailable, historically stale, or unmatched even when the provider documents league coverage. Letting them drive settlement would create provider-integrity risk.

**How to apply:** Match the provider event and player only after the verified API-Football fixture is known. Fail closed when the provider is unavailable or unmatched, and expose the result as clearly labeled context rather than model input.