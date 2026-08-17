---
name: Team-schedule possession context
description: Possession context shown beside opponent cohorts must come from independent completed team schedules, not player appearances or comparable-player rows.
---

Team possession evidence is a club-level fixture-history measure. Average the selected team's and opponent's verified fixture possession independently; a player's minutes, lineup status, or absence must not remove a match from either schedule sample. Keep player-match possession separate for player-specific evidence.

**Why:** Comparable-player cohorts require minutes and exact-position evidence, so deriving possession from those rows makes the displayed team context unstable and unintentionally player-linked.

**How to apply:** Add a cache-first team fixture-statistics path with explicit sample counts/source labels. Use separate team and opponent schedule samples in evidence responses and label the UI as team-schedule context.

Exact-position cohorts can remain limited even after expanding multiple seasons when provider lineup histories are sparse. Keep the limited label and disclose lineup-grid versus grounded-profile counts rather than relaxing venue or generic-position rules to reach a target sample.

The ten-match gate is an end-to-end response contract, not only a calculation guard: both schedule packets must be verified before deterministic possession-sensitive math runs, while odds-only values remain visible as estimates.

**Why:** A late response-assembly diagnostic path once referenced a stale sample variable and turned an otherwise valid prediction into a retryable 500; pure helper/source tests did not exercise that path.

**How to apply:** Validate at least one full prediction response after changing the possession packet, and keep sample counts, status, provenance, and projection eligibility distinct in both backend and UI.