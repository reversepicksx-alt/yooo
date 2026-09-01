import React, { useEffect } from 'react';
import { View, Image, StyleSheet, Platform, Dimensions } from 'react-native';
import Animated, {
  useSharedValue, useAnimatedStyle, withTiming, withDelay,
  withSequence, withSpring, Easing, SharedValue,
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

  // 4 independent bolt animations for staggered flash effect
  const bolt1 = useSharedValue(0);
  const bolt2 = useSharedValue(0);
  const bolt3 = useSharedValue(0);
  const bolt4 = useSharedValue(0);

  // Expanding ring animations
  const ring1Scale   = useSharedValue(0.2);
  const ring1Opacity = useSharedValue(0);
  const ring2Scale   = useSharedValue(0.2);
  const ring2Opacity = useSharedValue(0);
  const ring3Scale   = useSharedValue(0.2);
  const ring3Opacity = useSharedValue(0);

  useEffect(() => {
    // Web: never hide the HTML splash here — AppBoot in _layout.tsx controls
    // the hand-off so the HTML splash stays visible until React is ready.
    if (!isNative) {
      // Skip __rpHideLoader on web; let AppBoot handle it after auth init.
      logoOpacity.value = withTiming(1, { duration: 700, easing: Easing.out(Easing.cubic) });
      logoScale.value   = withTiming(1, { duration: 700, easing: Easing.out(Easing.cubic) });
    } else {
      // Scale: from splash size → grow slightly during spin → overshoot → settle
      logoScale.value = withSequence(
        withTiming(1,    { duration: 1000, easing: Easing.linear }),
        withTiming(1.06, { duration: 250,  easing: Easing.out(Easing.cubic) }),
        withSpring(1,    { damping: 8, stiffness: 180 }),
      );

      // Spin: 5 rotations, then spring-snap to exact position
      logoRotate.value = withSequence(
        withTiming(1710, { duration: 1000, easing: Easing.linear }),
        withTiming(1890, { duration: 250,  easing: Easing.out(Easing.cubic) }),
        withSpring(1800, { damping: 6, stiffness: 200 }),
      );

      // 3-round lightning storm starting just as linear spin finishes
      const FLASH_DUR = 55;
      const FLASH_GAP = 110;
      const mkFlash = (offset: number) =>
        withDelay(1050 + offset,
          withSequence(
            withTiming(1, { duration: FLASH_DUR }),
            withTiming(0, { duration: FLASH_DUR }),
            withDelay(FLASH_GAP, withTiming(1, { duration: FLASH_DUR })),
            withTiming(0, { duration: FLASH_DUR }),
            withDelay(FLASH_GAP, withTiming(1, { duration: FLASH_DUR })),
            withTiming(0, { duration: 120 }),
          )
        );
      bolt1.value = mkFlash(0);
      bolt2.value = mkFlash(60);
      bolt3.value = mkFlash(30);
      bolt4.value = mkFlash(90);

      // Expanding electric rings (3 waves)
      const mkRing = (scl: SharedValue<number>, opc: SharedValue<number>, delay: number) => {
        scl.value = withDelay(delay, withTiming(3.5, { duration: 900, easing: Easing.out(Easing.cubic) }));
        opc.value = withDelay(delay, withSequence(
          withTiming(0.85, { duration: 80 }),
          withTiming(0,    { duration: 820, easing: Easing.in(Easing.cubic) }),
        ));
      };
      mkRing(ring1Scale, ring1Opacity, 1070);
      mkRing(ring2Scale, ring2Opacity, 1320);
      mkRing(ring3Scale, ring3Opacity, 1570);
    }

    const hudDelay = isNative ? 1300 : 600;
    hudOpacity.value = withDelay(hudDelay, withTiming(1, { duration: 400 }));

    const tagDelay = isNative ? 1900 : 1400;
    tagOpacity.value = withDelay(tagDelay, withTiming(0.85, { duration: 500 }));

    const duration = isNative ? 2500 : 2800;
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

  const bolt1Anim = useAnimatedStyle(() => ({ opacity: bolt1.value }));
  const bolt2Anim = useAnimatedStyle(() => ({ opacity: bolt2.value }));
  const bolt3Anim = useAnimatedStyle(() => ({ opacity: bolt3.value }));
  const bolt4Anim = useAnimatedStyle(() => ({ opacity: bolt4.value }));

  const ring1Anim = useAnimatedStyle(() => ({
    opacity: ring1Opacity.value,
    transform: [{ scale: ring1Scale.value }],
  }));
  const ring2Anim = useAnimatedStyle(() => ({
    opacity: ring2Opacity.value,
    transform: [{ scale: ring2Scale.value }],
  }));
  const ring3Anim = useAnimatedStyle(() => ({
    opacity: ring3Opacity.value,
    transform: [{ scale: ring3Scale.value }],
  }));

  const hudAnim = useAnimatedStyle(() => ({ opacity: hudOpacity.value }));
  const tagAnim = useAnimatedStyle(() => ({ opacity: tagOpacity.value }));
  const BRAND = 'REVERSEPICKS';
  const LETTER_BASE = isNative ? 1300 : 0;

  return (
    <View style={styles.root}>

      <Animated.View
        style={[styles.logoWrap, isNative && styles.logoWrapNative, scaleAnim]}
        pointerEvents="none"
      >
        {/* Expanding electric shockwave rings */}
        {isNative && (
          <>
            <Animated.View style={[styles.ring, ring1Anim]} />
            <Animated.View style={[styles.ring, ring2Anim]} />
            <Animated.View style={[styles.ring, ring3Anim]} />
          </>
        )}

        {/* Lightning bolt rays — 4 independent groups, staggered */}
        {isNative && (
          <>
            <Animated.View style={[styles.boltWrap, bolt1Anim]}>
              <View style={[styles.boltRay, { transform: [{ rotate: '0deg'   }] }]} />
              <View style={[styles.boltRay, { transform: [{ rotate: '90deg'  }] }]} />
            </Animated.View>
            <Animated.View style={[styles.boltWrap, bolt2Anim]}>
              <View style={[styles.boltRay, { transform: [{ rotate: '45deg'  }] }]} />
              <View style={[styles.boltRay, { transform: [{ rotate: '135deg' }] }]} />
            </Animated.View>
            <Animated.View style={[styles.boltWrap, bolt3Anim]}>
              <View style={[styles.boltRay, { transform: [{ rotate: '22.5deg'  }] }]} />
              <View style={[styles.boltRay, { transform: [{ rotate: '112.5deg' }] }]} />
            </Animated.View>
            <Animated.View style={[styles.boltWrap, bolt4Anim]}>
              <View style={[styles.boltRay, { transform: [{ rotate: '67.5deg'  }] }]} />
              <View style={[styles.boltRay, { transform: [{ rotate: '157.5deg' }] }]} />
            </Animated.View>
          </>
        )}

        {/* Rotating logo */}
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

  ring: {
    position: 'absolute',
    width: LOGO_SIZE,
    height: LOGO_SIZE,
    borderRadius: LOGO_SIZE / 2,
    borderWidth: 2,
    borderColor: NEON,
    shadowColor: NEON,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 1,
    shadowRadius: 12,
    elevation: 8,
  },

  boltWrap: {
    position: 'absolute',
    width: LOGO_SIZE * 2.4,
    height: LOGO_SIZE * 2.4,
    alignItems: 'center',
    justifyContent: 'center',
  },

  boltRay: {
    position: 'absolute',
    width: 2,
    height: LOGO_SIZE * 2.2,
    backgroundColor: NEON,
    shadowColor: NEON,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 1,
    shadowRadius: 10,
    elevation: 12,
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

});
