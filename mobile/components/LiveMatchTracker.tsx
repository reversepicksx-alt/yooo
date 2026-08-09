import React, { useState, useEffect, useRef, useCallback } from 'react';
import { View, Text, StyleSheet, Modal, TouchableOpacity, ScrollView, Animated, Dimensions, ActivityIndicator } from 'react-native';
import Svg, { Rect, Circle, Line, Path, Defs, RadialGradient, Stop } from 'react-native-svg';
import { Ionicons } from '@expo/vector-icons';
import { Pick, fetchFixtureEvents, LiveEvent } from '@/lib/api';
import Colors from '@/constants/colors';

interface LiveMatchTrackerProps {
  pick: Pick;
  visible: boolean;
  onClose: () => void;
}

const SCREEN_WIDTH = Dimensions.get('window').width;

export default function LiveMatchTracker({ pick, visible, onClose }: LiveMatchTrackerProps) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [loadingEvents, setLoadingEvents] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);
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

  const loadEvents = useCallback(async () => {
    if (!pick.fixtureId) return;
    setLoadingEvents(true);
    setEventsError(null);
    try {
      const data = await fetchFixtureEvents(pick.fixtureId);
      setEvents(data.events || []);
    } catch (e) {
      setEventsError('Could not load live events');
    } finally {
      setLoadingEvents(false);
    }
  }, [pick.fixtureId]);

  useEffect(() => {
    if (visible) {
      loadEvents();
      // Keep the event timeline moving while the modal is open.  The pick
      // itself is refreshed by the parent list query and arrives as a new prop.
      const timer = setInterval(loadEvents, 30000);
      return () => clearInterval(timer);
    } else {
      setEvents([]);
      setEventsError(null);
    }
  }, [visible, loadEvents]);

  const currentValue = pick.currentValue ?? pick.actualValue ?? 0;
  const progressPercent = Math.min((currentValue / (pick.line || 1)) * 100, 100);
  const isCovering = currentValue >= pick.line;

  const homeTeam = pick.homeTeam || pick.teamName || 'Home';
  const awayTeam = pick.awayTeam || pick.opponentName || 'Away';

  // Parse matchScore "2 - 1" or "2-1"
  const scoreParts = (pick.matchScore || '0 - 0').split(/\s*-\s*/);
  const homeGoals = parseInt(scoreParts[0] || '0', 10) || 0;
  const awayGoals = parseInt(scoreParts[1] || '0', 10) || 0;

  const isFinal = pick.matchStatus === 'final' || pick.status === 'settled' || ['hit', 'miss', 'push', 'dnp'].includes(pick.result || '');
  const isLive = !isFinal && (pick.matchStatus === 'live' || pick.status === 'live' || pick.status === 'pending');
  const elapsed = pick.elapsed ?? 0;
  const matchTime = isFinal ? 'FT' : elapsed ? `${pick.period || ''}${elapsed}'` : pick.matchStatus ? pick.matchStatus.toUpperCase() : 'LIVE';

  const homePoss = pick.homePoss ?? pick.projHomePoss ?? 50;
  const awayPoss = pick.awayPoss ?? pick.projAwayPoss ?? (100 - homePoss);
  const normalizedHomePoss = homePoss / (homePoss + awayPoss || 100) * 100;
  const normalizedAwayPoss = 100 - normalizedHomePoss;

  const pace = pick.pace ?? 0;
  const hitPct = pick.hitPct ?? 0;

  // Build timeline from real events + inferred match boundaries
  const timeline: LiveEvent[] = [];
  if (!isLive && !isFinal && events.length === 0) {
    timeline.push({ id: 'pre', time: '0\'', type: 'start', text: 'Match scheduled' });
  } else {
    timeline.push({ id: 'start', time: '0\'', type: 'start', text: 'Kick-off' });
    if (isLive && elapsed >= 45) {
      timeline.push({ id: 'ht', time: '45\'', type: 'half', text: 'Half-time' });
    }
    if (events.length > 0) {
      events.forEach((ev, idx) => {
        timeline.push({
          id: `${ev.elapsed}-${idx}`,
          time: ev.time,
          type: ev.type,
          text: formatEventText(ev),
        });
      });
    } else if (isLive) {
      timeline.push({ id: 'live', time: matchTime, type: 'info', text: 'No major events reported yet' });
    }
    if (isFinal) {
      timeline.push({ id: 'ft', time: 'FT', type: 'half', text: `Full-time: ${homeGoals} - ${awayGoals}` });
    }
  }

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={styles.container}>
        <View style={styles.header}>
          <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
            <Ionicons name="chevron-down" size={28} color={Colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Live Match Tracker</Text>
          <View style={styles.headerSpacer} />
        </View>
        {pick.fixtureId != null && (
          <View style={styles.matchIdBanner}>
            <Text style={styles.matchIdText}>MATCH ID {pick.fixtureId}</Text>
          </View>
        )}

        <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
          <View style={styles.scoreBoard}>
            <View style={styles.teamScore}>
              <Text style={styles.teamName} numberOfLines={1}>{homeTeam}</Text>
              <Text style={styles.scoreText}>{homeGoals}</Text>
            </View>
            <View style={styles.timeContainer}>
              {isLive && <Animated.View style={[styles.liveDot, { opacity: pulseAnim }]} />}
              <Text style={[styles.timeText, isFinal && { color: Colors.textSecondary }]}>{matchTime}</Text>
            </View>
            <View style={styles.teamScore}>
              <Text style={styles.scoreText}>{awayGoals}</Text>
              <Text style={styles.teamName} numberOfLines={1}>{awayTeam}</Text>
            </View>
          </View>

          <View style={styles.pitchContainer}>
            <Text style={styles.sectionTitle}>Field Activity</Text>
            <View style={styles.pitchWrapper}>
              <Svg width="100%" height={200} viewBox="0 0 100 65">
                <Defs>
                  <RadialGradient id="heatHome" cx="50%" cy="50%" rx="50%" ry="50%">
                    <Stop offset="0%" stopColor={Colors.primary} stopOpacity="0.25" />
                    <Stop offset="100%" stopColor={Colors.primary} stopOpacity="0" />
                  </RadialGradient>
                  <RadialGradient id="heatAway" cx="50%" cy="50%" rx="50%" ry="50%">
                    <Stop offset="0%" stopColor={Colors.error} stopOpacity="0.25" />
                    <Stop offset="100%" stopColor={Colors.error} stopOpacity="0" />
                  </RadialGradient>
                </Defs>

                <Rect x="0" y="0" width="100" height="65" fill="#0A150A" />
                <Rect x="0" y="0" width="100" height="65" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Line x1="50" y1="0" x2="50" y2="65" stroke={Colors.border} strokeWidth="0.5" />
                <Circle cx="50" cy="32.5" r="9.15" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Circle cx="50" cy="32.5" r="0.5" fill={Colors.border} />
                <Rect x="0" y="13.84" width="16.5" height="37.32" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Rect x="0" y="24.84" width="5.5" height="15.32" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Circle cx="11" cy="32.5" r="0.5" fill={Colors.border} />
                <Path d="M 16.5 25.5 A 9.15 9.15 0 0 1 16.5 39.5" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Rect x="83.5" y="13.84" width="16.5" height="37.32" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Rect x="94.5" y="24.84" width="5.5" height="15.32" stroke={Colors.border} strokeWidth="0.5" fill="none" />
                <Circle cx="89" cy="32.5" r="0.5" fill={Colors.border} />
                <Path d="M 83.5 25.5 A 9.15 9.15 0 0 0 83.5 39.5" stroke={Colors.border} strokeWidth="0.5" fill="none" />

                {/* Possession heat zones — left/right biased by actual possession split */}
                <Circle cx={35 - normalizedHomePoss * 0.15} cy="32.5" r={10 + normalizedHomePoss * 0.12} fill="url(#heatHome)" />
                <Circle cx={65 + normalizedAwayPoss * 0.15} cy="32.5" r={10 + normalizedAwayPoss * 0.12} fill="url(#heatAway)" />

                {/* Player dot — always in the attacking half on the team with more possession */}
                <Circle cx={homePoss >= awayPoss ? 65 : 35} cy="32.5" r="2" fill={Colors.primary} />
              </Svg>
            </View>
          </View>

          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Possession</Text>
            <View style={styles.possessionRow}>
              <Text style={styles.possessionText}>{Math.round(normalizedHomePoss)}%</Text>
              <View style={styles.possessionBar}>
                <View style={[styles.possessionFill, { width: `${normalizedHomePoss}%`, backgroundColor: Colors.primary }]} />
                <View style={[styles.possessionFill, { width: `${normalizedAwayPoss}%`, backgroundColor: Colors.error }]} />
              </View>
              <Text style={styles.possessionText}>{Math.round(normalizedAwayPoss)}%</Text>
            </View>
            {(pick.homePoss || pick.awayPoss) ? (
              <Text style={styles.possessionNote}>Live stats from the fixture</Text>
            ) : (
              <Text style={styles.possessionNote}>Projected possession — live data unavailable</Text>
            )}
          </View>

          <View style={styles.card}>
            <Text style={styles.sectionTitle}>{pick.playerName} - {pick.propType}</Text>
            <View style={styles.progressHeader}>
              <Text style={styles.progressValues}>
                <Text style={styles.currentValue}>{currentValue}</Text> / {pick.line}
              </Text>
              {isCovering ? (
                <View style={styles.coveringBadge}>
                  <Ionicons name="checkmark-circle" size={14} color={Colors.success} />
                  <Text style={styles.coveringText}>Covering</Text>
                </View>
              ) : isLive ? (
                <View style={styles.behindBadge}>
                  <Text style={styles.behindText}>Behind</Text>
                </View>
              ) : null}
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

            {(pace > 0 || hitPct > 0) && (
              <View style={styles.paceRow}>
                {pace > 0 && (
                  <View style={styles.paceItem}>
                    <Text style={styles.paceLabel}>Pace</Text>
                    <Text style={styles.paceValue}>{pace.toFixed(1)}x</Text>
                  </View>
                )}
                {hitPct > 0 && (
                  <View style={styles.paceItem}>
                    <Text style={styles.paceLabel}>Live Hit %</Text>
                    <Text style={styles.paceValue}>{Math.round(hitPct)}%</Text>
                  </View>
                )}
                {pick.paceMismatch && (
                  <View style={[styles.paceItem, styles.paceWarning]}>
                    <Ionicons name="warning" size={12} color={Colors.warning} />
                    <Text style={styles.paceWarningText}>{pick.paceWarning || 'Pace mismatch'}</Text>
                  </View>
                )}
              </View>
            )}
          </View>

          <View style={styles.card}>
            <View style={styles.eventsHeader}>
              <Text style={styles.sectionTitle}>Key Events</Text>
              {pick.fixtureId && (
                <TouchableOpacity onPress={loadEvents} disabled={loadingEvents}>
                  {loadingEvents ? (
                    <ActivityIndicator size="small" color={Colors.primary} />
                  ) : (
                    <Ionicons name="refresh" size={18} color={Colors.primary} />
                  )}
                </TouchableOpacity>
              )}
            </View>

            {eventsError && (
              <Text style={styles.eventsError}>{eventsError}</Text>
            )}

            {!pick.fixtureId && isLive && (
              <Text style={styles.eventsEmpty}>Fixture link not yet assigned. Live events will appear once the match is matched.</Text>
            )}

            <View style={styles.timeline}>
              {timeline.map((event, index) => {
                const isLast = index === timeline.length - 1;
                return (
                  <View key={event.id} style={styles.timelineItem}>
                    <View style={styles.timelineLeft}>
                      <Text style={styles.timelineTime}>{event.time}</Text>
                    </View>
                    <View style={styles.timelineCenter}>
                      <View style={[
                        styles.timelineDot,
                        event.type === 'goal' && { backgroundColor: Colors.success },
                        event.type === 'own_goal' && { backgroundColor: Colors.error },
                        event.type === 'penalty' && { backgroundColor: Colors.success },
                        event.type === 'red' && { backgroundColor: Colors.error },
                        event.type === 'yellow' && { backgroundColor: '#FFD60A' },
                        event.type === 'sub' && { backgroundColor: Colors.push },
                        event.type === 'half' && { backgroundColor: Colors.textSecondary },
                        event.type === 'start' && { backgroundColor: Colors.primary },
                        event.type === 'var' && { backgroundColor: Colors.warning },
                        event.type === 'injury' && { backgroundColor: Colors.error },
                      ]}>
                        {(event.type === 'goal' || event.type === 'penalty') && <Ionicons name="football" size={10} color="#000" />}
                        {event.type === 'own_goal' && <Text style={styles.ogIcon}>OG</Text>}
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

function formatEventText(ev: LiveEvent): string {
  switch (ev.type) {
    case 'goal':
      return ev.assistName ? `Goal — ${ev.playerName} (assist: ${ev.assistName})` : `Goal — ${ev.playerName}`;
    case 'own_goal':
      return `Own Goal — ${ev.playerName}`;
    case 'penalty':
      return `Penalty Goal — ${ev.playerName}`;
    case 'yellow':
      return `Yellow Card — ${ev.playerName}`;
    case 'red':
      return `Red Card — ${ev.playerName}`;
    case 'sub':
      return ev.assistName ? `Sub — ${ev.playerName} ↔ ${ev.assistName}` : `Sub — ${ev.playerName}`;
    case 'var':
      return `VAR — ${ev.detail || 'Review'}`;
    case 'injury':
      return `Injury — ${ev.playerName}`;
    default:
      return ev.detail || `${ev.type} — ${ev.playerName}`;
  }
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
  matchIdBanner: {
    alignItems: 'center',
    paddingVertical: 8,
    backgroundColor: 'rgba(57,255,20,0.06)',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(57,255,20,0.14)',
  },
  matchIdText: {
    color: Colors.primary,
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.8,
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
  possessionNote: {
    fontSize: 12,
    color: Colors.textSecondary,
    marginTop: 8,
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
  behindBadge: {
    backgroundColor: Colors.errorDim,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  behindText: {
    color: Colors.error,
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
  paceRow: {
    flexDirection: 'row',
    marginTop: 16,
    gap: 12,
  },
  paceItem: {
    backgroundColor: Colors.background,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  paceLabel: {
    fontSize: 12,
    color: Colors.textSecondary,
    fontWeight: '600',
  },
  paceValue: {
    fontSize: 14,
    color: Colors.text,
    fontWeight: '700',
  },
  paceWarning: {
    backgroundColor: Colors.warningDim,
  },
  paceWarningText: {
    fontSize: 12,
    color: Colors.warning,
    fontWeight: '700',
  },
  eventsHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  eventsError: {
    fontSize: 13,
    color: Colors.error,
    marginBottom: 12,
  },
  eventsEmpty: {
    fontSize: 13,
    color: Colors.textSecondary,
    marginBottom: 12,
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
  ogIcon: {
    fontSize: 8,
    fontWeight: '800',
    color: '#000',
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
