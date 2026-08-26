---
name: Paywall purchase/restore signup parity
description: Any "no session yet" branch added to the buy flow must be mirrored on the restore flow, or restoring on a fresh install silently drops the entitlement.
---

In `mobile/app/paywall.tsx`, `handlePurchase` checks `if (!session?.email)` and routes to a guest email-capture step (`setShowEmailCapture(true)`) so a brand-new user's purchase gets linked to an account. `handleRestore` originally lacked this same check — RevenueCat would correctly find the restored entitlement, but `syncBackendAndEnter` no-ops when there's no session (nothing to sync it to), and the code still tried to route into the authenticated app on a null session.

**Why:** This is the exact scenario that happens on a fresh reinstall/new device: user taps "Restore Purchases" before logging in via email OTP. Any future addition of a new "no session" branch to one entry point (purchase, restore, or a future third path) must be checked against all sibling entry points that also call `syncBackendAndEnter`.

**How to apply:** When touching IAP purchase/restore code, search all callers of the backend sync function and confirm each one has matching guest-signup handling, not just the one you're editing.

Authenticated restore is also a recovery path for older native builds and reinstalls. RevenueCat may return an anonymous StoreKit customer ID there; the server must still verify that anonymous customer directly with RevenueCat before linking the active entitlement to the signed-in email. The client must refresh its stored session from the grant response.

**Why:** A device can show an active Apple entitlement while `/api/predict` rejects the account if the anonymous customer was never linked to the email session. Blocking anonymous IDs at the authenticated grant endpoint made this mismatch persist even after Restore Purchases.

**How to apply:** Keep anonymous IDs allowed only on the authenticated, server-verified grant path; never trust the client entitlement alone, and update local access state after a successful grant.

Analyze-time access must revalidate the current native entitlement and refresh the server session before protected prediction requests; Account's cached Premium label is not sufficient evidence.

**Why:** Older or reinstalled native sessions can show an active Apple entitlement in Account while the backend session still contains `NoSubscription`, producing a false “Active subscription required” response.

**How to apply:** Keep RevenueCat as evidence only, send its current customer identity to the server for verification, persist the refreshed access type, and fail closed when RevenueCat is unavailable.

Native IAP grant calls must use the shared API client, not a relative browser URL. Relative `/api/...` fetches are routed by the web proxy but are not a valid transport for an iOS build.

**Why:** The auth and paywall recovery paths could detect an active StoreKit entitlement, then fail to sync it because the native request used `fetch('/api/...')`; the user was sent to the paywall despite already having access.

**How to apply:** Route every authenticated RevenueCat grant through `syncAppleAccess` (or `apiCall`) and use the server-returned access type to update the local session.

RevenueCat V2 `active_entitlements.items[].entitlement_id` is the entitlement resource ID, not necessarily the mobile SDK identifier; production currently returns `entl9515aab63f` for Pro.

**Why:** Checking only the SDK-facing identifier (`pro`) caused verified active Apple subscribers to appear unsubscribed to the backend and receive 403 prediction responses.

**How to apply:** Treat the V2 resource ID as canonical, keep it configurable with a hardcoded project fallback, and validate both live-access and purchase-grant paths against the same ID set.

The native tab layout must not redirect a session marked `NoSubscription` to the paywall while StoreKit/server entitlement reconciliation is still possible; Predict should revalidate the server session immediately before Analyze.

**Why:** A paying iOS customer can briefly restore with a stale local session or an in-flight RevenueCat query. Redirecting first creates a repeat paywall loop even though the server can confirm `Premium (Apple)`.

**How to apply:** Keep the native user on Predict during entitlement loading and stale-session reconciliation, then update the local access type from the server grant. Preserve the backend subscription check for users who remain unsubscribed.
