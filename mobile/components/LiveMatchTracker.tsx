import React, { useState, useEffect, useRef } from 'react';
import { View, Text, StyleSheet, Modal, TouchableOpacity, ScrollView, Animated, Dimensions } from 'react-native';
import Svg, { Rect, Circle, Line, Path, Defs, RadialGradient, Stop } from 'react-native-svg';
import { Ionicons } from '@expo/vector-icons';
import { Pick } from '@/lib/api';
import Colors from '@/constants/colors';

interface LiveMatchTrackerProps {
  pick: Pick;
  visible: boolean;
  onClose: () => void;
}

const SCREEN_WIDTH = Dimensions.get('window').width;

const MOCK_EVENTS = [
  { id: '1', time: "12'", type: "goal", team: "home", text: "Goal - L. Messi" },
  { id: '2', time: "34'", type: "yellow", team: "away", text: "Yellow Card - S. Ramos" },
  { id: '3', time: "45'", type: "half", text: "Half Time" },
  { id: '4', time: "67'", type: "sub", team: "home", text: "Substitution - Home Team" },
  { id: '5', time: "78'", type: "red", team: "away", text: "Red Card - Casemiro" },
];

export default function LiveMatchTracker({ pick, visible, onClose }: LiveMatchTrackerProps) {
  const [matchTime, setMatchTime] = useState(78);
  const pulseAnim = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulseAnim, {
          toValue: 0.4,
          duration: 1000,
          useNativeDriver: true,
        }),
        Animated.timing(pulseAnim, {
          toValue: 1,
          duration: 1000,
          useNativeDriver: true,
        }),
      ])
    ).start();
  }, [pulseAnim]);

  const currentValue = pick.currentValue ?? pick.actualValue ?? 0;
  const progressPercent = Math.min((currentValue / (pick.line || 1)) * 100, 100);
  const isCovering = currentValue >= pick.line;

  const homeTeam = pick.teamName || "Home";
  const awayTeam = pick.opponentName || "Away";

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={styles.container}>
        {/* Header */}
        <View style={styles.header}>
          <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
            <Ionicons name="chevron-down" size={28} color={Colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Live Match Tracker</Text>
          <View style={styles.headerSpacer} />
        </View>

        <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
          {/* Live Score */}
          <View style={styles.scoreBoard}>
            <View style={styles.teamScore}>
              <Text style={styles.teamName} numberOfLines={1}>{homeTeam}</Text>
              <Text style={styles.scoreText}>2</Text>
            </View>
            <View style={styles.timeContainer}>
              <Animated.View style={[styles.liveDot, { opacity: pulseAnim }]} />
              <Text style={styles.timeText}>{matchTime}'</Text>
            </View>
            <View style={styles.teamScore}>
              <Text style={styles.scoreText}>1</Text>
              <Text style={styles.teamName} numberOfLines={1}>{awayTeam}</Text>
            </View>
          </View>

          {/* Mini Pitch */}
          <View style={styles.pitchContainer}>
            <Text style={styles.sectionTitle}>Field Activity</Text>
            <View style={styles.pitchWrapper}>
              <Svg width="100%" height={200} viewBox="0 0 100 65">
                <Defs>
                  <RadialGradient id="heat1" cx="50%" cy="50%" rx="50%" ry="50%">
                    <Stop offset="0%" stopColor={Colors.primary} stopOpacity="0.4" />
                    <Stop offset="100%" stopColor={Colors.primary} stopOpacity="0" />
                  </RadialGradient>
                  <RadialGradient id="heat2" cx="50%" cy="50%" rx="50%" ry="50%">
                    <Stop offset="0%" stopColor={Colors.error} stopOpacity="0.4" />
                    <Stop offset="100%" stopColor={Colors.error} stopOpacity="0" />
                  </RadialGradient>
                </Defs>

                {/* Field Background */}
                <Rect x="0" y="0" width="100" height="65" fill="#0A150A" />
                
                {/* Field Lines */}
                <Rect x="0" y="0" width="100" height="65" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Line x1="50" y1="0" x2="50" y2="65" stroke={Colors.border} strokeWidth="0.5" />
                <Circle cx="50" cy="32.5" r="9.15" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Circle cx="50" cy="32.5" r="0.5" fill={Colors.border} />
                
                {/* Left Penalty Area */}
                <Rect x="0" y="13.84" width="16.5" height="37.32" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Rect x="0" y="24.84" width="5.5" height="15.32" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Circle cx="11" cy="32.5" r="0.5" fill={Colors.border} />
                <Path d="M 16.5 25.5 A 9.15 9.15 0 0 1 16.5 39.5" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                
                {/* Right Penalty Area */}
                <Rect x="83.5" y="13.84" width="16.5" height="37.32" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Rect x="94.5" y="24.84" width="5.5" height="15.32" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Circle cx="89" cy="32.5" r="0.5" fill={Colors.border} />
                <Path d="M 83.5 25.5 A 9.15 9.15 0 0 0 83.5 39.5" stroke={Colors.border} strokeWidth="0.5" fill="none" />

                {/* Mock Heat Map for Player */}
                <Circle cx="70" cy="20" r="15" fill="url(#heat1)" />
                <Circle cx="80" cy="35" r="20" fill="url(#heat1)" />
                <Circle cx="65" cy="45" r="12" fill="url(#heat1)" />

                {/* Mock Player Positions */}
                <Circle cx="60" cy="25" r="1.5" fill={Colors.primary} />
                <Circle cx="72" cy="32" r="1.5" fill={Colors.primary} />
                <Circle cx="80" cy="40" r="1.5" fill={Colors.primary} />
                
                <Circle cx="65" cy="30" r="1.5" fill={Colors.error} />
                <Circle cx="75" cy="45" r="1.5" fill={Colors.error} />
                <Circle cx="85" cy="20" r="1.5" fill={Colors.error} />

                {/* Ball */}
                <Circle cx="72" cy="32" r="0.8" fill="#FFFFFF" />
              </Svg>
            </View>
          </View>

          {/* Possession Bar */}
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Possession</Text>
            <View style={styles.possessionRow}>
              <Text style={styles.possessionText}>60%</Text>
              <View style={styles.possessionBar}>
                <View style={[styles.possessionFill, { width: '60%', backgroundColor: Colors.primary }]} />
                <View style={[styles.possessionFill, { width: '40%', backgroundColor: Colors.error }]} />
              </View>
              <Text style={styles.possessionText}>40%</Text>
            </View>
          </View>

          {/* Player Stat vs Line Progress */}
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>{pick.playerName} - {pick.propType}</Text>
            <View style={styles.progressHeader}>
              <Text style={styles.progressValues}>
                <Text style={styles.currentValue}>{currentValue}</Text> / {pick.line}
              </Text>
              {isCovering && (
                <View style={styles.coveringBadge}>
                  <Ionicons name="checkmark-circle" size={14} color={Colors.success} />
                  <Text style={styles.coveringText}>Covering</Text>
                </View>
              )}
            </View>
            <View style={styles.progressBarBg}>
              <View 
                style={[
                  styles.progressBarFill, 
                  { 
                    width: `${progressPercent}%`,
                    backgroundColor: isCovering ? Colors.success : Colors.primary
                  }
                ]} 
              />
            </View>
          </View>

          {/* Key Events Timeline */}
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Key Events</Text>
            <View style={styles.timeline}>
              {MOCK_EVENTS.map((event, index) => {
                const isLast = index === MOCK_EVENTS.length - 1;
                return (
                  <View key={event.id} style={styles.timelineItem}>
                    <View style={styles.timelineLeft}>
                      <Text style={styles.timelineTime}>{event.time}</Text>
                    </View>
                    <View style={styles.timelineCenter}>
                      <View style={[
                        styles.timelineDot,
                        event.type === 'goal' && { backgroundColor: Colors.success },
                        event.type === 'red' && { backgroundColor: Colors.error },
                        event.type === 'yellow' && { backgroundColor: '#FFD60A' },
                        event.type === 'sub' && { backgroundColor: Colors.push },
                        event.type === 'half' && { backgroundColor: Colors.textSecondary },
                      ]}>
                        {event.type === 'goal' && <Ionicons name="football" size={10} color="#000" />}
                      </View>
                      {!isLast && <View style={styles.timelineLine} />}
                    </View>
                    <View style={styles.timelineRight}>
                      <Text style={styles.timelineText}>{event.text}</Text>
                    </View>
                  </View>
                );
              })}
            </View>
          </View>

        </ScrollView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingTop: 60,
    paddingBottom: 20,
    paddingHorizontal: 20,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
    backgroundColor: Colors.backgroundSecondary,
  },
  closeBtn: {
    padding: 4,
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: Colors.text,
  },
  headerSpacer: {
    width: 36,
  },
  scrollContent: {
    padding: 20,
    paddingBottom: 60,
  },
  scoreBoard: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    padding: 24,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  teamScore: {
    alignItems: 'center',
    flex: 1,
  },
  teamName: {
    fontSize: 14,
    fontWeight: '600',
    color: Colors.textSecondary,
    marginBottom: 8,
    textAlign: 'center',
  },
  scoreText: {
    fontSize: 36,
    fontWeight: '800',
    color: Colors.text,
  },
  timeContainer: {
    alignItems: 'center',
    paddingHorizontal: 20,
  },
  liveDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: Colors.error,
    marginBottom: 6,
  },
  timeText: {
    fontSize: 18,
    fontWeight: '700',
    color: Colors.primary,
  },
  pitchContainer: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    padding: 20,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  pitchWrapper: {
    marginTop: 12,
    borderRadius: 8,
    overflow: 'hidden',
    backgroundColor: '#0A150A',
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: Colors.text,
    marginBottom: 12,
  },
  card: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    padding: 20,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  possessionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  possessionBar: {
    flex: 1,
    height: 8,
    backgroundColor: Colors.cardSecondary,
    borderRadius: 4,
    marginHorizontal: 12,
    flexDirection: 'row',
    overflow: 'hidden',
  },
  possessionFill: {
    height: '100%',
  },
  possessionText: {
    fontSize: 14,
    fontWeight: '700',
    color: Colors.text,
    width: 36,
    textAlign: 'center',
  },
  progressHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    marginBottom: 12,
  },
  progressValues: {
    fontSize: 16,
    fontWeight: '600',
    color: Colors.textSecondary,
  },
  currentValue: {
    fontSize: 24,
    fontWeight: '800',
    color: Colors.text,
  },
  coveringBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.successDim,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    gap: 4,
  },
  coveringText: {
    color: Colors.success,
    fontSize: 12,
    fontWeight: '700',
  },
  progressBarBg: {
    height: 12,
    backgroundColor: Colors.cardSecondary,
    borderRadius: 6,
    overflow: 'hidden',
  },
  progressBarFill: {
    height: '100%',
    borderRadius: 6,
  },
  timeline: {
    marginTop: 8,
  },
  timelineItem: {
    flexDirection: 'row',
    marginBottom: 0,
  },
  timelineLeft: {
    width: 40,
    alignItems: 'flex-end',
    paddingRight: 12,
    paddingTop: 2,
  },
  timelineTime: {
    fontSize: 13,
    fontWeight: '600',
    color: Colors.textSecondary,
  },
  timelineCenter: {
    width: 20,
    alignItems: 'center',
  },
  timelineDot: {
    width: 14,
    height: 14,
    borderRadius: 7,
    backgroundColor: Colors.primary,
    borderWidth: 2,
    borderColor: Colors.card,
    zIndex: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  timelineLine: {
    width: 2,
    height: 40,
    backgroundColor: Colors.borderSubtle,
    marginTop: -2,
    marginBottom: -2,
  },
  timelineRight: {
    flex: 1,
    paddingLeft: 12,
    paddingBottom: 24,
    paddingTop: 1,
  },
  timelineText: {
    fontSize: 14,
    color: Colors.text,
    fontWeight: '500',
  },
});
