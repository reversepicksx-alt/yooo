import React from 'react';
import { View, StyleSheet, ViewStyle } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import Colors from '@/constants/colors';

interface GlassPanelProps {
  children: React.ReactNode;
  style?: ViewStyle | ViewStyle[];
  accentColor?: string;
  radius?: number;
}

export default function GlassPanel({ children, style, accentColor = Colors.primary, radius = 24 }: GlassPanelProps) {
  return (
    <View style={[styles.outer, { borderRadius: radius, borderColor: accentColor + '2A' }, style]}>
      <LinearGradient
        colors={['rgba(255,255,255,0.05)', 'rgba(255,255,255,0.00)']}
        start={{ x: 0, y: 0 }}
        end={{ x: 0, y: 0.6 }}
        style={[StyleSheet.absoluteFillObject, { borderRadius: radius }]}
      />
      <LinearGradient
        colors={['rgba(14,14,16,0.92)', 'rgba(6,6,7,0.97)']}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[StyleSheet.absoluteFillObject, { borderRadius: radius, zIndex: -1 }]}
      />
      <View style={[styles.topSheen, { backgroundColor: accentColor }]} />
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  outer: {
    borderWidth: 1,
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.45,
    shadowRadius: 20,
    elevation: 6,
  },
  topSheen: {
    position: 'absolute',
    top: 0, left: 18, right: 18,
    height: 1.5,
    opacity: 0.5,
    borderRadius: 2,
  },
});
