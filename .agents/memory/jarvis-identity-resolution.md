---
name: JARVIS identity resolution
description: Owner conversational prediction requests must resolve identities through contextual team, fixture, and squad fallbacks.
---

The JARVIS resolver must treat market-board data as optional and resolve ambiguous player requests through a verified identity graph: opponent/team resolver → exact fixture → home/away orientation → team squad. User-supplied lines remain valid prediction inputs.

**Why:** API-Football can reject bare player searches, return irrelevant acronym matches, and omit future-fixture player rows even when the required player and squad data exists.

**How to apply:** Prefer the project’s alias-aware team resolver, ignore ambiguous raw acronym results, preserve normalized fixture identities, and use squad data for upcoming matches before returning UNKNOWN.