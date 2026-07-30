---
name: API-Football quota governor
description: Durable rules for protecting the shared API-Football quota while preserving user-triggered prediction and active-pick traffic.
---

Bulk API-Football prefetch must remain opt-in. The app uses on-demand cache fills because startup-wide fixture/player scans can consume most of the provider's daily quota before users interact with the app.

**Why:** The provider quota was reaching roughly 60% early in the user's local morning. Independent background jobs, duplicate requests, and retry storms multiplied calls.

**How to apply:** Keep user-triggered search/prediction/live-pick calls on the shared request helper. Preserve short-lived caching and request coalescing there. The configured daily soft budget is only a background-maintenance guard; user prediction requests must bypass it through request-scoped priority. Only a real provider 429/daily-quota response should trip the all-day breaker. Any new bulk job must be explicitly gated and budget-aware.

The provider dashboard's usage percentage is independent from the app's local call counter. A local "700 calls" message does not prove API-Football quota exhaustion; inspect the provider response before treating it as an upstream outage.

**Why:** Background maintenance exhausted the app's local 700-call ceiling while API-Football still showed 2% usage, starving live predictions and causing a misleading diagnosis.