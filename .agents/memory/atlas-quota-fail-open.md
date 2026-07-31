---
name: Atlas quota fail-open
description: Temporary resilience rule for prediction responses when MongoDB Atlas blocks writes at the storage limit.
---

The final prediction persistence write is non-critical to serving a computed prediction. If Atlas rejects that write because the cluster is over its storage quota, log the persistence failure and return the computed result; normal persistence should resume automatically once storage is available.

**Why:** Atlas can hard-block all writes at the free-tier storage ceiling, turning successful prediction calculations into user-visible HTTP 500 errors.

**How to apply:** Keep the fail-open guard narrowly scoped around prediction analytics persistence. Do not delete production data automatically, and separately clean up or upgrade the Atlas cluster before relying on stored analytics again.