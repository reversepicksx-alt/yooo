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

When a valid two-sided fixture-possession cache already exists under the general fixture key, the team-schedule packet must reuse it before requesting a team-specific cache key. Any in-memory matchup cache must also be bypassed or refreshed when new schedule rows are available, or a corrected evidence packet can still render the old odds-only result.

**Why:** A real Deportivo home sample was present in fixture-possession history, but the independent team key and cached matchup result made the response look like `0/N` until both layers were reconciled.

**How to apply:** Normalize every returned sample row with fixture ID, date, opponent, venue, value, and source; carry those rows through the matchup response and recalculate cached dominance when evidence rows exist.