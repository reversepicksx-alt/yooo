---
name: Squad sync name clobber
description: Squad re-sync delete+reinsert wipes enriched full player names with API abbreviated ones
---
Squad sync (`sync_team_squad` in backend/cache.py) does `delete_many({teamId})` + `insert_many` using API-Football squad display names, which are often abbreviated or first-name-only ("Jonathan" for "Jonathan de Jesus Alves"). This silently destroys enriched full names written by search/predict passes, breaking multi-word search.

**Why:** Multi-word search is an AND query on nameClean; once the stored name collapses to "Jonathan", "Jonathan Jesus" can never match, and the quality filter (correctly) drops surname-only partials — result: empty list even though the player is cached.

**How to apply:** Any writer that sets `name`/`nameClean` on db.players must first check the existing name and keep it when it has more words (guard exists in both the squads path and the background refresh path). Symptom to recognize: single-word search finds the player, two-word search returns [].

Also: dev workspace backend connects to the SAME Atlas cluster as production — a data fix in dev is instantly live in prod, but code fixes still require publishing.
