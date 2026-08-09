---
name: Owner-only player media
description: Player photos and team crests are restricted to the authenticated product owner.
---

Player photos and team crests must be authorized server-side from the authenticated owner session, not merely hidden by frontend conditionals. Search and live analysis may receive media only for the owner; persisted prediction records remain media-free.

**Why:** These assets are an owner workflow enhancement, and client-only gating would expose the data to any subscriber who inspected the response or modified local state.

**How to apply:** Keep owner media fields additive and optional. Require the existing owner session for search enrichment and attach prediction media after persistence so the shared prediction document cannot leak owner-only fields.