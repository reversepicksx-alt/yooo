import React, { useState, useCallback } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity,
  ActivityIndicator, Alert, Platform, RefreshControl,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import Colors from '@/constants/colors';
import { listPicks, deletePick, Pick } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

type Tab = 'live' | 'history';

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Attempts', shots: 'Shots', shots_on_target: 'SOT',
  goals: 'Goals', assists: 'Assists', key_passes: 'Key Passes',
  tackles: 'Tackles', saves: 'Saves', dribbles: 'Dribbles', crosses: 'Crosses',
  interceptions: 'Interceptions', blocks: 'Blocks', fouls_drawn: 'Fouls Drawn',
  fouls_committed: 'Fouls', clearances: 'Clearances', duels_won: 'Duels Won',
  yellow_cards: 'Yellow Cards', shots_assisted: 'Shot Assists', passes: 'Passes',
};

function isLive(p: Pick) {
  return p.status === 'live' || p.status === 'pending' || (!p.status && !['hit','miss','push','won','lost'].includes(p.result));
}
function isSettled(p: Pick) {
  return p.status === 'settled' || ['hit','miss','push','won','lost'].includes(p.result);
}
function pickWon(p: Pick) {
  return p.result === 'hit' || p.result === 'won' || p.status === 'won';
}
function pickLost(p: Pick) {
  return p.result === 'miss' || p.result === 'lost' || p.status === 'lost';
}
function pickPush(p: Pick) {
  return p.result === 'push';
}

