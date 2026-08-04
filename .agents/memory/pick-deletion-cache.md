---
name: Pick deletion cache
description: Pick deletion must remove all matching records and invalidate the per-user list cache.
---

Deleting a saved pick is a soft hide: every matching canonical `pickId` record for that user is marked hidden, never physically removed, and the cached picks list is invalidated. The UI must send the server-provided `pickId`, not fallback display or React key IDs.

**Why:** A stale in-memory list cache made successfully deleted history cards reappear, while physical deletion removed valuable calibration history and `delete_one` could leave duplicate legacy records behind.

**How to apply:** Keep soft-hide and list-cache invalidation coupled in the backend; treat zero matched records as an error and use only `pickId` in client mutations. Calibration queries must not filter `hiddenFromUser`.