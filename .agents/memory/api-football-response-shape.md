---
name: API-Football response shape
description: The response contract used by the shared API-Football helper and compatible test doubles.
---

The shared API-Football helper returns the provider's `response` array directly, not the raw envelope containing a `response` key. Some older mocks and tests still provide the envelope shape.

**Why:** Code that called `.get("response")` on the helper result silently treated valid lineup and fixture data as missing. That can change confirmed-starter handling, expected minutes, and downstream projections.

**How to apply:** Normalize both list-shaped helper output and envelope-shaped test doubles at the boundary before reading lineup, fixture, or player rows. Never assume an empty list means the provider had no data until the shape is normalized.