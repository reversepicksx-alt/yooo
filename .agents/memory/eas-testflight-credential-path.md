---
name: EAS TestFlight submission credential path
description: Avoid unnecessary iOS build retries when EAS submit's configured App Store Connect key path is absent.
---

Before an EAS TestFlight submission, verify that the configured App Store Connect API-key file path exists. In this workspace the key is stored as a managed secret, while the submit profile references a local file path that can be absent after environment changes.

**Why:** EAS can complete an iOS artifact build while `eas submit` then fails before upload because it cannot find that local key file. Rebuilding does not repair a submission-only credential-path problem and wastes a build number.

**How to apply:** Keep the successful build ID, materialize the existing secret to the configured path with restrictive file permissions, then submit that same ID. Confirm the current build's commit/version and ensure no matching in-progress build exists before creating any new iOS build.