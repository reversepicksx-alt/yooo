---
name: Web auth route split
description: Safari can mount the shared auth screen after logout but paint only its dark background
---

## Rule
Keep the web sign-in experience in a dedicated `auth.web.tsx` route when the shared/native auth tree renders blank after logout. Reuse the same backend verification contract, but avoid carrying the native-oriented nested flex and modal layout into Safari.

**Why:** The shared auth component mounted and produced no browser exception, while Terms and Privacy rendered normally. A dedicated web route restored the form reliably after sign-out.

**How to apply:** When changing web auth, verify the actual signed-out `/auth` preview after rebuilding `mobile/dist`; do not assume a successful React mount means the controls are visible.