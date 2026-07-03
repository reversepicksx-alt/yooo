---
name: RevenueCat V2 server API quirks
description: Gotchas when adding a backend live-check against RevenueCat's REST API (V2 secret keys, project_id requirement, permission scopes).
---

RevenueCat V2 secret API keys are incompatible with the legacy V1 REST endpoints (`/v1/subscribers/{id}` returns 403 "incompatible with API V1") — must use V2 exclusively once a V2 key is issued.

The V2 "Get a Customer" endpoint requires a `project_id` in the path: `GET /v2/projects/{project_id}/customers/{customer_id}`. There is no project-less shortcut.

Listing projects (`GET /v2/projects`) to auto-discover the project_id requires the separate `project_configuration:projects:read` permission scope — a key scoped only to "Customer information: Read-only" (the minimal scope needed for subscriber lookups) gets a 403 on `/v2/projects`. Simplest fix: get the project_id once from the dashboard URL (`app.revenuecat.com/projects/<id>/...`) and hardcode/env-var it rather than granting a broader scope just to self-discover it.

Active entitlements live under `active_entitlements.items[]` with `entitlement_id` and `expires_at` (ms epoch, `null` = never expires) — different shape from the V1 `entitlements` dict with `expires_date` ISO strings.

**Why this matters:** any backend live-fallback against RevenueCat (e.g. to cover delayed/dropped webhooks that leave a paying customer stuck as unsubscribed) needs a V2-scoped secret key + a known project_id, not just "a RevenueCat secret key."
