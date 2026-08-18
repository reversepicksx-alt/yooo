---
name: Retired integration secrets
description: Cleanup boundary for integrations that are no longer used by the application.
---

Removing an integration's client, endpoints, tests, configuration reads, and documentation is separate from deleting its secret-store entry. A retired secret can remain present without being active in the application.

**Why:** Secret-management tooling may expose secret presence but not permit an agent to delete the credential, and deleting a secret is destructive while historical records may still reference the integration.

**How to apply:** Search the repository for runtime reads and configuration flags, verify the application starts without them, preserve historical records unless deletion is explicitly requested, and tell the owner when a remaining secret must be removed through the workspace's secret-management UI.