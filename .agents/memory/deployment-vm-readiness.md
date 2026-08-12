---
name: Deployment VM readiness failures
description: How to distinguish a Replit VM promote timeout from an application build failure.
---

When a VM publish log reaches image push/security scan and then stops at “Waiting for deployment to be ready” with no traceback, the build succeeded and the failure is in VM readiness/provisioning. Confirm the production proxy returns HTTP 200 on `/` and that the backend reaches Uvicorn startup locally before changing application code.

**Why:** A failed VM can leave the previous successful build live, and an infrastructure timeout can look like a code regression even when the same run command published successfully immediately beforehand.

**How to apply:** Inspect the failed build record and compare it with the last successful build. If both build cleanly and local production startup is healthy, retry publishing; only investigate code/config changes when a repeated attempt produces a concrete startup, port, dependency, or environment error.