---
name: SportsGameOdds market reference
description: How SportsGameOdds is used for PrizePicks and Underdog soccer lines.
---

SportsGameOdds is an optional market-reference provider for soccer PrizePicks/Underdog lines and a lightweight discovery board. It must never become the source of truth for fixture identity, player game logs, projection math, or settlement; API-Football remains authoritative for those concerns.

**Why:** DFS market lines can be delayed, unavailable, historically stale, or unmatched even when the provider documents league coverage. Letting them drive settlement would create provider-integrity risk.

**How to apply:** The board may list only currently available player markets for discovery. Tapping a board card must hand the player back through API-Football identity/fixture resolution before analysis. Fail closed when the provider is unavailable or unmatched, and expose the result as clearly labeled context rather than model input.