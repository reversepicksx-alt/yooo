---
name: Community feed payloads
description: Keep historical community-feed responses lightweight so large pick-card images cannot make real chat history appear empty.
---

The community history endpoint must exclude stored base64 pick-card images by default. Large historical image payloads can exceed the client timeout; the old empty-state UI then misleadingly suggests that chat history was deleted. Image data should be requested only for live/new-message paths or explicitly when needed.

**Why:** Historical pick-card images made a 50-message response take roughly 20 seconds and caused the mobile/web client to fall into its empty state after timing out, even though the messages remained in the database.

**How to apply:** Use a database projection that omits `imageData` for history requests, keep image inclusion opt-in, and distinguish load errors from a genuinely empty feed in the UI.