---
name: API-Football quota governor
description: Durable rules for protecting the shared API-Football quota while preserving user-triggered prediction and active-pick traffic.
---

Bulk API-Football prefetch must remain opt-in. The app uses on-demand cache fills because startup-wide fixture/player scans can consume most of the provider's daily quota before users interact with the app.

**Why:** The provider quota was reaching roughly 60% early in the user's local morning. Independent background jobs, duplicate requests, and retry storms multiplied calls.

**How to apply:** Keep user-triggered search/prediction/live-pick calls on the shared request helper. Preserve short-lived caching and request coalescing there, enforce the configured daily soft budget, and fail fast on HTTP 429 instead of retrying every caller. Any new bulk job must be explicitly gated and budget-aware.