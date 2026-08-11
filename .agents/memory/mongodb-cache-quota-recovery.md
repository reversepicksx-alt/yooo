---
name: MongoDB cache quota recovery
description: Atlas storage quota recovery for this app's disposable caches and durable data.
---

When Atlas reaches its storage ceiling, deleting old cache documents may not release allocated space quickly enough. The reliable emergency recovery is to drop only regenerable cache collections and retry the required write. Never drop saved picks, users, sessions, messages, subscriptions, or payment collections.

**Why:** A full-cache cleanup removed zero records because the cache rows were still inside their retention windows, while Atlas continued to reject writes. Dropping disposable cache collections released the allocated storage.

**How to apply:** On a quota error during a required persistence operation, run the cache-only recovery, then retry once. Rebuilt caches may make the next prediction/search slower, but durable subscriber data must remain intact.