import React from 'react';
import {
  View, Text, StyleSheet, ScrollView,
  ActivityIndicator, RefreshControl, Platform,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useQuery } from '@tanstack/react-query';
import Colors from '@/constants/colors';
import { getIntelDashboard } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

interface IntelPick {
  playerName?: string;
  teamName?: string;
  propType?: string;
  line?: number;
  recommendation?: string;
  confidence?: number;
  projection?: number;
  sport?: string;
}

export default function IntelScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const topPad = Platform.OS === 'web' ? 67 : insets.top;

  const { data, isLoading, refetch, isRefetching, error } = useQuery({
    queryKey: ['intel', session?.email],
    queryFn: () => {
      if (!session) throw new Error('Not authenticated');
      return getIntelDashboard(session.email, session.token);
    },
    enabled: !!session,
  });

  const topPicks: IntelPick[] = (data?.topPicks || []) as IntelPick[];

  const PROP_LABELS: Record<string, string> = {
    pass_attempts: 'Pass Att', shots: 'Shots', shots_on_target: 'SOT',
    goals: 'Goals', assists: 'Assists', key_passes: 'Key Pass', tackles: 'Tackles',
  };

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Intel</Text>
        <Text style={styles.headerSub}>Market insights & edge picks</Text>
      </View>

      <ScrollView
        contentContainerStyle={styles.body}
        refreshControl={
          <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={Colors.primary} />
        }
      >
        {isLoading && (
          <View style={styles.center}>
            <ActivityIndicator color={Colors.primary} size="large" />
            <Text style={styles.loadingText}>Loading intel dashboard…</Text>
          </View>
        )}

        {error && !isLoading && (
          <View style={styles.errorBox}>
            <Ionicons name="alert-circle-outline" size={24} color={Colors.error} />
            <Text style={styles.errorText}>Failed to load intel. Pull to refresh.</Text>
          </View>
        )}

        {data && !isLoading && (
          <>
            {data.insights && (
              <View style={styles.insightCard}>
                <View style={styles.insightHeader}>
                  <Ionicons name="sparkles" size={16} color={Colors.accent} />
                  <Text style={styles.insightTitle}>AI Market Insights</Text>
                </View>
                <Text style={styles.insightText}>{String(data.insights)}</Text>
              </View>
            )}

            {topPicks.length > 0 && (
              <>
                <Text style={styles.sectionTitle}>Top Picks Today</Text>
                {topPicks.map((pick, i) => {
                  const isOver = pick.recommendation === 'OVER';
                  const isUnder = pick.recommendation === 'UNDER';
                  const recColor = isOver ? Colors.success : isUnder ? Colors.error : Colors.textSecondary;
                  const confPct = pick.confidence != null ? Math.round(pick.confidence * 100) : null;

                  return (
                    <View key={i} style={styles.intelCard}>
                      <View style={styles.intelRank}>
                        <Text style={styles.intelRankNum}>{i + 1}</Text>
                      </View>
                      <View style={styles.intelContent}>
                        <Text style={styles.intelPlayer} numberOfLines={1}>{pick.playerName || 'Player'}</Text>
                        {pick.teamName && <Text style={styles.intelTeam} numberOfLines={1}>{pick.teamName}</Text>}
                        <View style={styles.intelMeta}>
                          <View style={styles.metaPill}>
                            <Text style={styles.metaPillText}>{PROP_LABELS[pick.propType || ''] || pick.propType}</Text>
                          </View>
                          <Text style={styles.metaLine}>Line {pick.line}</Text>
                          {pick.projection != null && (
                            <Text style={styles.metaProj}>Proj {pick.projection.toFixed(1)}</Text>
                          )}
                        </View>
                      </View>
                      <View style={styles.intelRight}>
                        {pick.recommendation && (
                          <Text style={[styles.intelRec, { color: recColor }]}>{pick.recommendation}</Text>
                        )}
                        {confPct != null && (
                          <Text style={styles.intelConf}>{confPct}%</Text>
                        )}
                      </View>
                    </View>
                  );
                })}
              </>
            )}

            {topPicks.length === 0 && !data.insights && (
              <View style={styles.emptyState}>
                <Ionicons name="pulse-outline" size={48} color={Colors.textTertiary} />
                <Text style={styles.emptyTitle}>No intel yet</Text>
                <Text style={styles.emptySub}>Intel is generated as props come in. Check back soon.</Text>
              </View>
            )}
          </>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: { paddingHorizontal: 20, paddingBottom: 16 },
  headerTitle: { fontSize: 28, fontWeight: '800', color: Colors.text },
  headerSub: { fontSize: 13, color: Colors.textSecondary, marginTop: 2 },
  body: { paddingHorizontal: 20, paddingBottom: 40 },
  center: { alignItems: 'center', justifyContent: 'center', paddingTop: 60, gap: 12 },
  loadingText: { color: Colors.textSecondary, fontSize: 14 },
  errorBox: { alignItems: 'center', gap: 12, paddingTop: 60 },
  errorText: { color: Colors.error, fontSize: 15, textAlign: 'center' },
  insightCard: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    padding: 18,
    borderWidth: 1,
    borderColor: Colors.border,
    marginBottom: 24,
  },
  insightHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  insightTitle: { fontSize: 14, fontWeight: '700', color: Colors.accent },
  insightText: { color: Colors.textSecondary, fontSize: 13, lineHeight: 20 },
  sectionTitle: { fontSize: 16, fontWeight: '700', color: Colors.text, marginBottom: 12 },
  intelCard: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    padding: 14,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: Colors.border,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  intelRank: {
    width: 30,
    height: 30,
    borderRadius: 15,
    backgroundColor: Colors.primaryDim,
    alignItems: 'center',
    justifyContent: 'center',
  },
  intelRankNum: { color: Colors.primary, fontWeight: '800', fontSize: 14 },
  intelContent: { flex: 1 },
  intelPlayer: { fontSize: 15, fontWeight: '700', color: Colors.text },
  intelTeam: { fontSize: 12, color: Colors.textSecondary },
  intelMeta: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 6, flexWrap: 'wrap' },
  metaPill: { backgroundColor: Colors.primaryDim, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 20 },
  metaPillText: { fontSize: 10, color: Colors.primary, fontWeight: '600' },
  metaLine: { fontSize: 11, color: Colors.textSecondary },
  metaProj: { fontSize: 11, color: Colors.accent, fontWeight: '600' },
  intelRight: { alignItems: 'flex-end', gap: 4 },
  intelRec: { fontSize: 13, fontWeight: '800', letterSpacing: 0.5 },
  intelConf: { fontSize: 12, color: Colors.textSecondary },
  emptyState: { alignItems: 'center', paddingTop: 60, gap: 12 },
  emptyTitle: { fontSize: 18, fontWeight: '700', color: Colors.text },
  emptySub: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center', lineHeight: 20 },
});
