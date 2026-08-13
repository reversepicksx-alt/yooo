---
name: Owner-only player media
description: Player photos and team crests are restricted to the authenticated product owner.
---

Player photos and team crests must be authorized server-side from the authenticated owner session, not merely hidden by frontend conditionals. Search and live analysis may receive media only for the owner; persisted prediction records remain media-free. Every response boundary—including fast cached snapshots, first-load/background-refresh snapshots, compact card renderers, and client-side response mappers—must preserve the additive owner-media fields.

**Why:** These assets are an owner workflow enhancement, and client-only gating would expose the data to any subscriber who inspected the response or modified local state. A media-safe persistence design can still look broken if an early-return path or mapper silently drops the authorized fields.

**How to apply:** Keep owner media fields additive and optional. Require the existing owner session for search enrichment and attach prediction media after persistence so the shared prediction document cannot leak owner-only fields. When adding a new cached/aggregated endpoint, trace both server enrichment and the mobile normalization layer before declaring media support complete.