---
name: Pick deletion cache
description: Pick deletion must remove all matching records and invalidate the per-user list cache.
---

Deleting a saved pick is not complete until every matching canonical `pickId` record for that user is removed and the cached picks list is invalidated. The UI must send the server-provided `pickId`, not fallback display or React key IDs.

**Why:** A stale in-memory list cache made successfully deleted history cards reappear, while `delete_one` could leave duplicate legacy records behind.

**How to apply:** Keep delete and list-cache invalidation coupled in the backend; treat zero deleted records as an error and use only `pickId` in client mutations.