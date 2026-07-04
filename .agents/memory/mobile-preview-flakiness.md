---
name: Mobile web preview flakiness on splash/auth screens
description: Screenshot tool app_preview can catch the app mid-splash-animation; a single screenshot or single runTest attempt failing on the auth flow is not proof of a real bug.
---

The mobile app's landing/auth screen has an animated splash logo before the login form settles. A single `screenshot(app_preview)` snapshot, or one `runTest` run, can land mid-animation or mid-request and report "paywall/subscribe screen" or "unable to verify" even when the backend and full flow are actually correct.

**Why:** Chased what looked like a broken owner-login flow (mandatory Playwright nav check failed twice) but `curl` directly against the backend's `/api/auth/verify-access` (both port 8000 and proxied port 5000) returned `verified: true, access_type: "Owner"` immediately. A follow-up `runTest` with explicit network-response capture confirmed the real app flow works and lands on the authenticated tab bar — the earlier failures were test-timing flakiness, not a regression.

**How to apply:** Before concluding the mandatory owner-login nav check (required by replit.md before completing any task) is broken, verify the backend endpoint directly with `curl` first. If curl succeeds, retry `runTest` once with explicit waits / network-capture instructions before treating it as a real regression.
