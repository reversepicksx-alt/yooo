import React, { useRef } from 'react';
import { TouchableOpacity, View, Text, StyleSheet } from 'react-native';
import * as Haptics from 'expo-haptics';
import ReanimatedSwipeable from 'react-native-gesture-handler/ReanimatedSwipeable';
import Reanimated, { useAnimatedStyle, interpolate, Extrapolation, SharedValue } from 'react-native-reanimated';
import Colors from '@/constants/colors';

function SwipeLeftAction({
  drag,
  onPress,
}: {
  drag: SharedValue<number>;
  onPress: () => void;
}) {
  const style = useAnimatedStyle(() => ({
    transform: [{ translateX: interpolate(drag.value, [0, 80], [-60, 0], Extrapolation.CLAMP) }],
  }));
  return (
    <Reanimated.View style={[styles.swipeAction, style]}>
      <TouchableOpacity onPress={onPress} style={styles.swipeBtn} activeOpacity={0.8}>
        <Text style={styles.swipeBtnText}>DELETE</Text>
      </TouchableOpacity>
    </Reanimated.View>
  );
}

export default function SwipeablePickRow({
  onDelete,
  children,
}: {
  onDelete: () => void;
  children: React.ReactNode;
}) {
  const swipeRef = useRef<any>(null);

  return (
    <ReanimatedSwipeable
      ref={swipeRef}
      friction={1.5}
      leftThreshold={40}
      dragOffsetFromLeftEdge={6}
      overshootLeft={false}
      enableTrackpadTwoFingerGesture
      renderLeftActions={(_progress, drag) => (
        <SwipeLeftAction
          drag={drag}
          onPress={() => {
            swipeRef.current?.close();
            onDelete();
          }}
        />
      )}
      onSwipeableWillOpen={() => {
        try { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); } catch {}
      }}
    >
      {children}
    </ReanimatedSwipeable>
  );
}

const styles = StyleSheet.create({
  swipeAction: {
    width: 80,
    justifyContent: 'center',
    alignItems: 'flex-start',
    paddingLeft: 8,
  },
  swipeBtn: {
    backgroundColor: Colors.error,
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 14,
  },
  swipeBtnText: {
    color: '#fff',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
});
