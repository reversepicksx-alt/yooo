---
name: Website Stripe duplicate protection
description: Safety rules for launching new Stripe website prices without affecting existing subscribers.
---

New website checkout must never cancel, migrate, or replace an existing Stripe subscription. Search all Stripe customers for the email and block checkout when any non-terminal subscription exists. Existing customers use the explicit Change Plan action, which changes the subscription with no proration so the new rate begins at renewal.

**Why:** A previous checkout guard canceled every open subscription before creating a replacement, which could disrupt an existing customer and create an unsafe double-billing path during a brand-new price launch.

**How to apply:** Use deterministic email-scoped idempotency for checkout retries, fail closed if Stripe cannot be queried, keep automated retirement routines dormant, and treat Stripe's live subscription state as authoritative. Never test by creating a real checkout or charge.