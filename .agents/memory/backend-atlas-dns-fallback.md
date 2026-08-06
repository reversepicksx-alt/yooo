---
name: Backend Atlas DNS fallback
description: Startup behavior when the workspace cannot resolve the Atlas MongoDB SRV record.
---

If the workspace backend dies during import with a PyMongo `mongodb+srv` SRV/DNS resolution error, provider-backed routes such as player search are unavailable even though the frontend loads. The backend can start against the workflow's local MongoDB fallback so external provider searches remain usable while Atlas DNS is unavailable.

**Why:** A dead backend presents in the app as empty search results, which looks like a broken search implementation even when the provider endpoints and response mapping are correct.

**How to apply:** Check backend workflow logs and port 8000 before changing search UI code. Treat Atlas quota/write errors separately from DNS startup errors; provider search should remain fail-open when persistence is unavailable.