function PickCard({ pick, onDelete }: { pick: Pick; onDelete: () => void }) {
  const won = pickWon(pick);
  const lost = pickLost(pick);
  const live = isLive(pick);

  const isOver = pick.recommendation === 'OVER';
  const isUnder = pick.recommendation === 'UNDER';

  const recColor = isOver ? Colors.primary : isUnder ? Colors.error : Colors.textSecondary;
  const push = pickPush(pick);
  const statusColor = won ? Colors.success : lost ? Colors.error : push ? Colors.textSecondary : Colors.textTertiary;

  const propLabel = PROP_LABELS[pick.propType] || pick.propType?.replace(/_/g, ' ') || '—';
  const venueStr = pick.venue ? pick.venue.toUpperCase() : '';

  // Tracking bar: only use actual in-game value — never projection (match not started yet)
  const trackValue = pick.actualValue != null ? pick.actualValue : null;
  const isProjected = false;

  const trackWinning = trackValue != null && pick.line != null
    ? (isOver && trackValue > pick.line) || (isUnder && trackValue < pick.line)
    : null;
  const trackColor = won
    ? Colors.success
    : lost
    ? Colors.error
    : trackWinning === true
    ? Colors.success
    : trackWinning === false
    ? Colors.error
    : Colors.textTertiary;

  // Fill %: line sits at 50%, value scaled to 0–100% (range: 0 to 2×line)
  const trackFillPct = trackValue != null && pick.line != null && pick.line > 0
    ? Math.min(Math.max((trackValue / (pick.line * 2)) * 100, 1), 99)
    : null;

  // PACE: for settled = actual, for live = projection
  const paceVal = pick.actualValue != null
    ? Number(pick.actualValue).toFixed(0)
    : pick.projection != null
    ? Number(pick.projection).toFixed(1)
    : '—';
  const paceColor = pick.actualValue != null ? statusColor : Colors.primary;

  return (
    <View style={[styles.card, won && styles.cardWon, lost && styles.cardLost]}>
      {/* Top row */}
      <View style={styles.cardTopRow}>
        <View style={styles.cardLeft}>
          <Text style={styles.cardPlayer} numberOfLines={1}>{pick.playerName}</Text>
          <Text style={styles.cardMeta} numberOfLines={1}>
            {[pick.teamName, pick.opponentName ? `vs ${pick.opponentName}` : null, venueStr]
              .filter(Boolean).join(' · ')}
          </Text>
        </View>
        <View style={styles.cardRight}>
          {live && !won && !lost && pick.actualValue != null && (
            <View style={styles.liveBadge}>
              <View style={styles.liveDot} />
              <Text style={styles.liveText}>LIVE</Text>
            </View>
          )}
          {live && !won && !lost && pick.actualValue == null && (
            <View style={styles.pendingBadge}>
              <Ionicons name="time-outline" size={11} color={Colors.textSecondary} />
              <Text style={styles.pendingText}>PENDING</Text>
            </View>
          )}
          {won && (
            <View style={styles.wonBadge}>
              <Ionicons name="checkmark" size={11} color="#000" />
              <Text style={styles.wonText}>HIT</Text>
            </View>
          )}
          {lost && (
            <View style={styles.lostBadge}>
              <Ionicons name="close" size={11} color={Colors.error} />
              <Text style={styles.lostText}>MISS</Text>
            </View>
          )}
          {push && !won && !lost && (
            <View style={styles.pendingBadge}>
              <Ionicons name="remove-outline" size={11} color={Colors.textSecondary} />
              <Text style={styles.pendingText}>PUSH</Text>
            </View>
          )}
          <TouchableOpacity onPress={onDelete} hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}>
            <Ionicons name="trash-outline" size={16} color="rgba(255,255,255,0.2)" />
          </TouchableOpacity>
        </View>
      </View>

      {/* Pick line */}
      {pick.recommendation && (
        <View style={styles.pickRow}>
          <View style={[styles.recPill, { backgroundColor: isOver ? Colors.successDim : isUnder ? Colors.errorDim : Colors.cardSecondary }]}>
            <Text style={[styles.recPillText, { color: recColor }]}>{pick.recommendation}</Text>
          </View>
          <Text style={styles.pickDetail}>
            {propLabel} · Line {pick.line}
          </Text>
        </View>
      )}

      {/* Stats row: NOW | LINE | PACE | HIT% */}
      <View style={styles.statsRow}>
        <View style={styles.statCol}>
          <Text style={[styles.statVal, { color: pick.actualValue != null ? statusColor : Colors.textSecondary }]}>
            {pick.actualValue != null ? Number(pick.actualValue).toFixed(0) : '—'}
          </Text>
          <Text style={styles.statLbl}>NOW</Text>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.statCol}>
          <Text style={styles.statVal}>{pick.line ?? '—'}</Text>
          <Text style={styles.statLbl}>LINE</Text>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.statCol}>
          <Text style={[styles.statVal, { color: paceColor }]}>{paceVal}</Text>
          <Text style={styles.statLbl}>PACE</Text>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.statCol}>
          <Text style={styles.statVal}>—</Text>
          <Text style={styles.statLbl}>HIT%</Text>
        </View>
      </View>

      {/* Tracking bar */}
      {trackFillPct != null && (
        <View style={styles.trackBarOuter}>
          <View
            style={[
              styles.trackBarFill,
              {
                width: `${trackFillPct}%` as unknown as number,
                backgroundColor: trackColor,
                opacity: isProjected ? 0.45 : 0.85,
              },
            ]}
          />
          <View style={styles.trackBarMarker} />
        </View>
      )}

      {/* Tracking ID */}
      {pick.trackingId && (
        <Text style={styles.trackingId}>{pick.trackingId}</Text>
      )}
    </View>
  );
}

function RecordBar({ picks }: { picks: Pick[] }) {
  const hits = picks.filter(pickWon).length;
  const misses = picks.filter(pickLost).length;
  const pending = picks.filter(isLive).length;
  const settled = hits + misses;
  const winPct = settled > 0 ? Math.round((hits / settled) * 100) : null;

  let streak = 0;
  const sorted = [...picks].sort((a, b) =>
    new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime()
  );
  for (const p of sorted) {
    if (pickWon(p)) streak++;
    else if (pickLost(p)) break;
  }

  return (
    <View style={styles.recordBar}>
      <Text style={styles.recordLabel}>YOUR RECORD</Text>
      <View style={styles.recordStats}>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.success }]}>{hits}</Text>
          <Text style={styles.recordKey}>HITS</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.error }]}>{misses}</Text>
          <Text style={styles.recordKey}>MISS</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.textSecondary }]}>{pending}</Text>
          <Text style={styles.recordKey}>LIVE</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.primary }]}>
            {winPct != null ? `${winPct}%` : '—'}
          </Text>
          <Text style={styles.recordKey}>WIN%</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.accent }]}>
            {streak > 0 ? `${streak}W` : '—'}
          </Text>
          <Text style={styles.recordKey}>STRK</Text>
        </View>
      </View>
      <View style={styles.progressWrap}>
        <View style={styles.progressTrack}>
          <View
            style={[
              styles.progressFill,
              { width: `${picks.length > 0 ? Math.max(8, Math.min(100, (settled / picks.length) * 100)) : 0}%` },
            ]}
          />
        </View>
        <Text style={styles.progressText}>
          {settled}/{picks.length} settled
        </Text>
      </View>
    </View>
  );
}

