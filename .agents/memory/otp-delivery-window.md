---
name: OTP delivery window
description: Email OTPs can arrive out of order when users request resends in quick succession.
---

Email login must retain a short rolling window of unexpired, individually single-use codes instead of replacing the prior code on every resend.

**Why:** Email delivery can lag or arrive out of order, so replacing the stored code makes a code that is valid from the user's perspective fail immediately after a resend.

**How to apply:** Keep the window bounded and time-limited, accept only an unexpired matching entry, and mark only the accepted entry used.