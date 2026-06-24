import React, { useEffect, useRef } from 'react';
import {
  View, Text, Image, StyleSheet, Platform, Dimensions,
} from 'react-native';
import Animated, {
  useSharedValue, useAnimatedStyle, withTiming, withRepeat,
  withSequence, withDelay, Easing,
} from 'react-native-reanimated';

const { width: W, height: H } = Dimensions.get('window');
const SHORT = Math.min(W, H);
const NEON  = '#39FF14';
const DARK  = '#050505';

// Eye artwork: 1492×955, eye pupil is at ~50% x, ~52% y in the image
const IMG_ASPECT = 1492 / 955;
// Size: fill 88% of width, but cap so image height ≤ 68% of screen height
const IMG_W = Math.min(W * 0.88, H * 0.68 * IMG_ASPECT);
const IMG_H = IMG_W / IMG_ASPECT;
const IMG_L = (W - IMG_W) / 2;
const IMG_T = H * 0.42 - IMG_H * 0.52;   // so eye pupil lands at 42% from top

// Eye center in screen coordinates (drives all glow / ring / scan positioning)
const EYE_CX = W * 0.5;
const EYE_CY = H * 0.42;
const RING_R  = SHORT * 0.22;

// ── Sonar ring ────────────────────────────────────────────────────────────────
function SonarRing({ delay }: { delay: number }) {
  const scale   = useSharedValue(0.25);
  const opacity = useSharedValue(0);

  useEffect(() => {
    const pulse = () => {
      scale.value   = 0.25;
      opacity.value = 0.8;
      scale.value   = withTiming(1.9, { duration: 3200, easing: Easing.out(Easing.quad) });
      opacity.value = withTiming(0,   { duration: 3200 });
    };
    const t = setTimeout(() => {
      pulse();
      const id = setInterval(pulse, 3800);
      return () => clearInterval(id);
    }, delay);
    return () => clearTimeout(t);
  }, []);

  const anim = useAnimatedStyle(() => ({
    transform: [{ scale: scale.value }],
    opacity: opacity.value,
  }));

  return (
    <Animated.View
      style={[styles.sonarRing, anim]}
      pointerEvents="none"
    />
  );
}

