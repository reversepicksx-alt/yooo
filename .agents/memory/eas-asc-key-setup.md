---
name: EAS submit ASC API key setup
description: How to reconstruct the App Store Connect API private key file required by eas.json's submit.production.ios.ascApiKeyPath, and disk-quota pitfalls during EAS builds/submits.
---

## Key reconstruction
`eas.json`'s iOS submit profile points `ascApiKeyPath` at a local file (`/home/runner/.eas/asc_key_fixed.p8`) rather than using Apple ID + app-specific-password auth. That file does not persist across sessions/workspaces and must be rebuilt before every submit.

The `ASC_PRIVATE_KEY` secret stores the PEM contents with spaces instead of newlines (not a valid PEM as-is). Reconstruct it programmatically, in-memory, without printing the value:
1. Strip any existing `-----BEGIN/END PRIVATE KEY-----` markers and collapse all whitespace out of the body.
2. Re-wrap the base64 body at 64 chars per line.
3. Re-add the BEGIN/END markers and write to `/home/runner/.eas/asc_key_fixed.p8` (mode 0600).

**Why:** EAS submit fails to parse the key if it's not properly PEM-wrapped; this must be redone each time because the reconstructed file isn't committed/persisted (correctly, since it's derived from a secret).

**How to apply:** Run this reconstruction step immediately before every `eas submit` for iOS, before authoring the actual submit command. Safe to delete the file afterward — it's cheap to regenerate.

## Disk quota (EDQUOT) risk
EAS CLI temp files accumulate under `/tmp/runner/eas-cli-nodejs` across build/submit invocations. If this grows unchecked it can trigger `EDQUOT`/error -122 mid-build. Check `du -sh /tmp` before a build; clear stale EAS temp dirs if usage is high. Not usually an issue on a fresh workspace but worth a quick check if a build/submit fails with a disk-related error.

## Build vs Submit status polling
- Poll build status with `eas build:list --platform ios --limit 1 --non-interactive` (status moves `new` → `in progress` → `finished`).
- After `eas submit --non-interactive --no-wait`, do NOT re-run `eas submit` to check progress — it creates a duplicate submission. Instead poll the submission id via Expo's GraphQL API (`https://api.expo.dev/graphql`, `Authorization: Bearer $EXPO_TOKEN`, query `submissions.byId(submissionId).status`) until `FINISHED` or an `error` is populated.
