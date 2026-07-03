---
name: Dev workspace mongod is not production data
description: Clarifies that this workspace's local mongod is empty/ephemeral and real user, subscription, and pick data must be queried from the actual data source (deployed environment or third-party APIs), not assumed to be here.
---

## What was assumed vs. what's true

An earlier memory note claimed "Backend uses Atlas, not local mongod" — that was stale. The current `backend/config.py` falls back to a **local** mongod (`mongodb://localhost:27017`, db `reversepicks`) when `MONGO_URL` isn't set, and in this dev workspace `MONGO_URL` does not exist as a secret, so the backend workflow here always talks to local mongod at `/home/runner/.reversepicks_db`.

However, that local mongod in the dev workspace is **empty/ephemeral** — no `users`, no `stripe_subscriptions`, no `apple_iap_subscriptions` documents, and the data directory itself doesn't reliably persist across workspace restarts. The real, live production data (actual users, subscriptions, saved picks) lives in the app's separately deployed/production environment, which is not reachable from this dev workspace's filesystem or localhost.

**Why:** Replit Deployments run in an isolated container/filesystem from the dev workspace by design. A workflow labeled "PRODUCTION=true" running in the dev workspace is still the *dev workspace's* process — it does not automatically share storage with a real Deployment unless explicitly wired to the same external database.

**How to apply:** Don't try to answer "how many users/subscribers/downloads does the app have" by querying local mongod in this workspace — it will return misleadingly low/zero numbers. Instead:
- For real payment/subscriber counts: query the connected Stripe and RevenueCat APIs directly (`listConnections('stripe')` / `listConnections('revenuecat')` in the code_execution sandbox) — but check `livemode`/project on the Stripe connection first, since the connected key may be in test mode and not reflect real transactions.
- For App Store download counts: use the App Store Connect API (JWT via ASC_KEY_ID/ASC_ISSUER_ID/ASC_PRIVATE_KEY) or point the user to the App Analytics tab in App Store Connect directly — Apple's Analytics Reports API requires an ongoing report request that takes 24-48h to start producing data, so it's not available on-demand.
- If you need to inspect a specific live prediction/pick's exact stored data, that data is not queryable from this workspace at all — reason about the bug from the code path instead (reproduce the calculation logic directly), rather than trying to fetch the live document.