// ── Star sparkle ──────────────────────────────────────────────────────────────
interface SparkleProps {
  x: number;
  y: number;
  delayMs: number;
  size: number;
}
function Sparkle({ x, y, delayMs, size }: SparkleProps) {
  const opacity = useSharedValue(0);
  const scale   = useSharedValue(0.2);

  useEffect(() => {
    const fire = () => {
      opacity.value = 0;
      scale.value   = 0.2;
      opacity.value = withDelay(0, withSequence(
        withTiming(1,   { duration: 350 }),
        withTiming(0.8, { duration: 400 }),
        withTiming(0,   { duration: 350 }),
      ));
      scale.value = withDelay(0, withSequence(
        withTiming(1,   { duration: 350 }),
        withTiming(1,   { duration: 400 }),
        withTiming(0.2, { duration: 350 }),
      ));
    };
    const t = setTimeout(() => {
      fire();
      const period = 2200 + delayMs * 0.3;
      const id = setInterval(fire, period);
      return () => clearInterval(id);
    }, delayMs);
    return () => clearTimeout(t);
  }, []);

  const anim = useAnimatedStyle(() => ({
    opacity: opacity.value,
    transform: [{ scale: scale.value }],
  }));

  return (
    <Animated.View style={[styles.sparkleWrap, { left: x - size, top: y - size }, anim]} pointerEvents="none">
      <View style={[styles.flareH, { width: size * 2, height: 1.5, top: size - 0.75, backgroundColor: NEON }]} />
      <View style={[styles.flareV, { height: size * 2, width: 1.5, left: size - 0.75, backgroundColor: NEON }]} />
    </Animated.View>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function LoadingScreen() {
  const containerOpacity = useSharedValue(1);   // image visible immediately
  const irisGlow         = useSharedValue(0.35);
  const outerGlow        = useSharedValue(0.2);
  const scanY            = useSharedValue(0);
  const scanOpacity      = useSharedValue(0);
  const progress         = useSharedValue(0);
  const hudOpacity       = useSharedValue(0);
  const hudTranslateY    = useSharedValue(18);

  useEffect(() => {
    // 1. Fade whole screen in
    containerOpacity.value = withTiming(1, { duration: 900, easing: Easing.out(Easing.quad) });

    // 2. Iris glow pulse (on the eye pupil)
    irisGlow.value = withRepeat(
      withSequence(
        withTiming(1,    { duration: 2200, easing: Easing.inOut(Easing.sin) }),
        withTiming(0.35, { duration: 2200, easing: Easing.inOut(Easing.sin) }),
      ), -1,
    );

    // 3. Outer atmospheric glow
    outerGlow.value = withRepeat(
      withSequence(
        withTiming(0.6,  { duration: 3000 }),
        withTiming(0.2,  { duration: 3000 }),
      ), -1,
    );

    // 4. Scan line loop
    const startScan = () => {
      scanY.value     = IMG_T - 10;
      scanOpacity.value = 0;
      scanOpacity.value = withSequence(
        withTiming(0.9, { duration: 150 }),
        withTiming(0.9, { duration: 2200 }),
        withTiming(0,   { duration: 150 }),
      );
      scanY.value = withTiming(IMG_T + IMG_H + 10, {
        duration: 2500,
        easing: Easing.inOut(Easing.quad),
      });
    };
    startScan();
    const scanId = setInterval(startScan, 4200);

    // 5. Progress bar
    let cur = 0;
    const progId = setInterval(() => {
      cur = Math.min(cur + Math.random() * 20, 95);
      progress.value = withTiming(cur / 100, { duration: 350 });
      if (cur >= 95) clearInterval(progId);
    }, 450);

    // 6. HUD slides up
    hudOpacity.value    = withDelay(500, withTiming(1, { duration: 700 }));
    hudTranslateY.value = withDelay(500, withTiming(0, { duration: 700, easing: Easing.out(Easing.quad) }));

    return () => {
      clearInterval(scanId);
      clearInterval(progId);
    };
  }, []);

  const containerAnim = useAnimatedStyle(() => ({ opacity: containerOpacity.value }));
  const irisGlowAnim  = useAnimatedStyle(() => ({ opacity: irisGlow.value }));
  const outerGlowAnim = useAnimatedStyle(() => ({ opacity: outerGlow.value }));
  const scanAnim      = useAnimatedStyle(() => ({
    transform: [{ translateY: scanY.value }],
    opacity: scanOpacity.value,
  }));
  const progressAnim  = useAnimatedStyle(() => ({
    width: `${progress.value * 100}%` as any,
  }));
  const hudAnim = useAnimatedStyle(() => ({
    opacity: hudOpacity.value,
    transform: [{ translateY: hudTranslateY.value }],
  }));

  // Sparkle positions — scattered around the eye artwork area
  const sparkles = [
    { x: IMG_L + IMG_W * 0.08, y: IMG_T + IMG_H * 0.12, delay: 200,  size: 7 },
    { x: IMG_L + IMG_W * 0.92, y: IMG_T + IMG_H * 0.10, delay: 900,  size: 9 },
    { x: IMG_L + IMG_W * 0.03, y: IMG_T + IMG_H * 0.52, delay: 1600, size: 6 },
    { x: IMG_L + IMG_W * 0.97, y: IMG_T + IMG_H * 0.48, delay: 500,  size: 8 },
    { x: IMG_L + IMG_W * 0.18, y: IMG_T + IMG_H * 0.85, delay: 1300, size: 7 },
    { x: IMG_L + IMG_W * 0.82, y: IMG_T + IMG_H * 0.82, delay: 700,  size: 9 },
    { x: IMG_L + IMG_W * 0.50, y: IMG_T - 10,           delay: 1100, size: 10 },
    { x: IMG_L + IMG_W * 0.32, y: IMG_T + IMG_H * 0.25, delay: 400,  size: 5 },
    { x: IMG_L + IMG_W * 0.68, y: IMG_T + IMG_H * 0.22, delay: 1800, size: 6 },
  ];

  return (
    <Animated.View style={[styles.root, containerAnim]}>

      {/* ── Eye artwork — centered, smaller, black shows around it ──────── */}
      <Image
        source={require('../assets/splash-eye.jpeg')}
        style={styles.eyeImg}
        resizeMode="contain"
      />

      {/* ── Outer atmospheric glow around eye ──────────────────────────── */}
      <Animated.View style={[styles.outerGlow, outerGlowAnim]} pointerEvents="none" />

      {/* ── Iris inner glow (pulsing green on the pupil) ───────────────── */}
      <Animated.View style={[styles.irisGlow, irisGlowAnim]} pointerEvents="none" />

      {/* ── Sonar rings expanding from eye center ──────────────────────── */}
      <SonarRing delay={0}    />
      <SonarRing delay={1267} />
      <SonarRing delay={2534} />

      {/* ── Scan line sweeping over the eye ────────────────────────────── */}
      <Animated.View style={[styles.scanLine, scanAnim]} pointerEvents="none" />

      {/* ── Sparkle stars scattered around artwork ─────────────────────── */}
      {sparkles.map((s, i) => (
        <Sparkle key={i} x={s.x} y={s.y} delayMs={s.delay} size={s.size} />
      ))}

      {/* ── Bottom HUD ─────────────────────────────────────────────────── */}
      <Animated.View style={[styles.hud, hudAnim]} pointerEvents="none">
        <Text style={styles.hudTitle}>REVERSEPICKS</Text>
        <Text style={styles.hudTagline}>THE EYE SEES WHAT OTHERS MISS</Text>
        <View style={styles.progressTrack}>
          <Animated.View style={[styles.progressFill, progressAnim]} />
        </View>
      </Animated.View>

    </Animated.View>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  root: {
    ...StyleSheet.absoluteFillObject as any,
    backgroundColor: DARK,
    zIndex: 999,
  },

  eyeImg: {
    position: 'absolute',
    width: IMG_W,
    height: IMG_H,
    left: IMG_L,
    top: IMG_T,
  },

  outerGlow: {
    position: 'absolute',
    width:  SHORT * 1.6,
    height: SHORT * 1.6,
    borderRadius: SHORT * 0.8,
    left: EYE_CX - SHORT * 0.8,
    top:  EYE_CY - SHORT * 0.8,
    ...(Platform.OS === 'web'
      ? ({ boxShadow: `0 0 ${SHORT * 0.5}px ${SHORT * 0.15}px rgba(57,255,20,0.22)` } as any)
      : {
          shadowColor: NEON,
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 0.6,
          shadowRadius: SHORT * 0.35,
          backgroundColor: 'transparent',
        }),
  },

  irisGlow: {
    position: 'absolute',
    width:  SHORT * 0.28,
    height: SHORT * 0.28,
    borderRadius: SHORT * 0.14,
    left: EYE_CX - SHORT * 0.14,
    top:  EYE_CY - SHORT * 0.14,
    ...(Platform.OS === 'web'
      ? ({
          boxShadow: `0 0 ${SHORT * 0.12}px ${SHORT * 0.06}px rgba(57,255,20,0.55), 0 0 ${SHORT * 0.05}px ${SHORT * 0.03}px rgba(57,255,20,0.9)`,
        } as any)
      : {
          shadowColor: NEON,
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 0.9,
          shadowRadius: SHORT * 0.1,
          backgroundColor: 'rgba(57,255,20,0.08)',
        }),
  },

  sonarRing: {
    position: 'absolute',
    width:  RING_R * 2,
    height: RING_R * 2,
    borderRadius: RING_R,
    borderWidth: 1.5,
    borderColor: NEON,
    left: EYE_CX - RING_R,
    top:  EYE_CY - RING_R,
    ...(Platform.OS === 'web'
      ? ({ boxShadow: `0 0 8px 2px rgba(57,255,20,0.3)` } as any)
      : {
          shadowColor: NEON,
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 0.5,
          shadowRadius: 6,
        }),
  },

  scanLine: {
    position: 'absolute',
    left: 0,
    width: W,
    height: 2,
    backgroundColor: NEON,
    ...(Platform.OS === 'web'
      ? ({ boxShadow: `0 0 12px 4px rgba(57,255,20,0.5), 0 0 30px 8px rgba(57,255,20,0.2)` } as any)
      : {
          shadowColor: NEON,
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 0.9,
          shadowRadius: 10,
        }),
  },

  sparkleWrap: {
    position: 'absolute',
  },

  flareH: {
    position: 'absolute',
    ...(Platform.OS === 'web'
      ? ({ boxShadow: `0 0 6px 2px rgba(57,255,20,0.6)` } as any)
      : {}),
  },

  flareV: {
    position: 'absolute',
    ...(Platform.OS === 'web'
      ? ({ boxShadow: `0 0 6px 2px rgba(57,255,20,0.6)` } as any)
      : {}),
  },

  hud: {
    position: 'absolute',
    bottom: H * 0.06,
    left: 0,
    right: 0,
    alignItems: 'center',
    gap: 8,
  },

  hudTitle: {
    fontSize: 20,
    fontWeight: '900',
    color: '#ffffff',
    letterSpacing: 7,
    textTransform: 'uppercase',
    ...(Platform.OS === 'web'
      ? ({ textShadow: `0 0 24px rgba(57,255,20,0.6), 0 0 50px rgba(57,255,20,0.3)` } as any)
      : {
          textShadowColor: NEON,
          textShadowOffset: { width: 0, height: 0 },
          textShadowRadius: 18,
        }),
  },

  hudTagline: {
    fontSize: 9,
    fontWeight: '600',
    color: NEON,
    letterSpacing: 2.5,
    textTransform: 'uppercase',
    opacity: 0.8,
    marginBottom: 2,
  },

  progressTrack: {
    width: W * 0.5,
    height: 2,
    borderRadius: 1,
    backgroundColor: 'rgba(57,255,20,0.2)',
    overflow: 'hidden',
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
