import React, { useEffect } from 'react';
import { View, Image, StyleSheet, Platform, Dimensions } from 'react-native';
import Animated, {
  useSharedValue, useAnimatedStyle, withTiming, withDelay, withSequence, Easing,
} from 'react-native-reanimated';

const { width: W, height: H } = Dimensions.get('window');
const NEON = '#39FF14';
const DARK = '#050505';

const LOGO_SIZE = Math.min(W * 0.52, 220);

const NATIVE_SPLASH_SCALE = 97 / LOGO_SIZE;

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

export default function LoadingScreen({ onDone }: { onDone?: () => void }) {
  const isNative = Platform.OS !== 'web';

  const logoOpacity = useSharedValue(isNative ? 1 : 0);
  const logoScale   = useSharedValue(isNative ? NATIVE_SPLASH_SCALE : 0.78);
  const logoRotate  = useSharedValue(0);
  const hudOpacity  = useSharedValue(0);
  const tagOpacity  = useSharedValue(0);
  const progress    = useSharedValue(0);
  const boltOpacity = useSharedValue(0);

  useEffect(() => {
    if (typeof window !== 'undefined' && (window as any).__rpHideLoader) {
      (window as any).__rpHideLoader();
    }

    if (!isNative) {
      logoOpacity.value = withTiming(1, { duration: 700, easing: Easing.out(Easing.cubic) });
      logoScale.value   = withTiming(1, { duration: 700, easing: Easing.out(Easing.cubic) });
    } else {
      const SPIN_EASE = Easing.bezier(0.22, 1, 0.36, 1);
      logoScale.value  = withTiming(1,   { duration: 1300, easing: SPIN_EASE });
      logoRotate.value = withTiming(720, { duration: 1300, easing: SPIN_EASE });

      boltOpacity.value = withDelay(1340,
        withSequence(
          withTiming(1, { duration: 65 }),
          withTiming(0, { duration: 90 }),
          withDelay(260, withTiming(1, { duration: 65 })),
          withTiming(0, { duration: 90 }),
          withDelay(240, withTiming(1, { duration: 65 })),
          withTiming(0, { duration: 130 }),
        )
      );
    }

    const hudDelay = isNative ? 1500 : 600;
    hudOpacity.value = withDelay(hudDelay, withTiming(1, { duration: 400 }));

    const tagDelay = isNative ? 2150 : 1400;
    tagOpacity.value = withDelay(tagDelay, withTiming(0.85, { duration: 500 }));

    const progDelay = isNative ? 400 : 600;
    progress.value = withDelay(progDelay, withTiming(0.92, { duration: 4500, easing: Easing.out(Easing.cubic) }));

    const duration = isNative ? 5000 : 2800;
    const t = setTimeout(() => onDone?.(), duration);
    return () => clearTimeout(t);
  }, []);

  const scaleAnim = useAnimatedStyle(() => ({
    opacity: logoOpacity.value,
    transform: [{ scale: logoScale.value }],
  }));

  const rotateAnim = useAnimatedStyle(() => ({
    transform: [{ rotate: `${logoRotate.value}deg` }],
  }));

  const boltAnim  = useAnimatedStyle(() => ({ opacity: boltOpacity.value }));
  const hudAnim   = useAnimatedStyle(() => ({ opacity: hudOpacity.value }));
  const tagAnim   = useAnimatedStyle(() => ({ opacity: tagOpacity.value }));
  const progressAnim = useAnimatedStyle(() => ({
    width: `${progress.value * 100}%` as any,
  }));

  const BRAND = 'REVERSEPICKS';
  const LETTER_BASE = isNative ? 1500 : 0;

  return (
    <View style={styles.root}>

      <Animated.View
        style={[styles.logoWrap, isNative && styles.logoWrapNative, scaleAnim]}
        pointerEvents="none"
      >
        {isNative && (
          <Animated.View style={[styles.boltWrap, boltAnim]}>
            {[0, 45, 90, 135].map((angle) => (
              <View
                key={angle}
                style={[styles.boltRay, { transform: [{ rotate: `${angle}deg` }] }]}
              />
            ))}
          </Animated.View>
        )}

        <Animated.View style={isNative ? rotateAnim : undefined}>
          <Image
            source={require('../assets/logo.png')}
            style={styles.logoImg}
            resizeMode="contain"
          />
        </Animated.View>
      </Animated.View>

      <Animated.View
        style={[styles.hud, isNative ? styles.hudNative : styles.hudWeb, hudAnim]}
        pointerEvents="none"
      >
        <View style={styles.brandRow}>
          {BRAND.split('').map((ch, i) => (
            <RevLetter key={i} char={ch} delay={LETTER_BASE + i * 55} />
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

  logoWrapNative: {
    marginBottom: 0,
  },

  boltWrap: {
    position: 'absolute',
    width: LOGO_SIZE * 2.2,
    height: LOGO_SIZE * 2.2,
    alignItems: 'center',
    justifyContent: 'center',
  },

  boltRay: {
    position: 'absolute',
    width: 1.5,
    height: LOGO_SIZE * 2.0,
    backgroundColor: NEON,
    shadowColor: NEON,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 1,
    shadowRadius: 8,
    elevation: 10,
  },

  logoImg: {
    width:  LOGO_SIZE,
    height: LOGO_SIZE,
  },

  hud: {
    alignItems: 'center',
    gap: 10,
  },

  hudNative: {
    position: 'absolute',
    top: H * 0.64,
    left: 0,
    right: 0,
    alignItems: 'center',
    gap: 10,
  },

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
