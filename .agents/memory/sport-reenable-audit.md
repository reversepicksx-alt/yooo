---
name: Sport re-enable audit
description: Durable checklist for bringing a previously hidden sport back into the product.
---

When re-enabling a previously hidden sport, audit the full lifecycle rather than only the sport configuration: customer picker visibility, saved-pick persistence, saved-history response filters, shared response normalization, mobile adapters, live tracking, and terminal settlement.

**Why:** A legacy “hidden sports” history filter can continue dropping picks after the picker and prediction route are restored, making the feature appear partially broken and hiding valid results from users.

**How to apply:** Search for the sport key and for old “hidden”, “budget”, or “no longer offered” gates across backend routes, background loops, and mobile rendering before declaring the re-enable complete.