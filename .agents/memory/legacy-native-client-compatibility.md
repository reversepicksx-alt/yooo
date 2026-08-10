---
name: Legacy native client compatibility
description: How to ship narrow display fixes to an installed native bundle without an OTA update.
---

When the installed native bundle lacks `expo-updates`, JavaScript/UI changes cannot reach it without a new binary. For narrow display fixes, prefer a backward-compatible server payload field or encoding that the old renderer already displays, while normalizing it for current clients.

**Why:** The installed TestFlight app was older than the workspace source and did not include the H2H venue marker. The project does not currently include an OTA update channel, so a backend-only compatibility path was necessary to avoid forcing an iOS rebuild for a small display correction.

**How to apply:** Verify the old renderer's exact fields and substring behavior first, encode only inside an already-visible field, preserve the structured field for newer clients, and publish the backend before claiming the installed app is fixed.