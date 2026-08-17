---
name: Native iOS crash boundaries
description: Native-only module loading and result-entry worklets can crash iOS at render time under New Architecture.
---

## Rule
Never use `require()` inside a React component function body to load a native-only module. Also avoid Reanimated `entering` worklets on the prediction result mount until they have been verified in a store-signed build. With React Native New Architecture (Hermes engine + Fabric renderer), either pattern can crash iOS at render time.

**Why:** Hermes/Fabric initializes modules differently from the legacy bridge. Lazy native resolution can interfere with Fabric's module setup, while result-entry worklets execute exactly when the successful prediction response changes the screen phase. Both can produce a fatal native crash that bypasses JS try/catch even when the server returns 200.

**How to apply:** Use Metro's platform-split file convention instead:
- `MyComponent.native.tsx` — contains the static `import` of the native-only module (runs on iOS/Android only)
- `MyComponent.tsx` — web fallback that returns children or a no-op

Metro resolves the correct file at compile time, so no runtime `require()` is ever needed. This is always the correct pattern for modules like `react-native-gesture-handler/ReanimatedSwipeable`. Keep the result screen on plain React Native views unless the transition has been verified in a store-signed build.

## Example that caused a crash
```js
// WRONG — crashes instantly on iOS with New Architecture
const _getNativeSwipeable = () => {
  if (Platform.OS === 'web') return null;
  try {
    return require('react-native-gesture-handler/ReanimatedSwipeable').default;
  } catch { return null; }
};

function SwipeableRow({ onDelete, children }) {
  const NativeSwipeable = _getNativeSwipeable(); // called at render time
  ...
}
```

## Correct fix
- `mobile/components/SwipeablePickRow.native.tsx` — static import, full implementation
- `mobile/components/SwipeablePickRow.tsx` — `return <>{children}</>` for web
- `picks.tsx` imports `SwipeablePickRow` from `@/components/SwipeablePickRow`
