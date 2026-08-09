---
name: Season-boundary evidence handling
description: How to handle new-season fixtures when historical provider rows are mixed with incomplete cache data.
---

New-season readiness has two separate concerns: identifying the upcoming fixture under the new competition season and collecting verified historical evidence from the completed prior season. A partial or stale historical row must be excluded, not allowed to reject the entire verified sample and not filled with an estimate.

**Why:** On August 9, 2026, a new Community Shield fixture was found correctly while one or more cached/direct historical rows lacked exact possession, causing a misleading “season not ready” validation error.

**How to apply:** Keep season lookup newest-first and preserve exact fixture provenance. Filter soccer history to rows with exact minutes and both fetched team/opponent possession values; only return an unavailable error when no verified history remains.