export default function PicksScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const qc = useQueryClient();
  const topPad = Platform.OS === 'web' ? 67 : insets.top;
  const [activeTab, setActiveTab] = useState<Tab>('live');

  const { data: picks = [], isLoading, refetch, isRefetching } = useQuery({
    queryKey: ['picks', session?.email],
    queryFn: () => {
      if (!session) return [];
      return listPicks(session.email, session.token);
    },
    enabled: !!session,
    refetchInterval: 60000,
  });

  useFocusEffect(
    useCallback(() => {
      refetch();
    }, [refetch])
  );

  const deleteMutation = useMutation({
    mutationFn: (pickId: string) => {
      if (!session) throw new Error('Not authenticated');
      return deletePick(session.email, session.token, pickId);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['picks'] });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    },
    onError: (e: Error) => Alert.alert('Delete failed', e.message),
  });

  const handleDelete = useCallback((pick: Pick) => {
    const id = pick.pickId || pick._id || pick.id;
    if (!id) return;
    Alert.alert('Delete Pick', `Remove ${pick.playerName}?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: () => deleteMutation.mutate(id) },
    ]);
  }, [deleteMutation]);

  const live = picks.filter(isLive);
  const history = picks.filter(isSettled);
  const displayed = activeTab === 'live' ? live : history;

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.headerTitle}>My Picks</Text>
        <View style={styles.tabToggle}>
          {(['live', 'history'] as Tab[]).map(t => (
            <TouchableOpacity
              key={t}
              style={[styles.toggle, activeTab === t && styles.toggleActive]}
              onPress={() => { setActiveTab(t); Haptics.selectionAsync(); }}
            >
              {t === 'live' && live.length > 0 && activeTab !== 'live' && (
                <View style={styles.tabDot} />
              )}
              <Text style={[styles.toggleText, activeTab === t && styles.toggleTextActive]}>
                {t === 'live' ? `Live${live.length > 0 ? ` (${live.length})` : ''}` : `History${history.length > 0 ? ` (${history.length})` : ''}`}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      {/* Record bar — always show if any picks exist */}
      {picks.length > 0 && <RecordBar picks={picks} />}

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.primary} size="large" />
        </View>
      ) : displayed.length === 0 ? (
        <View style={styles.empty}>
          <Ionicons
            name={activeTab === 'live' ? 'timer-outline' : 'archive-outline'}
            size={52}
            color={Colors.textTertiary}
          />
          <Text style={styles.emptyTitle}>
            {activeTab === 'live' ? 'No live picks' : 'No settled picks yet'}
          </Text>
          <Text style={styles.emptySub}>
            {activeTab === 'live'
              ? 'Scan a prop slip and save a prediction to track it here.'
              : 'Picks move here once their game is finished and results are confirmed.'}
          </Text>
        </View>
      ) : (
        <FlatList
          data={displayed}
          keyExtractor={(item, i) => item.pickId || item._id || item.id || String(i)}
          renderItem={({ item }) => <PickCard pick={item} onDelete={() => handleDelete(item)} />}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={isRefetching}
              onRefresh={refetch}
              tintColor={Colors.primary}
            />
          }
          showsVerticalScrollIndicator={false}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: {
    paddingHorizontal: 20, paddingBottom: 12,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
  },
  headerTitle: { fontSize: 28, fontWeight: '800', color: Colors.text },
  tabToggle: {
    flexDirection: 'row', backgroundColor: Colors.card,
    borderRadius: 10, borderWidth: 1, borderColor: Colors.border, padding: 3,
  },
  toggle: { paddingVertical: 7, paddingHorizontal: 14, borderRadius: 8, flexDirection: 'row', alignItems: 'center', gap: 5 },
  toggleActive: { backgroundColor: Colors.primary },
  toggleText: { fontSize: 13, fontWeight: '600', color: Colors.textSecondary },
  toggleTextActive: { color: '#000' },
  tabDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: Colors.primary },
  progressWrap: { gap: 6, marginTop: 2 },
  progressTrack: {
    height: 6,
    borderRadius: 999,
    backgroundColor: Colors.cardSecondary,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%' as unknown as number,
    borderRadius: 999,
    backgroundColor: Colors.primary,
  },
  progressText: { fontSize: 10, color: Colors.textTertiary, fontWeight: '600', textAlign: 'right' },

  recordBar: {
    marginHorizontal: 20, marginBottom: 12, backgroundColor: Colors.card,
    borderRadius: Colors.radius, borderWidth: 1, borderColor: Colors.border, padding: 14, gap: 8,
  },
  recordLabel: { fontSize: 10, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 1.5 },
  recordStats: { flexDirection: 'row', justifyContent: 'space-between' },
  recordStat: { alignItems: 'center', flex: 1 },
  recordVal: { fontSize: 18, fontWeight: '800', color: Colors.text },
  recordKey: { fontSize: 9, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.5, marginTop: 2 },

  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12, paddingHorizontal: 40 },
  emptyTitle: { fontSize: 18, fontWeight: '700', color: Colors.text },
  emptySub: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center', lineHeight: 21 },
  list: { paddingHorizontal: 20, paddingBottom: 40, gap: 10 },

  card: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    padding: 16, borderWidth: 1, borderColor: Colors.borderSubtle, gap: 10,
  },
  cardWon: { borderColor: 'rgba(57,255,20,0.3)' },
  cardLost: { borderColor: 'rgba(255,59,48,0.25)' },

  cardTopRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  cardLeft: { flex: 1, marginRight: 10 },
  cardRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  cardPlayer: { fontSize: 16, fontWeight: '700', color: Colors.text, marginBottom: 3 },
  cardMeta: { fontSize: 11, color: Colors.textTertiary, letterSpacing: 0.3 },

  liveBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: 'rgba(255,255,255,0.06)', borderRadius: 6,
    paddingHorizontal: 8, paddingVertical: 4,
  },
  liveDot: { width: 5, height: 5, borderRadius: 3, backgroundColor: Colors.primary },
  liveText: { fontSize: 9, color: Colors.primary, fontWeight: '700', letterSpacing: 0.5 },
  pendingBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: 'rgba(255,255,255,0.04)', borderRadius: 6,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
    paddingHorizontal: 8, paddingVertical: 4,
  },
  pendingText: { fontSize: 9, color: Colors.textSecondary, fontWeight: '600', letterSpacing: 0.5 },
  wonBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: Colors.primary, borderRadius: 6,
    paddingHorizontal: 8, paddingVertical: 4,
  },
  wonText: { fontSize: 9, color: '#000', fontWeight: '800', letterSpacing: 0.5 },
  lostBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: Colors.errorDim, borderRadius: 6,
    paddingHorizontal: 8, paddingVertical: 4,
    borderWidth: 1, borderColor: 'rgba(255,59,48,0.3)',
  },
  lostText: { fontSize: 9, color: Colors.error, fontWeight: '800', letterSpacing: 0.5 },

  pickRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  recPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6 },
  recPillText: { fontSize: 12, fontWeight: '800', letterSpacing: 0.5 },
  pickDetail: { fontSize: 13, color: Colors.textSecondary, flex: 1 },

  statsRow: { flexDirection: 'row', alignItems: 'center', backgroundColor: Colors.cardSecondary, borderRadius: 10, padding: 2 },
  statCol: { flex: 1, alignItems: 'center', paddingVertical: 10, gap: 3 },
  statVal: { fontSize: 16, fontWeight: '700', color: Colors.text },
  statLbl: { fontSize: 9, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 1 },
  statDivider: { width: 1, height: 32, backgroundColor: Colors.borderSubtle },

  trackBarOuter: {
    height: 5,
    backgroundColor: Colors.cardSecondary,
    borderRadius: 3,
    overflow: 'hidden',
    position: 'relative',
  },
  trackBarFill: {
    position: 'absolute',
    left: 0,
    top: 0,
    height: '100%' as unknown as number,
    borderRadius: 3,
  },
  trackBarMarker: {
    position: 'absolute',
    left: '50%' as unknown as number,
    top: 0,
    width: 2,
    height: '100%' as unknown as number,
    backgroundColor: 'rgba(255,255,255,0.35)',
    transform: [{ translateX: -1 }],
  },

  trackingId: { fontSize: 9, color: Colors.textTertiary, textAlign: 'right', letterSpacing: 0.5 },
});
