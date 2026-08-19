---
name: Saved pick revisions
description: User-facing policy for preserving updated prediction saves.
---

Every deliberate Save Pick action creates a new saved snapshot, even when its player, opponent, prop, line, and fixture match a previous pick. Soft-hidden records from deletion never block a later save.

**Why:** Users need to preserve revised model output after data, analysis, or projection improvements. Requiring deletion first made a successful deletion look broken because the durable soft-hidden record still triggered duplicate detection.

**How to apply:** Keep client in-flight protection and same-`pickId` upserts for idempotent retries, but do not reject a new explicit snapshot based on prediction identity. Reporting and calibration must continue to deduplicate prediction events where they need one event per fixture.