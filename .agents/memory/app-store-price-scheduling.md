---
name: App Store price scheduling
description: Production App Store subscription prices can remain at the old tier until Apple’s scheduled effective date.
---

App Store Connect subscription price changes are scheduled, not necessarily immediate. StoreKit and RevenueCat continue returning the current price until the effective date; the native paywall correctly displays that live `priceString`.

**Why:** A weekly subscription can show the old price in an installed app even when the requested new price is already configured for a future date.

**How to apply:** Inspect the subscription’s USA price schedule and linked price point before changing client code or creating another price. Never stack a second future price when one already exists.