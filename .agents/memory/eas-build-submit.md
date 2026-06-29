---
name: EAS Build & App Store Submit
description: How to build and submit ReversePicks iOS app to the App Store via EAS
---

## EAS Build

Command (from mobile/ dir):
```
EXPO_TOKEN=$EXPO_TOKEN EAS_NO_VCS=1 EAS_BUILD_NO_EXPO_GO_WARNING=1 EAS_SKIP_AUTO_FINGERPRINT=1 eas build --platform ios --profile production --non-interactive --no-wait
```

**Why EAS_NO_VCS=1**: Replit doesn't allow EAS to access git directly.
**Why --no-wait**: EAS builds take 8+ minutes; use --no-wait and poll separately.

Poll command:
```
EXPO_TOKEN=$EXPO_TOKEN eas build:list --platform ios --limit 1 --non-interactive
```

## EAS Submit

Command (from mobile/ dir):
```
EXPO_TOKEN=$EXPO_TOKEN EXPO_APPLE_ID=$APPLE_ID EXPO_APPLE_APP_SPECIFIC_PASSWORD=<app-specific-pwd> eas submit --platform ios --latest --non-interactive
```

- `APPLE_PASSWORD` secret must be an App-Specific Password (format: xxxx-xxxx-xxxx-xxxx), NOT the regular Apple ID password
- App-Specific Passwords are generated at appleid.apple.com → Security → App-Specific Passwords
- ASC App ID: 6781092173 (set in eas.json)

## Critical fixes that were required

1. **Replit package-firewall URLs**: Every time `npm install` runs inside Replit, package-lock.json gets `http://package-firewall.replit.local/npm/` URLs baked in. EAS build machines can't reach these. Fix: `sed -i 's|http://package-firewall.replit.local/npm/|https://registry.npmjs.org/|g' package-lock.json`. Prevented by pinning registry in .npmrc: `registry=https://registry.npmjs.org/`

2. **New Architecture must be enabled**: react-native-reanimated v4 (required for Xcode 16 compatibility) uses react-native-worklets, which requires `newArchEnabled: true` in app.json. Old arch + v4 = CocoaPods failure.

3. **react-native-reanimated v3 incompatible with Xcode 16**: The `{fmt}` library's consteval functions error at compile time on Xcode 16 (EAS "latest" image). Must use v4.

## eas.json submit profile
ascAppId: 6781092173
appleTeamId: FDC3LJRAC7

## Build history (successful)
- Build 128 (2bd82434): first clean build after apple-auth removal → FINISHED, submitted to TestFlight
- Build 129 (0ca0662d): all 7 auth polish improvements (Terms links, slide animations, Welcome back, Face ID, social proof, skeleton loaders, owner link behind 5 logo taps)

## expo-local-authentication
- Installed for Face ID; requires plugin in app.json with faceIDPermission + NSFaceIDUsageDescription in infoPlist
- Biometric button only shows when `hasHardwareAsync() && isEnrolledAsync()` AND savedEmail exists in SecureStore

## expo-apple-authentication — DO NOT re-add
- Removed permanently to fix XCODE_BUILD_ERROR (entitlement mismatch with provisioning profile)
- Re-adding requires manual provisioning profile regeneration via Apple 2FA (not feasible non-interactively)
