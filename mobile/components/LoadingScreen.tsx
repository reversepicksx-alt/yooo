import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Animated, StyleSheet, Platform } from 'react-native';
import Colors from '@/constants/colors';

interface LoadingScreenProps {
  /** The base label before the animated dots (e.g. "LOADING", "ANALYZING", "SCANNING") */
  label?: string;
  /** Optional sub-status messages that change as progress increases */
  statuses?: string[];
}

export default function LoadingScreen({
  label = 'LOADING',
  statuses = [
    'INITIALIZING ENGINES',
    'LOADING PLAYER DATABASE',
    'CALIBRATING PROBABILITY MODELS',
    'READY',
  ],
}: LoadingScreenProps) {
  const [progress] = useState(() => new Animated.Value(0));
  const [dots, setDots] = useState(0);
  const [currentStatus, setCurrentStatus] = useState(statuses[0]);
  const pulseScale = useRef(new Animated.Value(1)).current;
  const pulseOpacity = useRef(new Animated.Value(0.5)).current;

  // Pulse ring animation (infinite loop)
  useEffect(() => {
    const pulse = () => {
      Animated.parallel([
        Animated.timing(pulseScale, {
          toValue: 1.15,
          duration: 1000,
          useNativeDriver: true,
        }),
        Animated.timing(pulseOpacity, {
          toValue: 0,
          duration: 1000,
          useNativeDriver: true,
        }),
      ]).start(() => {
        pulseScale.setValue(1);
        pulseOpacity.setValue(0.5);
        pulse();
      });
    };
    pulse();
  }, [pulseScale, pulseOpacity]);

  // Progress bar animation
  useEffect(() => {
    let target = 0;
    const interval = setInterval(() => {
      target += Math.random() * 8 + 2;
      if (target >= 100) target = 100;
      Animated.timing(progress, {
        toValue: target,
        duration: 200,
        useNativeDriver: false,
      }).start();

      // Update status based on progress
      const pct = target / 100;
      const idx = Math.min(Math.floor(pct * statuses.length), statuses.length - 1);
      setCurrentStatus(statuses[idx]);
    }, 200);
    return () => clearInterval(interval);
  }, [progress, statuses]);

  // Dots animation
  useEffect(() => {
    const interval = setInterval(() => {
      setDots((d) => (d + 1) % 4);
    }, 500);
    return () => clearInterval(interval);
  }, []);

  const progressWidth = progress.interpolate({
    inputRange: [0, 100],
    outputRange: ['0%', '100%'],
  });

  return (
    <View style={styles.container}>
      <View style={styles.center}>
        {/* Logo with pulse ring */}
        <View style={styles.logoWrapper}>
          <View style={styles.logoBox}>
            <Text style={styles.logoText}>RP</Text>
          </View>
          <Animated.View
            style={[
              styles.pulseRing,
              {
                transform: [{ scale: pulseScale }],
                opacity: pulseOpacity,
              },
            ]}
          />
        </View>

        {/* Brand name */}
        <View style={styles.textCenter}>
          <Text style={styles.brandTitle}>REVERSEPICKS</Text>
          <Text style={styles.brandSub}>
            {label}{'.'.repeat(dots)}
          </Text>
        </View>

        {/* Progress bar */}
        <View style={styles.progressTrack}>
          <Animated.View style={[styles.progressFill, { width: progressWidth }]} />
        </View>

        {/* Status text */}
        <Text style={styles.statusText}>{currentStatus}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: Colors.background,
    zIndex: 50,
    alignItems: 'center',
    justifyContent: 'center',
  },
  center: {
    alignItems: 'center',
    gap: 32,
  },
  logoWrapper: {
    width: 80,
    height: 80,
    alignItems: 'center',
    justifyContent: 'center',
  },
  logoBox: {
    width: 80,
    height: 80,
    borderRadius: 20,
    backgroundColor: '#111111',
    borderWidth: 1,
    borderColor: '#222',
    alignItems: 'center',
    justifyContent: 'center',
    ...(Platform.OS === 'web'
      ? { boxShadow: '0 0 40px rgba(57,255,20,0.15), 0 0 80px rgba(57,255,20,0.05)' }
      : {
          shadowColor: '#39FF14',
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 0.15,
          shadowRadius: 40,
        }),
  },
  logoText: {
    fontSize: 24,
    fontWeight: '900',
    color: Colors.primary,
    letterSpacing: 1,
  },
  pulseRing: {
    position: 'absolute',
    width: 80,
    height: 80,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.3)',
  },
  textCenter: {
    alignItems: 'center',
  },
  brandTitle: {
    fontSize: 16,
    fontWeight: '900',
    color: '#ffffff',
    letterSpacing: 4,
    marginBottom: 4,
  },
  brandSub: {
    fontSize: 10,
    fontWeight: '600',
    color: Colors.textSecondary,
    letterSpacing: 2,
    minWidth: 90,
  },
  progressTrack: {
    width: 192,
    height: 3,
    borderRadius: 2,
    backgroundColor: '#1a1a1a',
    overflow: 'hidden',
  },
  progressFill: {
    height: 3,
    borderRadius: 2,
    backgroundColor: Colors.primary,
    ...(Platform.OS === 'web'
      ? { boxShadow: '0 0 10px rgba(57,255,20,0.5)' }
      : {
          shadowColor: '#39FF14',
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 0.5,
          shadowRadius: 10,
        }),
  },
  statusText: {
    fontSize: 9,
    fontWeight: '600',
    color: Colors.textTertiary,
    letterSpacing: 1.5,
    textTransform: 'uppercase',
  },
});
