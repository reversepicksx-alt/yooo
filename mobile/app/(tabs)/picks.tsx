import React, { useCallback } from 'react';
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

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Att', shots: 'Shots', shots_on_target: 'SOT',
  goals: 'Goals', assists: 'Assists', key_passes: 'Key Pass',
  tackles: 'Tackles', saves: 'Saves', dribbles: 'Dribbles', crosses: 'Crosses',
};

function PickRow({ pick, onDelete }: { pick: Pick; onDelete: () => void }) {
  const isOver = pick.recommendation === 'OVER';
  const isUnder = pick.recommendation === 'UNDER';
  const recColor = isOver ? Colors.success : isUnder ? Colors.error : Colors.textSecondary;

  const statusIcon: Record<string, keyof typeof Ionicons.glyphMap> = {
    won: 'checkmark-circle',
    lost: 'close-circle',
    pending: 'time-outline',
  };
  const statusColor = { won: Colors.success, lost: Colors.error, pending: Colors.accent };
  const status = pick.status || 'pending';

  return (
    <View style={styles.pickCard}>
      <View style={styles.pickHeader}>
        <View style={styles.pickLeft}>
          <Text style={styles.pickPlayer} numberOfLines={1}>{pick.playerName}</Text>
          {pick.teamName && <Text style={styles.pickTeam} numberOfLines={1}>{pick.teamName}</Text>}
        </View>
        <View style={styles.pickRight}>
          {pick.recommendation && (
            <Text style={[styles.pickRec, { color: recColor }]}>{pick.recommendation}</Text>
          )}
          <Ionicons name={statusIcon[status] || 'time-outline'} size={18} color={statusColor[status as keyof typeof statusColor] || Colors.textSecondary} />
        </View>
      </View>
      <View style={styles.pickMeta}>
        <View style={styles.metaPill}>
          <Text style={styles.metaPillText}>
            {PROP_LABELS[pick.propType] || pick.propType}
          </Text>
        </View>
        <Text style={styles.metaLine}>Line {pick.line}</Text>
        {pick.projection != null && (
          <Text style={styles.metaProj}>Proj {pick.projection.toFixed(1)}</Text>
        )}
        {pick.confidence != null && (
          <Text style={styles.metaConf}>{Math.round(pick.confidence * 100)}% conf</Text>
        )}
      </View>
      <TouchableOpacity style={styles.deleteBtn} onPress={onDelete} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
        <Ionicons name="trash-outline" size={16} color={Colors.error} />
      </TouchableOpacity>
    </View>
  );
}

export default function PicksScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const qc = useQueryClient();
  const topPad = Platform.OS === 'web' ? 67 : insets.top;

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

  const pending = picks.filter(p => p.status === 'pending' || !p.status);
  const settled = picks.filter(p => p.status === 'won' || p.status === 'lost');
  const wonCount = picks.filter(p => p.status === 'won').length;
  const settledCount = settled.length;

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>My Picks</Text>
        {settledCount > 0 && (
          <View style={styles.record}>
            <Text style={styles.recordText}>{wonCount}–{settledCount - wonCount}</Text>
            <Text style={styles.recordSub}>W–L</Text>
          </View>
        )}
      </View>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.primary} size="large" />
        </View>
      ) : picks.length === 0 ? (
        <View style={styles.empty}>
          <Ionicons name="bookmark-outline" size={48} color={Colors.textTertiary} />
          <Text style={styles.emptyTitle}>No picks yet</Text>
          <Text style={styles.emptySub}>Analyze a prop and save it to track it here.</Text>
        </View>
      ) : (
        <FlatList
          data={picks}
          keyExtractor={(item, i) => item._id || item.id || String(i)}
          renderItem={({ item }) => <PickRow pick={item} onDelete={() => handleDelete(item)} />}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={isRefetching}
              onRefresh={refetch}
              tintColor={Colors.primary}
            />
          }
          scrollEnabled={picks.length > 0}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: {
    paddingHorizontal: 20,
    paddingBottom: 16,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerTitle: { fontSize: 28, fontWeight: '800', color: Colors.text },
  record: { alignItems: 'center', backgroundColor: Colors.card, paddingHorizontal: 14, paddingVertical: 8, borderRadius: Colors.radius },
  recordText: { fontSize: 18, fontWeight: '800', color: Colors.primary },
  recordSub: { fontSize: 10, color: Colors.textSecondary, fontWeight: '600' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, paddingHorizontal: 40 },
  emptyTitle: { fontSize: 18, fontWeight: '700', color: Colors.text },
  emptySub: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center', lineHeight: 20 },
  list: { paddingHorizontal: 20, paddingBottom: 40 },
  pickCard: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    padding: 16,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  pickHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 },
  pickLeft: { flex: 1, marginRight: 12 },
  pickRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  pickPlayer: { fontSize: 16, fontWeight: '700', color: Colors.text },
  pickTeam: { fontSize: 12, color: Colors.textSecondary, marginTop: 2 },
  pickRec: { fontSize: 12, fontWeight: '800', letterSpacing: 0.5 },
  pickMeta: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  metaPill: { backgroundColor: Colors.primaryDim, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 20 },
  metaPillText: { fontSize: 11, color: Colors.primary, fontWeight: '600' },
  metaLine: { fontSize: 12, color: Colors.textSecondary },
  metaProj: { fontSize: 12, color: Colors.accent, fontWeight: '600' },
  metaConf: { fontSize: 12, color: Colors.textTertiary },
  deleteBtn: { position: 'absolute', top: 12, right: 12 },
});
