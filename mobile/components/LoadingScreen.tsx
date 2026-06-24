import React, { useEffect } from 'react';
import { View, Text, Image, StyleSheet, Platform, Dimensions } from 'react-native';
import Animated, {
  useSharedValue, useAnimatedStyle, withTiming, withRepeat,
  withSequence, withDelay, Easing,
} from 'react-native-reanimated';

const { width: W, height: H } = Dimensions.get('window');
const NEON = '#39FF14';
const DARK = '#050505';

const IMG_ASPECT = 1492 / 955;
const IMG_W = Math.min(W * 0.88, H * 0.68 * IMG_ASPECT);
const IMG_H = IMG_W / IMG_ASPECT;
const IMG_L = (W - IMG_W) / 2;
const IMG_T = H * 0.42 - IMG_H * 0.52;
const EYE_CX = W * 0.5;
const EYE_CY = H * 0.42;

// ── Lightning bolt ─────────────────────────────────────────────────────────────
function LightningBolt({
  x, y, rotation, delay, boltW, boltH,
}: { x: number; y: number; rotation: string; delay: number; boltW: number; boltH: number }) {
  const opacity = useSharedValue(0);

  useEffect(() => {
    const flash = () => {
      opacity.value = withSequence(
        withTiming(0,   { duration: 0 }),
        withTiming(1.0, { duration: 45 }),
        withTiming(0.6, { duration: 60 }),
        withTiming(1.0, { duration: 40 }),
        withTiming(0,   { duration: 100 }),
      );
    };
    const t = setTimeout(() => {
      flash();
      const period = 1400 + delay * 0.4;
      const id = setInterval(flash, period);
      return () => clearInterval(id);
    }, delay);
    return () => clearTimeout(t);
  }, []);

  const anim = useAnimatedStyle(() => ({ opacity: opacity.value }));
  return (
    <Animated.View
      pointerEvents="none"
      style={[{
        position: 'absolute',
        left: x,
        top: y,
        width: boltW,
        height: boltH,
        backgroundColor: NEON,
        transform: [{ rotate: rotation }],
        ...(Platform.OS === 'web'
          ? ({ boxShadow: `0 0 8px 3px rgba(57,255,20,0.8)` } as any)
          : {
              shadowColor: NEON,
              shadowOffset: { width: 0, height: 0 },
              shadowOpacity: 1,
              shadowRadius: 5,
            }),
      }, anim]}
    />
  );
}

// ── Cloud wisp ─────────────────────────────────────────────────────────────────
function CloudWisp({ y, delay, dir }: { y: number; delay: number; dir: 1 | -1 }) {
  const tx      = useSharedValue(dir > 0 ? -W * 0.15 : W * 0.15);
  const opacity = useSharedValue(0);

  useEffect(() => {
    const drift = () => {
      tx.value      = dir > 0 ? -W * 0.15 : W * 0.15;
      opacity.value = 0;
      opacity.value = withSequence(
        withTiming(0.22, { duration: 500 }),
        withTiming(0.22, { duration: 2200 }),
        withTiming(0,    { duration: 500 }),
      );
      tx.value = withTiming(dir > 0 ? W * 0.15 : -W * 0.15, {
        duration: 3200,
        easing: Easing.inOut(Easing.quad),
      });
    };
    const t = setTimeout(() => {
      drift();
      const id = setInterval(drift, 3800);
      return () => clearInterval(id);
    }, delay);
    return () => clearTimeout(t);
  }, []);

  const anim = useAnimatedStyle(() => ({
    opacity: opacity.value,
    transform: [{ translateX: tx.value }],
  }));
  return (
    <Animated.View
      pointerEvents="none"
      style={[{
        position: 'absolute',
        left: IMG_L - 20,
        top: y,
        width: IMG_W + 40,
        height: 20,
        backgroundColor: 'rgba(57,255,20,0.07)',
        borderRadius: 10,
      }, anim]}
    />
  );
}

