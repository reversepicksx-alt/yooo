---
name: Player identity and context
description: Verified player IDs must remain authoritative for media, saved history, and profile aggregation when display names collide.
---

Use the verified player ID as the primary identity key. Use the fixture team ID as the context key when selecting among cache rows for photos or club metadata. Display-name matching is only a legacy fallback and must not merge multiple identified players with the same short name.

**Why:** API-Football and squad caches can contain several distinct players with the same display name, while one player can also have multiple club or competition context rows. Arbitrary first-row selection caused the wrong Reinaldo photo/profile to appear even though projection and settlement were correct.

**How to apply:** Preserve playerId and teamId through search, prediction, saved-pick mapping, owner media enrichment, and profile/stat lookups. Normalize numeric/string IDs at boundaries and keep identity changes separate from model math and settlement.