---
name: Mobile web build always calls production API, never the local dev backend
description: Why a backend feature that works via curl/local-proxy can still show "can't reach server" in the Replit web preview
---

`mobile/lib/api.ts#getApiBase()` hardcodes `https://reversepicks.com` whenever
`Platform.OS === 'web'`, by design ("same as iOS app — never the dev backend").
Only native builds fall back to `EXPO_PUBLIC_API_URL`/localhost. This means the
web preview in this dev workspace (and the mobile proxy's own dev backend on
port 8000) is **never** exercised when testing through the browser — every
`fetch` from web goes straight to the live production domain.

**Why:** consistent behavior between iOS App Store builds and the web preview,
and avoids accidentally shipping dev-only API URLs.

**How to apply:** when a brand-new backend endpoint/route works fine via
direct curl to localhost:8000 (or through the mobile proxy on 5000) but the
Playwright/browser test shows a generic "Cannot reach server" or "could not
load X" message on a freshly-built card, first check whether the endpoint is
even deployed to production (`curl https://reversepicks.com/api/<route>`) —
a 404 there gets masked by any blanket `catch` in the API client into the same
generic connectivity-failure text. New backend routes will not be reachable
from the web preview until the backend is actually deployed/published; local
dev-environment testing of such routes must go through direct curl to
localhost:8000, not the browser preview.
