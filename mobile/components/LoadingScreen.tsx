import React, { useEffect, useRef } from 'react';
import {
  View,
  Text,
  Image,
  StyleSheet,
  Dimensions,
  Animated as RNAnimated,
  Platform,
} from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withTiming,
  withRepeat,
  withSequence,
  withDelay,
  Easing,
  interpolate,
} from 'react-native-reanimated';

const { width: W, height: H } = Dimensions.get('window');

const NEON   = '#39FF14';
const GOLD   = '#FFD700';
const DARK   = '#050505';

// Use the shorter dimension so proportions hold on both portrait mobile + landscape web
const SHORT      = Math.min(W, H);

// Eye position on screen (image is landscape, displayed cover on portrait)
const EYE_CX     = W * 0.5;
const EYE_CY     = H * 0.47;
const EYE_HALF_H = SHORT * 0.13;
const IRIS_R     = SHORT * 0.19;
const CROWN_Y    = H * 0.13;

// ─── Sonar ring ────────────────────────────────────────────────────────────────
function SonarRing({ delay }: { delay: number }) {
  const scale   = useSharedValue(0.2);
  const opacity = useSharedValue(0);

  useEffect(() => {
    const run = () => {
      scale.value   = 0.2;
      opacity.value = 0;
      scale.value   = withDelay(delay, withTiming(2.8, { duration: 3000, easing: Easing.out(Easing.quad) }));
      opacity.value = withDelay(delay, withSequence(
        withTiming(0.7, { duration: 300 }),
        withTiming(0, { duration: 2700 }),
      ));
    };
    run();
    const id = setInterval(run, 3600);
    return () => clearInterval(id);
  }, []);

  const style = useAnimatedStyle(() => ({
    transform: [{ scale: scale.value }],
    opacity:   opacity.value,
  }));

  return (
    <Animated.View
      style={[
        styles.sonarRing,
        style,
        { left: EYE_CX - IRIS_R, top: EYE_CY - IRIS_R },
      ]}
    />
  );
}

// ─── Orbiting dot ──────────────────────────────────────────────────────────────
function OrbitDot({ angle, radius, duration }: { angle: number; radius: number; duration: number }) {
  const rot = useSharedValue(angle);

  useEffect(() => {
    rot.value = withRepeat(
      withTiming(angle + 360, { duration, easing: Easing.linear }),
      -1,
      false,
    );
  }, []);

  const style = useAnimatedStyle(() => {
    const rad = (rot.value * Math.PI) / 180;
    return {
      transform: [
        { translateX: Math.cos(rad) * radius },
        { translateY: Math.sin(rad) * radius },
      ],
    };
  });

  return <Animated.View style={[styles.orbitDot, style]} />;
}

// ─── Main component ────────────────────────────────────────────────────────────
interface LoadingScreenProps {
  label?:    string;
  statuses?: string[];
}

