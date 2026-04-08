import React, { useState, useCallback } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity,
  ActivityIndicator, Alert, Platform, RefreshControl,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
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
  yellow_cards: 'Yellow Cards', shots_assisted: 'Shot Assists',
};

function abbreviateName(name: string): string {
  const parts = name.trim().split(' ');
  if (parts.length < 2) return name;
  return `${parts[0][0]}. ${parts.slice(1).join(' ')}`;
}

function PickCard({ pick, onDelete }: { pick: Pick; onDelete: () => void }) {
  const isOver = pick.recommendation === 'OVER';
  const isUnder = pick.recommendation === 'UNDER';
  const isWon = pick.status === 'won';
  const isLost = pick.status === 'lost';
  const isPending = !pick.status || pick.status === 'pending';

  const recColor = isOver ? '#39FF14' : isUnder ? Colors.error : Colors.textSecondary;
  const statusColor = isWon ? Colors.success : isLost ? Colors.error : Colors.textTertiary;

  const venueStr = (pick as unknown as Record<string, string>).venue?.toUpperCase() === 'HOME' ? 'HOME' : 'AWAY';

  const confPct = pick.confidence != null
    ? (pick.confidence > 1 ? Math.round(pick.confidence) : Math.round(pick.confidence * 100))
    : null;

  const trackingId = (pick as unknown as Record<string, string>).trackingId;

  return (
    <View style={styles.card}>
      <View style={styles.cardTopRow}>
        <View style={styles.cardLeft}>
          <Text style={styles.cardPlayer} numberOfLines={1}>
            {abbreviateName(pick.playerName)}
          </Text>
          <Text style={styles.cardMeta} numberOfLines={1}>
            {[pick.teamName, venueStr, 'SOCCER'].filter(Boolean).join(' · ')}
            {trackingId ? `  ${trackingId}` : ''}
          </Text>
        </View>
        <View style={styles.cardRight}>
          {isPending && (
            <View style={styles.schedBadge}>
              <Text style={styles.schedText}>SCHED</Text>
            </View>
          )}
          {isWon && <Ionicons name="checkmark-circle" size={18} color={Colors.success} />}
          {isLost && <Ionicons name="close-circle" size={18} color={Colors.error} />}
          <TouchableOpacity onPress={onDelete} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Ionicons name="trash-outline" size={16} color="rgba(255,255,255,0.2)" />
          </TouchableOpacity>
        </View>
      </View>

      {pick.recommendation && (
        <Text style={[styles.pickLabel, { color: recColor }]}>
          PICK: {pick.recommendation} {pick.line} {PROP_LABELS[pick.propType] || pick.propType?.replace(/_/g, ' ')}
        </Text>
      )}

      <View style={styles.statsRow}>
        <View style={styles.statCol}>
          <Text style={[styles.statVal, { color: Colors.primary }]}>
            {pick.projection != null ? pick.projection.toFixed(1) : '—'}
          </Text>
          <Text style={styles.statLbl}>PROJ</Text>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.statCol}>
          <Text style={styles.statVal}>{pick.line ?? '—'}</Text>
          <Text style={styles.statLbl}>LINE</Text>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.statCol}>
          <Text style={[styles.statVal, { color: statusColor }]}>
            {confPct != null ? `${confPct}%` : '—'}
          </Text>
          <Text style={styles.statLbl}>CONF</Text>
        </View>
      </View>

      <View style={styles.propTag}>
        <Text style={styles.propTagText}>
          {PROP_LABELS[pick.propType] || pick.propType?.toUpperCase().replace(/_/g, ' ')}
        </Text>
      </View>
    </View>
  );
}

