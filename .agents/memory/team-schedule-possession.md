---
name: Team-schedule possession context
description: Possession context shown beside opponent cohorts must come from independent completed team schedules, not player appearances or comparable-player rows.
---

Team possession evidence is a club-level fixture-history measure. Average the selected team's and opponent's verified fixture possession independently; a player's minutes, lineup status, or absence must not remove a match from either schedule sample. Keep player-match possession separate for player-specific evidence.

**Why:** Comparable-player cohorts require minutes and exact-position evidence, so deriving possession from those rows makes the displayed team context unstable and unintentionally player-linked.

**How to apply:** Add a cache-first team fixture-statistics path with explicit sample counts/source labels. Use separate team and opponent schedule samples in evidence responses and label the UI as team-schedule context.