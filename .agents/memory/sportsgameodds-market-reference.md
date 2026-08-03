---
name: SportsGameOdds market reference
description: How SportsGameOdds is used for multi-sport PrizePicks and Underdog market discovery.
---

SportsGameOdds is an optional multi-sport market-reference provider and lightweight discovery board for PrizePicks/Underdog lines. It must never become the source of truth for fixture identity, player game logs, projection math, or settlement; each Reverse Picks engine remains authoritative for those concerns.

**Why:** DFS market lines can be delayed, unavailable, historically stale, or unmatched even when the provider documents league coverage. Letting them drive settlement would create provider-integrity risk.

**How to apply:** The board may list currently available player markets across every provider sport. Only numeric Over/Under markets with an exact Reverse Picks prop mapping may open an analyzer; binary or unsupported markets stay visible as “Market only.” Tapping an analyzable card must resolve identity through the sport’s existing search flow before analysis. Fail closed when unavailable or unmatched, and never use provider data as model input or settlement truth.