// ── Single letter with staggered reveal ───────────────────────────────────────
function RevLetter({ char, delay }: { char: string; delay: number }) {
  const scale   = useSharedValue(0.4);
  const opacity = useSharedValue(0);

  useEffect(() => {
    scale.value   = withDelay(delay, withTiming(1,   { duration: 280, easing: Easing.out(Easing.back(1.5)) }));
    opacity.value = withDelay(delay, withTiming(1,   { duration: 200 }));
  }, []);

  const anim = useAnimatedStyle(() => ({
    opacity: opacity.value,
    transform: [{ scale: scale.value }],
  }));
  return (
    <Animated.Text style={[styles.revLetter, anim]}>{char}</Animated.Text>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function LoadingScreen() {
  // Eyelids
  const topLidY    = useSharedValue(0);
  const bottomLidY = useSharedValue(0);

  // Eye close at end
  const topLidClose    = useSharedValue(0);
  const bottomLidClose = useSharedValue(0);

  // HUD
  const hudOpacity = useSharedValue(0);
  const hudY       = useSharedValue(16);

  // Progress
  const progress = useSharedValue(0);

  // Tagline
  const tagOpacity = useSharedValue(0);

  useEffect(() => {
    // Signal proxy HTML loading screen to hide — React has mounted
    if (typeof window !== 'undefined' && (window as any).__rpHideLoader) {
      (window as any).__rpHideLoader();
    }

    // 1. Eyelids OPEN (reveal the eye)
    topLidY.value    = withTiming(-(H * 0.52 + IMG_H * 0.54), {
      duration: 820,
      easing: Easing.out(Easing.cubic),
    });
    bottomLidY.value = withTiming(H * 0.58 + (H - IMG_T - IMG_H) + 40, {
      duration: 820,
      easing: Easing.out(Easing.cubic),
    });

    // 2. Eyelids CLOSE at end (eye blinks shut)
    topLidClose.value = withDelay(3600,
      withTiming(H * 0.52 + IMG_H * 0.54, { duration: 480, easing: Easing.in(Easing.cubic) })
    );
    bottomLidClose.value = withDelay(3600,
      withTiming(-(H * 0.58 + (H - IMG_T - IMG_H) + 40), { duration: 480, easing: Easing.in(Easing.cubic) })
    );

    // 3. HUD slides in
    hudOpacity.value = withDelay(900, withTiming(1, { duration: 600 }));
    hudY.value       = withDelay(900, withTiming(0, { duration: 600, easing: Easing.out(Easing.quad) }));

    // 4. Tagline
    tagOpacity.value = withDelay(1400, withTiming(1, { duration: 500 }));

    // 5. Progress bar
    let cur = 0;
    const progId = setInterval(() => {
      cur = Math.min(cur + Math.random() * 22, 95);
      progress.value = withTiming(cur / 100, { duration: 350 });
      if (cur >= 95) clearInterval(progId);
    }, 400);
    return () => clearInterval(progId);
  }, []);

  const topLidAnim    = useAnimatedStyle(() => ({
    transform: [{ translateY: topLidY.value + topLidClose.value }],
  }));
  const bottomLidAnim = useAnimatedStyle(() => ({
    transform: [{ translateY: bottomLidY.value + bottomLidClose.value }],
  }));
  const hudAnim = useAnimatedStyle(() => ({
    opacity: hudOpacity.value,
    transform: [{ translateY: hudY.value }],
  }));
  const tagAnim    = useAnimatedStyle(() => ({ opacity: tagOpacity.value }));
  const progressAnim = useAnimatedStyle(() => ({
    width: `${progress.value * 100}%` as any,
  }));

  // Lightning bolts around the eye / crown
  const bolts = [
    { x: EYE_CX - IMG_W * 0.28, y: IMG_T - 16, rotation: '-24deg', delay: 950,  boltW: 80,  boltH: 2.5 },
    { x: EYE_CX + IMG_W * 0.06, y: IMG_T - 10, rotation: '20deg',  delay: 1550, boltW: 60,  boltH: 2 },
    { x: IMG_L + 6,              y: EYE_CY - 18, rotation: '-10deg', delay: 1150, boltW: 50,  boltH: 2 },
    { x: IMG_L + IMG_W - 58,     y: EYE_CY - 12, rotation: '14deg', delay: 1750, boltW: 55,  boltH: 2 },
    { x: EYE_CX - 45,            y: IMG_T + IMG_H * 0.09, rotation: '-38deg', delay: 2100, boltW: 72, boltH: 2 },
    { x: EYE_CX + 8,             y: IMG_T + IMG_H * 0.07, rotation: '30deg',  delay: 1350, boltW: 65, boltH: 2 },
  ];

  // Cloud wisps over the cloud area of the artwork (lower portion)
  const wisps = [
    { y: IMG_T + IMG_H * 0.56, delay: 1000, dir: 1  as const },
    { y: IMG_T + IMG_H * 0.66, delay: 1600, dir: -1 as const },
    { y: IMG_T + IMG_H * 0.75, delay: 2300, dir: 1  as const },
    { y: IMG_T + IMG_H * 0.83, delay: 900,  dir: -1 as const },
  ];

  // "REVERSEPICKS" letter animation — staggered futuristic reveal
  const BRAND = 'REVERSEPICKS';

  return (
    <View style={styles.root}>

      {/* ── Eye artwork ───────────────────────────────────────────── */}
      <Image
        source={require('../assets/splash-eye.jpeg')}
        style={styles.eyeImg}
        resizeMode="contain"
      />

      {/* ── Cloud wisps drifting over lower clouds ─────────────── */}
      {wisps.map((w, i) => (
        <CloudWisp key={i} y={w.y} delay={w.delay} dir={w.dir} />
      ))}

      {/* ── Lightning bolts around crown / eye ─────────────────── */}
      {bolts.map((b, i) => (
        <LightningBolt
          key={i}
          x={b.x} y={b.y}
          rotation={b.rotation}
          delay={b.delay}
          boltW={b.boltW}
          boltH={b.boltH}
        />
      ))}

      {/* ── Top eyelid ─────────────────────────────────────────── */}
      <Animated.View style={[styles.topLid, topLidAnim]} pointerEvents="none" />

      {/* ── Bottom eyelid ──────────────────────────────────────── */}
      <Animated.View style={[styles.bottomLid, bottomLidAnim]} pointerEvents="none" />

      {/* ── Futuristic REVERSEPICKS letter-by-letter reveal ─────── */}
      <Animated.View style={[styles.hudAnim, hudAnim]} pointerEvents="none">
        <View style={styles.brandRow}>
          {BRAND.split('').map((ch, i) => (
            <RevLetter key={i} char={ch} delay={i * 55} />
          ))}
        </View>

        <Animated.Text style={[styles.hudTagline, tagAnim]}>
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
  },

  eyeImg: {
    position: 'absolute',
    width: IMG_W,
    height: IMG_H,
    left: IMG_L,
    top: IMG_T,
  },

  // Eyelids — full-screen black bars that slide to reveal the eye
  topLid: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    height: H * 0.55 + IMG_H * 0.54,
    backgroundColor: DARK,
  },
  bottomLid: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    height: H * 0.6 + (H - IMG_T - IMG_H),
    backgroundColor: DARK,
  },

  hudAnim: {
    position: 'absolute',
    bottom: H * 0.05,
    left: 0,
    right: 0,
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
    letterSpacing: 4,
    ...(Platform.OS === 'web'
      ? ({ textShadow: `0 0 20px rgba(57,255,20,0.7), 0 0 40px rgba(57,255,20,0.3)` } as any)
      : {
          textShadowColor: NEON,
          textShadowOffset: { width: 0, height: 0 },
          textShadowRadius: 14,
        }),
  },

  hudTagline: {
    fontSize: 9,
    fontWeight: '600',
    color: NEON,
    letterSpacing: 2.5,
    textTransform: 'uppercase',
    opacity: 0.85,
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
