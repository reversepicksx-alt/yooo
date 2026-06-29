---
name: Web splash race condition
description: The _layout.tsx race condition that caused permanent black screen on web
---

## Rule
On web, never gate the Stack render behind a React state variable that controls
the HTML loading overlay. The overlay (z-index 99999, injected by proxy.js) and
the React render are independent layers.

## The Bug
`webSplashReady` state was used to either return a dark placeholder <View> or
render the Stack. `showApp()` did:
  1. `setWebSplashReady(true)` — async React state update
  2. `requestAnimationFrame(() => __rpHideLoader())` — fires BEFORE step 1 commits

Result: overlay fades, but React hasn't re-rendered yet — user sees the dark
placeholder <View> instead of the app. Looked like a permanent black screen.

## The Fix
- Remove `webSplashReady` entirely
- Always render `<Stack>` on web immediately (overlay covers it)
- Call `hideWebOverlay()` after `isLoading` becomes false + 300ms delay (so
  auth-driven navigation commits before overlay fades)
- Hard cap: call `hideWebOverlay()` at 8s regardless

## Why Safari needs fresh bundle hash
`Clear-Site-Data: "cache"` is not supported in iOS Safari. Cached JS bundles
persist until the URL changes. Always do a clean rebuild (`rm -rf dist`) when
fixing web boot issues — expo's content hash changes with any source change,
forcing Safari to download fresh.
