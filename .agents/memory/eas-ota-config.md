---
name: EAS OTA configuration
description: Production JavaScript updates require an explicit Expo Updates URL and app-version runtime policy in dynamic app config.
---

When publishing OTA updates from a dynamic Expo config, keep `expo-updates` installed and configure the project Updates URL plus `runtimeVersion.policy = "appVersion"` explicitly.

**Why:** EAS cannot safely inject these values into `app.config.js`; without them, `eas update` stops before publishing. Publishing also replaces the chosen export directory with native bundles, so rebuild the web export afterward when the local preview serves that directory.

**How to apply:** Confirm the runtime version matches the shipped native binary, publish to the production branch without running a native build, then restore the local web `dist` output and restart the preview workflow.