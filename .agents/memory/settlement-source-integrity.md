---
name: Soccer settlement source integrity
description: Rules for mapping provider soccer stats, trusting final values, and repairing legacy settlements
---

# Soccer settlement source integrity

Provider stat names are not interchangeable: API-Football `statistics.passes.total` is pass attempts, while `passes.accurate` is completed passes. Every settled soccer value must retain its provider, exact fixture, player, stat path, fixture status, and verification state.

**Why:** A legacy settlement used 79 for Andy Najar's pass-attempt prop even though the official fixture row showed 57 total attempts and 52 accurate passes. Treating an unverified numeric value as final also contaminated outcome history and calibration.

**How to apply:** Require explicit result plus verified provenance before showing `FINAL` or using a record for calibration. Keep live `currentValue` separate. `pending_review` is a transient automatic-retry state: prioritize it for exact-fixture/player refetch on the next picks load, promote a verified result immediately, and exclude it from settled totals while the retry is unresolved. Route suspicious positive legacy values through a dry-run-first exact-fixture/player refetch, and preserve the prior value/result/source in an audit record before writing a verified replacement.

Active picks that remain `live` after a fixture finishes need the same exact-fixture fallback as review records; otherwise a missed live-to-final transition can strand them indefinitely.

**Why:** A finished API-Football fixture can be available and fully verifiable even when the initial live lookup missed the UTC-boundary transition.

**How to apply:** Include own active soccer picks in the bounded final-refresh queue, route them through the API-Football-only repair path, and persist the verified fixture ID with the settlement.

Final API-Football player totals can be revised after the first FT response; a
`verified=true` marker proves exact fixture/player provenance, not that the
provider snapshot is permanently final.

**Why:** Post-match passes for two exact-fixture picks increased after their
initial settlements, changing both results from HIT to MISS.

**How to apply:** Recheck recent settled soccer rows for a bounded window,
preserve the prior value/result/source in a correction audit, and keep
unverified or quota-deferred rows out of calibration until the replacement is
verified.