---
name: Native-only module lazy require crash
description: Dynamic require() inside a React component function body causes instant iOS crash with New Architecture. Use platform-split files instead.
---

## Rule
Never use `require()` inside a React component function body to load a native-only module. With React Native New Architecture (Hermes engine + Fabric renderer), this pattern causes an instant crash on iOS before any UI renders.

**Why:** Hermes/Fabric initializes modules differently from the legacy bridge. A lazy `require()` inside a component can interfere with Fabric's module resolution or TurboModule initialization at render time, producing a fatal native crash that bypasses JS try/catch.

**How to apply:** Use Metro's platform-split file convention instead:
- `MyComponent.native.tsx` — contains the static `import` of the native-only module (runs on iOS/Android only)
- `MyComponent.tsx` — web fallback that returns children or a no-op

Metro resolves the correct file at compile time, so no runtime `require()` is ever needed. This is always the correct pattern for modules like `react-native-gesture-handler/ReanimatedSwipeable`.

## Example that caused crash (build 131)
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

## Correct fix (build 133)
- `mobile/components/SwipeablePickRow.native.tsx` — static import, full implementation
- `mobile/components/SwipeablePickRow.tsx` — `return <>{children}</>` for web
- `picks.tsx` imports `SwipeablePickRow` from `@/components/SwipeablePickRow`
