---
name: JARVIS identity resolution
description: Owner conversational prediction requests must resolve identities through contextual team, fixture, and squad fallbacks.
---

The JARVIS resolver must treat market-board data as optional and resolve ambiguous player requests through a verified identity graph: opponent/team resolver → exact fixture → home/away orientation → team squad. User-supplied lines remain valid prediction inputs.

**Why:** API-Football can reject bare player searches, return irrelevant acronym matches, and omit future-fixture player rows even when the required player and squad data exists.

**How to apply:** Prefer the project’s alias-aware team resolver, ignore ambiguous raw acronym results, preserve normalized fixture identities, and use squad data for upcoming matches before returning UNKNOWN.

Conversational JARVIS turns must hydrate a user/session-scoped canonical state before parsing the next request; explicit new values override prior verified values, while omitted fields remain available for follow-up audits.

**Why:** A full-audit follow-up such as “Full audit Rongier under 52.5” intentionally omits the prop, venue, and opponent because they were established in the preceding turn.

**How to apply:** Persist only verified or explicitly user-supplied fields, never let UNKNOWN overwrite them, and keep state isolated by authenticated owner plus conversation ID.