function RecordBar({ picks }: { picks: Pick[] }) {
  const settled = picks.filter(p => p.status === 'won' || p.status === 'lost');
  const hits = picks.filter(p => p.status === 'won').length;
  const misses = picks.filter(p => p.status === 'lost').length;
  const pending = picks.filter(p => !p.status || p.status === 'pending').length;
  const winPct = settled.length > 0 ? Math.round((hits / settled.length) * 100) : 0;

  let streak = 0;
  const sorted = [...picks].sort((a, b) =>
    new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime()
  );
  for (const p of sorted) {
    if (p.status === 'won') streak++;
    else break;
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
          <Text style={styles.recordVal}>{pending}</Text>
          <Text style={styles.recordKey}>LIVE</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.primary }]}>
            {settled.length > 0 ? `${winPct}%` : '—'}
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
  });

  const deleteMutation = useMutation({
    mutationFn: (pickId: string) => {
      if (!session) throw new Error('Not authenticated');
      return deletePick(session.email, session.token, pickId);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['picks'] });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    },
  });

  const handleDelete = useCallback((pick: Pick) => {
    const id = pick._id || pick.id;
    if (!id) return;
    Alert.alert('Delete Pick', `Remove ${pick.playerName}?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete', style: 'destructive',
        onPress: () => deleteMutation.mutate(id),
      },
    ]);
  }, [deleteMutation]);

  const live = picks.filter(p => !p.status || p.status === 'pending');
  const history = picks.filter(p => p.status === 'won' || p.status === 'lost');
  const displayed = activeTab === 'live' ? live : history;

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Tracking</Text>
        <View style={styles.tabToggle}>
          {(['live', 'history'] as Tab[]).map(t => (
            <TouchableOpacity
              key={t}
              style={[styles.toggle, activeTab === t && styles.toggleActive]}
              onPress={() => { setActiveTab(t); Haptics.selectionAsync(); }}
            >
              <Text style={[styles.toggleText, activeTab === t && styles.toggleTextActive]}>
                {t === 'live' ? 'Live' : 'History'}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      {picks.length > 0 && <RecordBar picks={picks} />}

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.primary} size="large" />
        </View>
      ) : displayed.length === 0 ? (
        <View style={styles.empty}>
          <Ionicons
            name={activeTab === 'live' ? 'timer-outline' : 'archive-outline'}
            size={48}
            color={Colors.textTertiary}
          />
          <Text style={styles.emptyTitle}>
            {activeTab === 'live' ? 'No live picks' : 'No history yet'}
          </Text>
          <Text style={styles.emptySub}>
            {activeTab === 'live'
              ? 'Analyze a prop and save it to track it here.'
              : 'Settled picks will appear here once results are in.'}
          </Text>
        </View>
      ) : (
        <FlatList
          data={displayed}
          keyExtractor={(item, i) => item._id || item.id || String(i)}
          renderItem={({ item }) => <PickCard pick={item} onDelete={() => handleDelete(item)} />}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={isRefetching}
              onRefresh={refetch}
              tintColor={Colors.primary}
            />
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: {
    paddingHorizontal: 20,
    paddingBottom: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerTitle: { fontSize: 28, fontWeight: '800', color: Colors.text },
  tabToggle: {
    flexDirection: 'row',
    backgroundColor: Colors.card,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: 3,
  },
  toggle: {
    paddingVertical: 6,
    paddingHorizontal: 16,
    borderRadius: 8,
  },
  toggleActive: { backgroundColor: Colors.primary },
  toggleText: { fontSize: 13, fontWeight: '600', color: Colors.textSecondary },
  toggleTextActive: { color: '#000' },
  recordBar: {
    marginHorizontal: 20,
    marginBottom: 12,
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: 14,
    gap: 8,
  },
  recordLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: Colors.textTertiary,
    letterSpacing: 1.5,
  },
  recordStats: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  recordStat: { alignItems: 'center', flex: 1 },
  recordVal: { fontSize: 18, fontWeight: '800', color: Colors.text },
  recordKey: { fontSize: 9, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.5, marginTop: 2 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, paddingHorizontal: 40 },
  emptyTitle: { fontSize: 18, fontWeight: '700', color: Colors.text },
  emptySub: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center', lineHeight: 20 },
  list: { paddingHorizontal: 20, paddingBottom: 40 },
  card: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    gap: 8,
  },
  cardTopRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
  },
  cardLeft: { flex: 1, marginRight: 8 },
  cardRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  cardPlayer: { fontSize: 16, fontWeight: '700', color: Colors.text },
  cardMeta: { fontSize: 10, color: Colors.textTertiary, marginTop: 2, letterSpacing: 0.3 },
  schedBadge: {
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderRadius: 6,
    paddingHorizontal: 7,
    paddingVertical: 3,
  },
  schedText: { fontSize: 9, color: Colors.textSecondary, fontWeight: '700', letterSpacing: 0.5 },
  pickLabel: {
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.3,
    textTransform: 'capitalize',
  },
  statsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 0,
  },
  statCol: { flex: 1, alignItems: 'center', gap: 3 },
  statVal: { fontSize: 17, fontWeight: '700', color: Colors.text },
  statLbl: { fontSize: 9, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 1 },
  statDivider: { width: 1, height: 32, backgroundColor: Colors.borderSubtle },
  propTag: {
    alignSelf: 'flex-end',
  },
  propTagText: {
    fontSize: 10,
    fontWeight: '700',
    color: Colors.textTertiary,
    letterSpacing: 1,
    textTransform: 'uppercase',
  },
});
