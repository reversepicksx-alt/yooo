import React, { useEffect } from 'react';
import { View, Image, StyleSheet, Platform, Dimensions } from 'react-native';
import Animated, {
  useSharedValue, useAnimatedStyle, withTiming, withDelay, Easing,
} from 'react-native-reanimated';

const { width: W, height: H } = Dimensions.get('window');
const NEON = '#39FF14';
const DARK = '#050505';

// Final logo size in the custom loading screen
const LOGO_SIZE = Math.min(W * 0.52, 220);

// On native the iOS splash image is 1024×1024 rendered with resizeMode:contain.
// It fills the screen width (390pt on iPhone 14) → 390/1024 ≈ 0.381 scale factor.
// The RP crest occupies ~25% of the 1024px image → 1024*0.25*0.381 ≈ 97pt visible.
// Starting native scale so our logo matches the native splash size exactly:
//   97 / LOGO_SIZE ≈ 97 / 203 ≈ 0.48
const NATIVE_SPLASH_SCALE = 97 / LOGO_SIZE; // ≈ 0.48

// ── Single letter with staggered pop-in ────────────────────────────────────
function RevLetter({ char, delay }: { char: string; delay: number }) {
  const opacity = useSharedValue(0);
  const scale   = useSharedValue(0.3);

  useEffect(() => {
    opacity.value = withDelay(delay, withTiming(1, { duration: 220, easing: Easing.out(Easing.back(1.5)) }));
    scale.value   = withDelay(delay, withTiming(1, { duration: 220, easing: Easing.out(Easing.back(1.5)) }));
  }, []);

  const anim = useAnimatedStyle(() => ({
    opacity: opacity.value,
    transform: [{ scale: scale.value }],
  }));

  return (
    <Animated.Text style={[styles.revLetter, anim]}>
      {char}
    </Animated.Text>
  );
}

