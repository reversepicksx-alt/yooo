---
name: Paywall purchase/restore signup parity
description: Any "no session yet" branch added to the buy flow must be mirrored on the restore flow, or restoring on a fresh install silently drops the entitlement.
---

In `mobile/app/paywall.tsx`, `handlePurchase` checks `if (!session?.email)` and routes to a guest email-capture step (`setShowEmailCapture(true)`) so a brand-new user's purchase gets linked to an account. `handleRestore` originally lacked this same check — RevenueCat would correctly find the restored entitlement, but `syncBackendAndEnter` no-ops when there's no session (nothing to sync it to), and the code still tried to route into the authenticated app on a null session.

**Why:** This is the exact scenario that happens on a fresh reinstall/new device: user taps "Restore Purchases" before logging in via email OTP. Any future addition of a new "no session" branch to one entry point (purchase, restore, or a future third path) must be checked against all sibling entry points that also call `syncBackendAndEnter`.

**How to apply:** When touching IAP purchase/restore code, search all callers of the backend sync function and confirm each one has matching guest-signup handling, not just the one you're editing.
