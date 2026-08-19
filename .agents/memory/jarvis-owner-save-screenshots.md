---
name: JARVIS owner save and screenshots
description: Security and behavior contract for private JARVIS save-pick and prediction screenshot actions.
---

## Rule
Private JARVIS actions must never accept subscriber identity or session credentials in their public request bodies. The bearer-authenticated assistant resolves the configured owner account server-side, keeps the session token inside MongoDB/backend execution, and returns only model data, opaque temporary screenshot handles, and ordinary pick identifiers.

**Why:** The assistant is already private and authenticated; forwarding the owner’s email/session token through ChatGPT actions creates an unnecessary credential exposure path and can put secrets into action history, logs, or schemas.

**How to apply:** Keep `save-pick/soccer` prediction inputs-only and preserve the existing save function so duplicate detection, correlation warnings, cache invalidation, and My Picks persistence remain unchanged. Screenshot actions use server-side Chromium to capture named report sections (`read`, `form`, `matchup`, `context`, `picks`); image handles are authenticated and expire quickly.