---
name: Owner API status indicator
description: Provider health is exposed only as a compact owner account-header indicator.
---

Operational API/quota details should not appear in regular-user UI. The owner account may show a minimal `API` label with a green dot for available quota or a red dot when the daily breaker is active; detailed diagnostics and reset controls are intentionally removed.

**Why:** Provider internals are owner operational information, not subscriber product information, and the full diagnostics card added clutter without helping regular users.

**How to apply:** Keep the status request owner-authenticated and render both the request and indicator behind the owner access check. Use a neutral loading state until the owner-only status response arrives.