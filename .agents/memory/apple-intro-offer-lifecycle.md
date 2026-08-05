---
name: Apple introductory-offer lifecycle
description: Operational behavior when disabling an App Store Connect introductory offer.
---

Deleting an introductory offer in App Store Connect removes it from future purchase eligibility. It does not cancel or retroactively convert subscriptions that already entered the trial period.

**Why:** Disabling the offer and canceling active customer trials are separate operations with different customer-impact and authorization implications.

**How to apply:** Disable the App Store offer first. Treat existing active trials as a separate, explicit customer-management decision; never cancel them as an automatic side effect of removing the offer.