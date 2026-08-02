---
name: Soccer settlement source integrity
description: Rules for mapping provider soccer stats, trusting final values, and repairing legacy settlements
---

# Soccer settlement source integrity

Provider stat names are not interchangeable: API-Football `statistics.passes.total` is pass attempts, while `passes.accurate` is completed passes. Every settled soccer value must retain its provider, exact fixture, player, stat path, fixture status, and verification state.

**Why:** A legacy settlement used 79 for Andy Najar's pass-attempt prop even though the official fixture row showed 57 total attempts and 52 accurate passes. Treating an unverified numeric value as final also contaminated outcome history and calibration.

**How to apply:** Require explicit result plus verified provenance before showing `FINAL` or using a record for calibration. Keep live `currentValue` separate. Route suspicious positive legacy values through a dry-run-first exact-fixture/player refetch, and preserve the prior value/result/source in an audit record before writing a verified replacement.