// ── Main loading screen ─────────────────────────────────────────────────────
export default function LoadingScreen({ onDone }: { onDone?: () => void }) {
  const isNative = Platform.OS !== 'web';

  // Native: logo starts at the native-splash size & opacity so the transition
  // from iOS splash → this screen is invisible (same logo, same position).
  // The logo then scales up to full size as the text fades in.
  // Web: logo fades in from 0 after the HTML pre-loader is removed.
  const logoOpacity = useSharedValue(isNative ? 1 : 0);
  const logoScale   = useSharedValue(isNative ? NATIVE_SPLASH_SCALE : 0.78);
  const hudOpacity  = useSharedValue(0);
  const tagOpacity  = useSharedValue(0);
  const progress    = useSharedValue(0);

  useEffect(() => {
    // Tell the web HTML pre-loader to hide — React has mounted
    if (typeof window !== 'undefined' && (window as any).__rpHideLoader) {
      (window as any).__rpHideLoader();
    }

    if (!isNative) {
      // Web: fade + scale logo in after HTML pre-loader clears
      logoOpacity.value = withTiming(1, { duration: 700, easing: Easing.out(Easing.cubic) });
      logoScale.value   = withTiming(1, { duration: 700, easing: Easing.out(Easing.cubic) });
    } else {
      // Native: logo is already visible at splash size — scale up to full size
      // in sync with the text appearing so it feels like one unified animation
      logoScale.value = withTiming(1, { duration: 600, easing: Easing.out(Easing.cubic) });
    }

    // REVERSEPICKS letters
    const hudDelay = isNative ? 150 : 600;
    hudOpacity.value = withDelay(hudDelay, withTiming(1, { duration: 400 }));

    // Tagline
    const tagDelay = isNative ? 800 : 1400;
    tagOpacity.value = withDelay(tagDelay, withTiming(0.85, { duration: 500 }));

    // Progress bar
    const progDelay = isNative ? 150 : 600;
    progress.value = withDelay(progDelay, withTiming(0.92, { duration: 3000, easing: Easing.out(Easing.cubic) }));

    const t = setTimeout(() => onDone?.(), 2800);
    return () => clearTimeout(t);
  }, []);

  const logoAnim = useAnimatedStyle(() => ({
    opacity: logoOpacity.value,
    transform: [{ scale: logoScale.value }],
  }));
  const hudAnim = useAnimatedStyle(() => ({ opacity: hudOpacity.value }));
  const tagAnim = useAnimatedStyle(() => ({ opacity: tagOpacity.value }));
  const progressAnim = useAnimatedStyle(() => ({
    width: `${progress.value * 100}%` as any,
  }));

  const BRAND = 'REVERSEPICKS';

  return (
    <View style={styles.root}>

      {/* ── RP Logo ──────────────────────────────────────────────────────────
          On native: no marginBottom — logo stays at true screen center
          matching the iOS native splash logo position exactly.
          On web: marginBottom pushes logo up in the normal flex flow.     */}
      <Animated.View
        style={[styles.logoWrap, isNative && styles.logoWrapNative, logoAnim]}
        pointerEvents="none"
      >
        <Image
          source={require('../assets/logo.png')}
          style={styles.logoImg}
          resizeMode="contain"
        />
      </Animated.View>

      {/* ── REVERSEPICKS + tagline + progress ────────────────────────────────
          On native: absolutely positioned so it doesn't shift the logo out
          of center. On web: normal flow below the logo.                   */}
      <Animated.View
        style={[styles.hud, isNative ? styles.hudNative : styles.hudWeb, hudAnim]}
        pointerEvents="none"
      >
        <View style={styles.brandRow}>
          {BRAND.split('').map((ch, i) => (
            <RevLetter key={i} char={ch} delay={i * 55} />
          ))}
        </View>

        <Animated.Text style={[styles.tagline, tagAnim]}>
          THE EYE SEES WHAT OTHERS MISS
        </Animated.Text>

        <View style={styles.progressTrack}>
          <Animated.View style={[styles.progressFill, progressAnim]} />
        </View>
      </Animated.View>

    </View>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  root: {
    ...StyleSheet.absoluteFillObject as any,
    backgroundColor: DARK,
    zIndex: 999,
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
  },

  logoWrap: {
    alignItems: 'center',
    justifyContent: 'center',
    // Web default: marginBottom is set via logoWrapWeb (not needed — hudWeb in flow)
    marginBottom: H * 0.06,
  },

  // On native: no marginBottom so the logo sits at true screen center
  logoWrapNative: {
    marginBottom: 0,
  },

  logoImg: {
    width:  LOGO_SIZE,
    height: LOGO_SIZE,
  },

  hud: {
    alignItems: 'center',
    gap: 10,
  },

  // Native: absolutely positioned below the logo so it doesn't move the logo
  // out of center. Top ~64% of screen height puts text just below the logo.
  hudNative: {
    position: 'absolute',
    top: H * 0.64,
    left: 0,
    right: 0,
    alignItems: 'center',
    gap: 10,
  },

  // Web: normal flow (logo has marginBottom to push it up)
  hudWeb: {},

  brandRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },

  revLetter: {
    fontSize: 22,
    fontWeight: '900',
    color: '#ffffff',
    letterSpacing: 3,
    ...(Platform.OS === 'web'
      ? ({ textShadow: `0 0 18px rgba(57,255,20,0.7)` } as any)
      : {
          textShadowColor: NEON,
          textShadowOffset: { width: 0, height: 0 },
          textShadowRadius: 12,
        }),
  },

  tagline: {
    fontSize: 9,
    fontWeight: '600',
    color: NEON,
    letterSpacing: 2.5,
    textTransform: 'uppercase',
  },

  progressTrack: {
    width: W * 0.48,
    height: 2,
    borderRadius: 1,
    backgroundColor: 'rgba(57,255,20,0.2)',
    overflow: 'hidden',
    marginTop: 2,
  },

  progressFill: {
    height: 2,
    backgroundColor: NEON,
    ...(Platform.OS === 'web'
      ? ({ boxShadow: `0 0 8px 2px ${NEON}` } as any)
      : {
          shadowColor: NEON,
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 1,
          shadowRadius: 6,
        }),
  },
});
