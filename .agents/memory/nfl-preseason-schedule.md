---
name: NFL preseason schedule fallback
description: BallDontLie NFL schedules can omit current preseason fixtures, so upcoming-game discovery needs a separate schedule fallback.
---

BallDontLie remains authoritative for NFL player identity, game logs, prediction inputs, and settlement, but its games feed may expose only regular-season fixtures during the preseason. Use a schedule-only fallback for upcoming fixture discovery, preserve exact team matching, and choose the earliest verified event across providers.

**Why:** Skipping an available preseason game silently autofills a later regular-season matchup and makes the analyzer use the wrong opponent and venue.

**How to apply:** Keep fallback schedule data clearly marked as non-statistical fixture context; never use it to fabricate player stats or settle a pick. Cache the schedule briefly and fail closed when the provider is unavailable.