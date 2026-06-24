import React, { useEffect } from 'react';
import { View, Text, Image, StyleSheet, Platform, Dimensions } from 'react-native';
import Animated, {
  useSharedValue, useAnimatedStyle, withTiming, withDelay, Easing,
} from 'react-native-reanimated';

const { width: W, height: H } = Dimensions.get('window');
const NEON = '#39FF14';
const DARK = '#050505';

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
  const logoOpacity = useSharedValue(0);
  const logoScale   = useSharedValue(0.78);
  const hudOpacity  = useSharedValue(0);
  const tagOpacity  = useSharedValue(0);
  const progress    = useSharedValue(0);

  useEffect(() => {
    // Signal proxy HTML loading screen to hide — React has mounted
    if (typeof window !== 'undefined' && (window as any).__rpHideLoader) {
      (window as any).__rpHideLoader();
    }

    // Logo entrance
    logoOpacity.value = withTiming(1, { duration: 700, easing: Easing.out(Easing.cubic) });
    logoScale.value   = withTiming(1, { duration: 700, easing: Easing.out(Easing.cubic) });

    // HUD (brand letters)
    hudOpacity.value = withDelay(600, withTiming(1, { duration: 400 }));

    // Tagline
    tagOpacity.value = withDelay(1400, withTiming(0.85, { duration: 500 }));

    // Progress bar
    progress.value = withDelay(600, withTiming(0.92, { duration: 3000, easing: Easing.out(Easing.cubic) }));

    // Done callback
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

      {/* ── RP Logo ──────────────────────────────────────────────── */}
      <Animated.View style={[styles.logoWrap, logoAnim]} pointerEvents="none">
        <Image
          source={require('../assets/logo.png')}
          style={styles.logoImg}
          resizeMode="contain"
        />
      </Animated.View>

      {/* ── REVERSEPICKS + tagline + progress ────────────────────── */}
      <Animated.View style={[styles.hud, hudAnim]} pointerEvents="none">
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
    marginBottom: H * 0.06,
  },

  logoImg: {
    width:  Math.min(W * 0.52, 220),
    height: Math.min(W * 0.52, 220),
  },

  hud: {
    alignItems: 'center',
    gap: 10,
  },

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
