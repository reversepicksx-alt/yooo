---
name: Player search exact-word cap
description: Fast single-word cache searches must not let a substring result cap hide exact standalone names.
---

Fast single-word player searches should merge a bounded exact-word cache query with the initial substring results before ranking. A first-page limit on substring matches can otherwise hide a standalone player name behind compound names sharing the token.

**Why:** Searching “Ronaldo” returned compound names first and omitted the standalone Bahia goalkeeper because the cache’s first 20 substring rows were ranked before exact-word rows were considered.

**How to apply:** Keep the interactive lookup bounded, query exact word boundaries separately, deduplicate by player/team/league context, then apply the existing ranking and quality filters. For surname-only searches, prefer durable identities with verified contexts over an unscoped provider profile page; same-name provider IDs can be unrelated and have no current club. Native clients also need a bounded verified-identity fallback when the production provider search lags a backend release, with punctuation stripped before matching.