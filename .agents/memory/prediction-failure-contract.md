---
name: Prediction failure contract
description: User-triggered prediction provider failures must remain structured and retryable at the API boundary.
---

Prediction endpoints should return a structured `{error, retryable}` response for unexpected provider/enrichment failures rather than exposing an opaque HTTP 5xx to the mobile client.

**Why:** Prediction requests combine several external feeds and optional enrichment stages; one transient failure should not turn into the generic “server error” experience or make the user think their account is broken.

**How to apply:** Preserve normal authentication, validation, and no-data responses. For unexpected failures, keep the traceback in backend logs but return the stable retryable error shape that the shared client already handles.