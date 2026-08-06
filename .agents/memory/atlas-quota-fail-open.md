---
name: Atlas quota fail-open
description: Temporary resilience rule for prediction responses when MongoDB Atlas blocks writes at the storage limit.
---

The final prediction persistence write is non-critical to serving a computed prediction. If Atlas rejects that write because the cluster is over its storage quota, log the persistence failure and return the computed result; normal persistence should resume automatically once storage is available.

**Why:** Atlas can hard-block all writes at the free-tier storage ceiling, turning successful prediction calculations into user-visible HTTP 500 errors.

**How to apply:** Keep the fail-open guard narrowly scoped around prediction analytics persistence. Do not delete production data automatically, and separately clean up or upgrade the Atlas cluster before relying on stored analytics again.

The same Atlas write block affects support-critical features such as direct-message sends and read receipts. Those routes must return an explicit storage-full response for sends and treat read receipts as non-critical, while the database is repaired.

**Why:** A successful read from `direct_messages` can create the misleading impression that customer replies are working even though the subsequent insert/update is rejected at the storage ceiling.

**How to apply:** Never substitute ephemeral local files for production customer messages; make the failure visible and free space or increase the cluster tier before relying on messaging persistence.

For storage maintenance, only remove rebuildable caches or bounded owner-diagnostic history after verifying the live code does not depend on the data. Never purge picks, users, sessions, subscriptions, payments, settlement evidence, or analytics used for recovery.

**Why:** The Atlas free-tier ceiling can be relieved safely without compromising the product, but broad cleanup can destroy subscriber history or payment access.

**How to apply:** Prefer retention/TTL guards on cache collections and audit feeds; inspect collection usage and field timestamps before any deletion, and require explicit confirmation for irreversible cleanup.