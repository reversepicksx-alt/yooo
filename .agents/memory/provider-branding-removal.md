---
name: Provider branding removal
description: External sports-provider quota and dashboard details must not appear in user-facing app surfaces.
---

Provider names, quota counters, breaker controls, and provider dashboard links are internal operations details, not product UI. Keep them out of Pick Insights, Account, and user-facing disclaimers.

**Why:** Provider limits and noisy status values create user confusion without explaining pick quality; removing the display does not require breaking the underlying data pipeline.

**How to apply:** Preserve internal provider safeguards needed for fixtures and settlement, but expose only product-level health or actionable user messaging when there is a real customer impact.