export default function LoadingScreen({
  label    = 'LOADING',
  statuses = [
    'INITIALIZING SIGHT',
    'SCANNING THE MARKET',
    'CALIBRATING PROBABILITY',
    'FUTURE LOCKED IN',
  ],
}: LoadingScreenProps) {

  // Shared values – Reanimated
  const fadeIn      = useSharedValue(0);
  const eyelidOpen  = useSharedValue(0);
  const irisRot     = useSharedValue(0);
  const irisRot2    = useSharedValue(0);
  const crownGlow   = useSharedValue(0);
  const scanY       = useSharedValue(-H * 0.05);
  const scanOpacity = useSharedValue(0);
  const pupilPulse  = useSharedValue(1);
  const progress    = useSharedValue(0);
  const centerGlow  = useSharedValue(0.3);

  // 12 fixed star shared values (hooks must not be in a loop)
  const st0  = useSharedValue(0.4); const st1  = useSharedValue(0.8);
  const st2  = useSharedValue(0.2); const st3  = useSharedValue(0.9);
  const st4  = useSharedValue(0.5); const st5  = useSharedValue(0.1);
  const st6  = useSharedValue(0.7); const st7  = useSharedValue(0.3);
  const st8  = useSharedValue(0.6); const st9  = useSharedValue(0.4);
  const st10 = useSharedValue(0.9); const st11 = useSharedValue(0.2);
  const starValues = [st0,st1,st2,st3,st4,st5,st6,st7,st8,st9,st10,st11];

  // Lightning – RN Animated (simpler flash scheduling)
  const lightL = useRef(new RNAnimated.Value(0)).current;
  const lightR = useRef(new RNAnimated.Value(0)).current;
  const lightL2 = useRef(new RNAnimated.Value(0)).current;
  const lightR2 = useRef(new RNAnimated.Value(0)).current;

  // Status label
  const [statusIdx, setStatusIdx] = React.useState(0);

  const flashLightning = (val: RNAnimated.Value, delayMs: number) => {
    setTimeout(function doFlash() {
      RNAnimated.sequence([
        RNAnimated.timing(val, { toValue: 1,   duration: 60,  useNativeDriver: true }),
        RNAnimated.timing(val, { toValue: 0.2, duration: 100, useNativeDriver: true }),
        RNAnimated.timing(val, { toValue: 0.9, duration: 50,  useNativeDriver: true }),
        RNAnimated.timing(val, { toValue: 0,   duration: 250, useNativeDriver: true }),
      ]).start(() => setTimeout(doFlash, 1800 + Math.random() * 3500));
    }, delayMs);
  };

  useEffect(() => {
    // 0. Center glow – pulses from eye center, illuminates artwork on any viewport
    centerGlow.value = withRepeat(
      withSequence(
        withTiming(1,    { duration: 2500 }),
        withTiming(0.35, { duration: 2500 }),
      ),
      -1,
    );

    // 1. Fade in
    fadeIn.value = withTiming(1, { duration: 900, easing: Easing.out(Easing.quad) });

    // 2. Eye opens
    eyelidOpen.value = withDelay(
      700,
      withTiming(1, { duration: 2200, easing: Easing.out(Easing.cubic) }),
    );

    // 3. Iris rotates (outer ring clockwise, inner counter-clockwise)
    irisRot.value  = withDelay(1500,
      withRepeat(withTiming(360,  { duration: 7000, easing: Easing.linear }), -1, false),
    );
    irisRot2.value = withDelay(1500,
      withRepeat(withTiming(-360, { duration: 4500, easing: Easing.linear }), -1, false),
    );

    // 4. Crown golden glow pulses
    crownGlow.value = withDelay(600,
      withRepeat(
        withSequence(
          withTiming(1,   { duration: 1800 }),
          withTiming(0.2, { duration: 1800 }),
        ),
        -1,
      ),
    );

    // 5. Pupil inner pulse
    pupilPulse.value = withDelay(1200,
      withRepeat(
        withSequence(
          withTiming(1.15, { duration: 900 }),
          withTiming(0.85, { duration: 900 }),
        ),
        -1,
      ),
    );

    // 6. Scan line sweeps down
    const runScan = () => {
      scanY.value       = -H * 0.05;
      scanOpacity.value = 0;
      scanOpacity.value = withTiming(0.45, { duration: 300 });
      scanY.value = withTiming(H * 1.05, {
        duration: 4500,
        easing: Easing.linear,
      });
      setTimeout(() => {
        scanOpacity.value = withTiming(0, { duration: 400 });
        setTimeout(runScan, 600);
      }, 4200);
    };
    setTimeout(runScan, 1800);

    // 7. Stars twinkle
    const starDelays = [0,300,800,150,600,1100,400,900,200,700,50,1300];
    const starDurs   = [900,600,1200,700,800,1000,550,950,750,650,1100,800];
    starValues.forEach((sv, i) => {
      sv.value = withDelay(
        starDelays[i],
        withRepeat(
          withSequence(
            withTiming(1,   { duration: starDurs[i] }),
            withTiming(0.05, { duration: starDurs[i] }),
          ),
          -1,
        ),
      );
    });

    // 8. Lightning flashes
    flashLightning(lightL,  1200);
    flashLightning(lightR,  2500);
    flashLightning(lightL2, 3800);
    flashLightning(lightR2, 600);

    // 9. Progress bar
    let current = 0;
    const progressInterval = setInterval(() => {
      current += Math.random() * 7 + 2;
      if (current >= 100) { current = 100; clearInterval(progressInterval); }
      progress.value = withTiming(current / 100, { duration: 200 });
    }, 220);

    // 10. Status cycling
    let idx = 0;
    const statusInterval = setInterval(() => {
      idx = Math.min(idx + 1, statuses.length - 1);
      setStatusIdx(idx);
    }, 1800);

    // 11. Periodic blink (every 5–9 seconds)
    let blinkTimeout: ReturnType<typeof setTimeout>;
    const scheduleBlink = () => {
      blinkTimeout = setTimeout(() => {
        eyelidOpen.value = withSequence(
          withTiming(0,   { duration: 120 }),
          withTiming(1,   { duration: 200 }),
        );
        scheduleBlink();
      }, 5000 + Math.random() * 4000);
    };
    const blinkStart = setTimeout(scheduleBlink, 3500);

    return () => {
      clearInterval(progressInterval);
      clearInterval(statusInterval);
      clearTimeout(blinkStart);
      clearTimeout(blinkTimeout!);
    };
  }, []);

  // ─── Animated styles ─────────────────────────────────────────────────────────

  const containerAnim = useAnimatedStyle(() => ({ opacity: fadeIn.value }));

  const topEyelidAnim = useAnimatedStyle(() => ({
    transform: [{
      translateY: interpolate(eyelidOpen.value, [0, 1], [0, -EYE_HALF_H * 1.1]),
    }],
  }));
  const botEyelidAnim = useAnimatedStyle(() => ({
    transform: [{
      translateY: interpolate(eyelidOpen.value, [0, 1], [0, EYE_HALF_H * 1.1]),
    }],
  }));

  const irisAnim = useAnimatedStyle(() => ({
    transform: [{ rotate: `${irisRot.value}deg` }],
  }));
  const irisAnim2 = useAnimatedStyle(() => ({
    transform: [{ rotate: `${irisRot2.value}deg` }],
  }));

  const pupilAnim = useAnimatedStyle(() => ({
    transform: [{ scale: pupilPulse.value }],
  }));

  const crownAnim = useAnimatedStyle(() => ({
    opacity: crownGlow.value,
  }));

  const centerGlowAnim = useAnimatedStyle(() => ({
    opacity: centerGlow.value,
  }));

  const scanAnim = useAnimatedStyle(() => ({
    transform: [{ translateY: scanY.value }],
    opacity: scanOpacity.value,
  }));

  const progressAnim = useAnimatedStyle(() => ({
    width: `${progress.value * 100}%` as any,
  }));

  // Star animated styles – each useAnimatedStyle must be called at top level
  const sa0  = useAnimatedStyle(() => ({ opacity: st0.value }));
  const sa1  = useAnimatedStyle(() => ({ opacity: st1.value }));
  const sa2  = useAnimatedStyle(() => ({ opacity: st2.value }));
  const sa3  = useAnimatedStyle(() => ({ opacity: st3.value }));
  const sa4  = useAnimatedStyle(() => ({ opacity: st4.value }));
  const sa5  = useAnimatedStyle(() => ({ opacity: st5.value }));
  const sa6  = useAnimatedStyle(() => ({ opacity: st6.value }));
  const sa7  = useAnimatedStyle(() => ({ opacity: st7.value }));
  const sa8  = useAnimatedStyle(() => ({ opacity: st8.value }));
  const sa9  = useAnimatedStyle(() => ({ opacity: st9.value }));
  const sa10 = useAnimatedStyle(() => ({ opacity: st10.value }));
  const sa11 = useAnimatedStyle(() => ({ opacity: st11.value }));
  const starStyles = [sa0,sa1,sa2,sa3,sa4,sa5,sa6,sa7,sa8,sa9,sa10,sa11];

  // Star positions (relative to screen, fixed)
  const starPos = [
    { x: 0.06, y: 0.07 }, { x: 0.87, y: 0.11 }, { x: 0.14, y: 0.28 },
    { x: 0.80, y: 0.22 }, { x: 0.93, y: 0.44 }, { x: 0.07, y: 0.53 },
    { x: 0.37, y: 0.09 }, { x: 0.62, y: 0.07 }, { x: 0.23, y: 0.74 },
    { x: 0.74, y: 0.71 }, { x: 0.47, y: 0.88 }, { x: 0.52, y: 0.19 },
  ];

  const starSizes = [4,3,5,3,4,3,4,3,5,4,3,4];

  return (
    <Animated.View style={[styles.root, containerAnim]}>

      {/* ── Background image ─────────────────────────────────────────────── */}
      <Image
        source={require('../assets/splash-eye.jpeg')}
        style={[
          styles.bg,
          Platform.OS === 'web' && ({ filter: 'brightness(1.35) saturate(1.2)' } as any),
        ]}
        resizeMode="cover"
      />

      {/* ── Edge vignette (native only — web image fills naturally) ──── */}
      {Platform.OS !== 'web' && <View style={styles.vignette} />}

      {/* ── Web: permanent radial gradient illuminates eye center ──── */}
      {Platform.OS === 'web' && (
        <View
          style={[
            StyleSheet.absoluteFill,
            {
              background: `radial-gradient(ellipse 46% 56% at ${EYE_CX}px ${EYE_CY}px, rgba(57,255,20,0.13) 0%, rgba(57,255,20,0.07) 40%, transparent 68%)`,
              pointerEvents: 'none',
            } as any,
          ]}
        />
      )}

      {/* ── Center atmospheric glow — pulsing layer (all platforms) ─── */}
      <Animated.View
        style={[
          styles.centerGlow,
          { left: EYE_CX - SHORT * 0.7, top: EYE_CY - SHORT * 0.7 },
          centerGlowAnim,
        ]}
        pointerEvents="none"
      />

      {/* ── Sonar pulse rings from eye center ────────────────────────── */}
      <SonarRing delay={0}    />
      <SonarRing delay={1200} />
      <SonarRing delay={2400} />

      {/* ── Iris rotation layer ──────────────────────────────────────── */}
      <View
        style={[
          styles.irisCenter,
          { left: EYE_CX - IRIS_R, top: EYE_CY - IRIS_R },
        ]}
        pointerEvents="none"
      >
        {/* Outer rotating ring */}
        <Animated.View style={[styles.irisRingOuter, irisAnim]}>
          {[0,1,2,3,4,5,6,7].map(i => (
            <View
              key={i}
              style={[
                styles.irisSegment,
                {
                  transform: [
                    { rotate: `${i * 45}deg` },
                    { translateX: IRIS_R * 0.82 },
                  ],
                },
              ]}
            />
          ))}
        </Animated.View>

        {/* Inner ring (counter-rotate) */}
        <Animated.View style={[styles.irisRingInner, irisAnim2]}>
          {[0,1,2,3,4,5].map(i => (
            <View
              key={i}
              style={[
                styles.irisSegmentInner,
                {
                  transform: [
                    { rotate: `${i * 60}deg` },
                    { translateX: IRIS_R * 0.52 },
                  ],
                },
              ]}
            />
          ))}
        </Animated.View>

        {/* Pupil glow pulse */}
        <Animated.View style={[styles.pupilGlow, pupilAnim]} />
      </View>

      {/* Orbiting particles around iris */}
      <View
        style={[
          styles.orbitCenter,
          { left: EYE_CX, top: EYE_CY },
        ]}
        pointerEvents="none"
      >
        <OrbitDot angle={0}   radius={IRIS_R * 1.12} duration={5000} />
        <OrbitDot angle={120} radius={IRIS_R * 1.12} duration={5000} />
        <OrbitDot angle={240} radius={IRIS_R * 1.12} duration={5000} />
        <OrbitDot angle={60}  radius={IRIS_R * 1.35} duration={8000} />
        <OrbitDot angle={200} radius={IRIS_R * 1.35} duration={8000} />
        <OrbitDot angle={310} radius={IRIS_R * 1.35} duration={8000} />
      </View>

      {/* ── Eyelids (open on mount, blink periodically) ─────────────── */}
      <View style={StyleSheet.absoluteFill} pointerEvents="none">
        {/* Top eyelid */}
        <Animated.View
          style={[
            styles.eyelid,
            styles.eyelidTop,
            topEyelidAnim,
          ]}
        />
        {/* Bottom eyelid */}
        <Animated.View
          style={[
            styles.eyelid,
            styles.eyelidBot,
            botEyelidAnim,
          ]}
        />
      </View>

      {/* ── Crown golden-green shimmer ──────────────────────────────── */}
      <Animated.View
        style={[
          styles.crownGlow,
          crownAnim,
          { top: CROWN_Y - 40 },
        ]}
        pointerEvents="none"
      />
      {/* Crown sparkle cross */}
      <Animated.View style={[styles.crownSparkle, crownAnim, { top: CROWN_Y }]} pointerEvents="none">
        <View style={styles.sparkleH} />
        <View style={styles.sparkleV} />
      </Animated.View>

      {/* ── Cloud lightning – Left ──────────────────────────────────── */}
      <RNAnimated.View style={[styles.cloudRegionL, { opacity: lightL }]} pointerEvents="none">
        <View style={[styles.boltLine, { top: 10, left: 20, transform: [{ rotate: '-25deg' }, { scaleY: 1.2 }] }]} />
        <View style={[styles.boltLine, { top: 40, left:  8, transform: [{ rotate: '-10deg' }] }]} />
        <View style={[styles.boltLine, { top: 65, left: 35, transform: [{ rotate: '-40deg' }] }]} />
        <View style={styles.cloudGlowL} />
      </RNAnimated.View>
      <RNAnimated.View style={[styles.cloudRegionL2, { opacity: lightL2 }]} pointerEvents="none">
        <View style={[styles.boltLine, { top: 25, left: 15, transform: [{ rotate: '-15deg' }] }]} />
        <View style={styles.cloudGlowL} />
      </RNAnimated.View>

      {/* ── Cloud lightning – Right ─────────────────────────────────── */}
      <RNAnimated.View style={[styles.cloudRegionR, { opacity: lightR }]} pointerEvents="none">
        <View style={[styles.boltLine, { top: 10, right: 20, transform: [{ rotate: '25deg' }, { scaleY: 1.2 }] }]} />
        <View style={[styles.boltLine, { top: 40, right:  8, transform: [{ rotate: '10deg' }] }]} />
        <View style={[styles.boltLine, { top: 65, right: 35, transform: [{ rotate: '40deg' }] }]} />
        <View style={styles.cloudGlowR} />
      </RNAnimated.View>
      <RNAnimated.View style={[styles.cloudRegionR2, { opacity: lightR2 }]} pointerEvents="none">
        <View style={[styles.boltLine, { top: 25, right: 15, transform: [{ rotate: '15deg' }] }]} />
        <View style={styles.cloudGlowR} />
      </RNAnimated.View>

      {/* ── Star sparkles ───────────────────────────────────────────── */}
      {starPos.map((pos, i) => (
        <Animated.View
          key={i}
          style={[
            styles.starDot,
            {
              left: pos.x * W - starSizes[i] / 2,
              top:  pos.y * H - starSizes[i] / 2,
              width:  starSizes[i],
              height: starSizes[i],
              borderRadius: starSizes[i] / 2,
            },
            starStyles[i],
          ]}
          pointerEvents="none"
        />
      ))}
      {/* Star cross-flares on select stars */}
      {[0,1,3,6].map(i => (
        <Animated.View
          key={`flare-${i}`}
          style={[
            styles.starFlare,
            {
              left: starPos[i].x * W - 8,
              top:  starPos[i].y * H - 8,
            },
            starStyles[i],
          ]}
          pointerEvents="none"
        >
          <View style={styles.flareH} />
          <View style={styles.flareV} />
        </Animated.View>
      ))}

      {/* ── Horizontal scan line ────────────────────────────────────── */}
      <Animated.View style={[styles.scanLine, scanAnim]} pointerEvents="none" />

      {/* ── Bottom HUD ──────────────────────────────────────────────── */}
      <View style={styles.hud} pointerEvents="none">
        <Text style={styles.hudTitle}>REVERSEPICKS</Text>
        <Text style={styles.hudTagline}>THE EYE SEES WHAT OTHERS MISS</Text>

        <View style={styles.progressTrack}>
          <Animated.View style={[styles.progressFill, progressAnim]} />
          <View style={styles.progressGlow} />
        </View>

        <Text style={styles.statusText}>{statuses[statusIdx]}</Text>
      </View>
    </Animated.View>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  root: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#010e01',
    zIndex: 999,
  },
  bg: {
    position: 'absolute',
    width: W,
    height: H,
    top: 0,
    left: 0,
  },

  // Vignette native: thick border creates a dark frame
  vignette: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'transparent',
    borderWidth: Math.min(W, H) * 0.15,
    borderColor: 'rgba(5,5,5,0.65)',
    borderRadius: 0,
  },
  // Vignette web: inset box-shadow creates a smooth radial dark edge
  vignetteWeb: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'transparent',
    ...(Platform.OS === 'web'
      ? ({ boxShadow: 'inset 0 0 220px 80px rgba(5,5,5,0.88)' } as any)
      : {}),
  },

  // Center atmospheric glow
  centerGlow: {
    position: 'absolute',
    width:  SHORT * 1.4,
    height: SHORT * 1.4,
    borderRadius: SHORT * 0.7,
    backgroundColor: 'transparent',
    ...(Platform.OS === 'web'
      ? ({ boxShadow: `0 0 ${SHORT * 0.6}px ${SHORT * 0.25}px rgba(57,255,20,0.18), 0 0 ${SHORT * 0.3}px ${SHORT * 0.1}px rgba(57,255,20,0.1)` } as any)
      : {
          shadowColor: NEON,
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 0.5,
          shadowRadius: SHORT * 0.4,
        }),
  },

  // Sonar ring
  sonarRing: {
    position: 'absolute',
    width:  IRIS_R * 2,
    height: IRIS_R * 2,
    borderRadius: IRIS_R,
    borderWidth: 1.5,
    borderColor: NEON,
  },

  // Iris
  irisCenter: {
    position: 'absolute',
    width:  IRIS_R * 2,
    height: IRIS_R * 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  irisRingOuter: {
    position: 'absolute',
    width:  IRIS_R * 2,
    height: IRIS_R * 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  irisRingInner: {
    position: 'absolute',
    width:  IRIS_R * 2,
    height: IRIS_R * 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  irisSegment: {
    position: 'absolute',
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: NEON,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 8px ${NEON}, 0 0 16px ${NEON}` }
      : { shadowColor: NEON, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 1, shadowRadius: 8 }),
  },
  irisSegmentInner: {
    position: 'absolute',
    width: 4,
    height: 4,
    borderRadius: 2,
    backgroundColor: GOLD,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 8px ${GOLD}` }
      : { shadowColor: GOLD, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 0.9, shadowRadius: 6 }),
  },
  pupilGlow: {
    position: 'absolute',
    width:  IRIS_R * 0.55,
    height: IRIS_R * 0.55,
    borderRadius: IRIS_R * 0.28,
    backgroundColor: 'transparent',
    borderWidth: 2,
    borderColor: NEON,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 20px ${NEON}, 0 0 40px rgba(57,255,20,0.3)` }
      : { shadowColor: NEON, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 0.9, shadowRadius: 18 }),
  },

  // Orbit
  orbitCenter: {
    position: 'absolute',
    width: 0,
    height: 0,
    alignItems: 'center',
    justifyContent: 'center',
  },
  orbitDot: {
    position: 'absolute',
    width: 5,
    height: 5,
    borderRadius: 2.5,
    backgroundColor: NEON,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 6px ${NEON}` }
      : { shadowColor: NEON, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 1, shadowRadius: 6 }),
  },

  // Eyelids
  eyelid: {
    position: 'absolute',
    left: 0,
    width: W,
    backgroundColor: DARK,
  },
  eyelidTop: {
    height: EYE_HALF_H,
    bottom: H - EYE_CY,
  },
  eyelidBot: {
    height: EYE_HALF_H,
    top: EYE_CY,
  },

  // Crown glow
  crownGlow: {
    position: 'absolute',
    left: W * 0.5 - 90,
    width: 180,
    height: 110,
    borderRadius: 55,
    backgroundColor: 'transparent',
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 60px rgba(57,255,20,0.7), 0 0 100px rgba(255,215,0,0.3)` }
      : {
          shadowColor: NEON,
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 0.8,
          shadowRadius: 50,
        }),
  },
  crownSparkle: {
    position: 'absolute',
    left: W * 0.5 - 1,
    width: 2,
    height: 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sparkleH: {
    position: 'absolute',
    width: 40,
    height: 1.5,
    backgroundColor: GOLD,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 8px ${GOLD}, 0 0 20px rgba(255,215,0,0.6)` }
      : { shadowColor: GOLD, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 1, shadowRadius: 8 }),
  },
  sparkleV: {
    position: 'absolute',
    width: 1.5,
    height: 48,
    backgroundColor: GOLD,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 8px ${GOLD}` }
      : { shadowColor: GOLD, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 1, shadowRadius: 8 }),
  },

  // Cloud lightning regions
  cloudRegionL: {
    position: 'absolute',
    left: 0,
    top: H * 0.52,
    width: W * 0.3,
    height: 120,
  },
  cloudRegionL2: {
    position: 'absolute',
    left: 0,
    top: H * 0.65,
    width: W * 0.25,
    height: 80,
  },
  cloudRegionR: {
    position: 'absolute',
    right: 0,
    top: H * 0.52,
    width: W * 0.3,
    height: 120,
  },
  cloudRegionR2: {
    position: 'absolute',
    right: 0,
    top: H * 0.65,
    width: W * 0.25,
    height: 80,
  },
  boltLine: {
    position: 'absolute',
    width: 2,
    height: 36,
    backgroundColor: NEON,
    borderRadius: 1,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 8px ${NEON}, 0 0 20px rgba(57,255,20,0.8)` }
      : { shadowColor: NEON, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 1, shadowRadius: 10 }),
  },
  cloudGlowL: {
    position: 'absolute',
    left: 0,
    top: 0,
    width: W * 0.3,
    height: 120,
    backgroundColor: 'transparent',
    ...(Platform.OS === 'web'
      ? { boxShadow: `inset 20px 0 60px rgba(57,255,20,0.25)` }
      : { shadowColor: NEON, shadowOffset: { width: 20, height: 0 }, shadowOpacity: 0.4, shadowRadius: 40 }),
  },
  cloudGlowR: {
    position: 'absolute',
    right: 0,
    top: 0,
    width: W * 0.3,
    height: 120,
    backgroundColor: 'transparent',
    ...(Platform.OS === 'web'
      ? { boxShadow: `inset -20px 0 60px rgba(57,255,20,0.25)` }
      : { shadowColor: NEON, shadowOffset: { width: -20, height: 0 }, shadowOpacity: 0.4, shadowRadius: 40 }),
  },

  // Stars
  starDot: {
    position: 'absolute',
    backgroundColor: NEON,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 6px ${NEON}, 0 0 12px rgba(57,255,20,0.6)` }
      : { shadowColor: NEON, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 1, shadowRadius: 6 }),
  },
  starFlare: {
    position: 'absolute',
    width: 16,
    height: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  flareH: {
    position: 'absolute',
    width: 14,
    height: 1,
    backgroundColor: NEON,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 6px ${NEON}` }
      : { shadowColor: NEON, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 1, shadowRadius: 4 }),
  },
  flareV: {
    position: 'absolute',
    width: 1,
    height: 14,
    backgroundColor: NEON,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 6px ${NEON}` }
      : { shadowColor: NEON, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 1, shadowRadius: 4 }),
  },

  // Scan line
  scanLine: {
    position: 'absolute',
    left: 0,
    width: W,
    height: 2,
    backgroundColor: NEON,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 12px ${NEON}, 0 0 30px rgba(57,255,20,0.4)` }
      : { shadowColor: NEON, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 0.8, shadowRadius: 12 }),
  },

  // HUD
  hud: {
    position: 'absolute',
    bottom: H * 0.07,
    left: 0,
    right: 0,
    alignItems: 'center',
    gap: 10,
  },
  hudTitle: {
    fontSize: 18,
    fontWeight: '900',
    color: '#ffffff',
    letterSpacing: 6,
    textTransform: 'uppercase',
    ...(Platform.OS === 'web'
      ? { textShadow: `0 0 20px rgba(57,255,20,0.5)` }
      : {}),
  },
  hudTagline: {
    fontSize: 9,
    fontWeight: '600',
    color: NEON,
    letterSpacing: 2.5,
    textTransform: 'uppercase',
    opacity: 0.75,
    marginBottom: 4,
  },
  progressTrack: {
    width: W * 0.55,
    height: 2,
    borderRadius: 1,
    backgroundColor: '#1a1a1a',
    overflow: 'hidden',
  },
  progressFill: {
    height: 2,
    backgroundColor: NEON,
    ...(Platform.OS === 'web'
      ? { boxShadow: `0 0 10px ${NEON}` }
      : { shadowColor: NEON, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 0.9, shadowRadius: 8 }),
  },
  progressGlow: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'transparent',
  },
  statusText: {
    fontSize: 8,
    fontWeight: '700',
    color: 'rgba(57,255,20,0.5)',
    letterSpacing: 2,
    textTransform: 'uppercase',
  },
});
