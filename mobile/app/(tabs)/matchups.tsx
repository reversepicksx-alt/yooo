import React, { useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery } from '@tanstack/react-query';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { listPicks, Pick } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

type SelectKey = 'position' | 'propType' | 'league' | 'result';

const RESULT_OPTIONS = ['All', 'Hit', 'Miss', 'Push', 'DNP'];

function normalizeResult(p: Pick) {
  if (p.result === 'hit' || p.result === 'won') return 'Hit';
  if (p.result === 'miss' || p.result === 'lost') return 'Miss';
  if (p.result === 'push') return 'Push';
  if (p.result === 'dnp') return 'DNP';
  return '';
}

function isSettled(p: Pick) {
  const r = normalizeResult(p);
  return r === 'Hit' || r === 'Miss' || r === 'Push' || r === 'DNP';
}

function getLeagueLabel(p: Pick) {
  return p.leagueName || (p.leagueId ? `League ${p.leagueId}` : '');
}

const LABELS: Record<SelectKey, string> = {
  position: 'Position',
  propType: 'Prop Type',
  league: 'League',
  result: 'Result',
};

export default function MatchupsScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const { data: picks = [], isLoading } = useQuery({
    queryKey: ['picks', session?.email],
    queryFn: async () => (session ? listPicks(session.email, session.token) : []),
    enabled: !!session,
    staleTime: 10000,
  });

  const settledPicks = useMemo(() => picks.filter(isSettled), [picks]);

  const [playerQuery, setPlayerQuery] = useState('');
  const [opponentQuery, setOpponentQuery] = useState('');
  const [filters, setFilters] = useState<Record<SelectKey, string>>({
    position: 'All',
    propType: 'All',
    league: 'All',
    result: 'All',
  });
  const [openDropdown, setOpenDropdown] = useState<SelectKey | null>(null);
  const [reviewed, setReviewed] = useState(false);

  const options = useMemo(() => {
    const uniq = (vals: Array<string | null | undefined>) =>
      ['All', ...[...new Set(vals.map(v => v?.trim()).filter(Boolean) as string[])].sort((a, b) => a.localeCompare(b))];
    return {
      position: uniq(settledPicks.map(p => p.position)),
      propType: uniq(settledPicks.map(p => p.propType)),
      league: uniq(settledPicks.map(p => getLeagueLabel(p))),
      result: RESULT_OPTIONS,
    };
  }, [settledPicks]);

  const filtered = useMemo(() => {
    return settledPicks.filter((p) => {
      const playerMatch = !playerQuery.trim() || (p.playerName || '').toLowerCase().includes(playerQuery.trim().toLowerCase());
      const oppMatch = !opponentQuery.trim() || (p.opponentName || '').toLowerCase().includes(opponentQuery.trim().toLowerCase());
      const posMatch = filters.position === 'All' || (p.position || '') === filters.position;
      const propMatch = filters.propType === 'All' || (p.propType || '') === filters.propType;
      const leagueMatch = filters.league === 'All' || getLeagueLabel(p) === filters.league;
      const resMatch = filters.result === 'All' || normalizeResult(p) === filters.result;
      return playerMatch && oppMatch && posMatch && propMatch && leagueMatch && resMatch;
    });
  }, [settledPicks, playerQuery, opponentQuery, filters]);

  const stats = useMemo(() => {
    const total = filtered.length;
    const hits = filtered.filter(p => normalizeResult(p) === 'Hit').length;
    const misses = filtered.filter(p => normalizeResult(p) === 'Miss').length;
    const pushes = filtered.filter(p => normalizeResult(p) === 'Push').length;
    const dnps = filtered.filter(p => normalizeResult(p) === 'DNP').length;
    const settled = hits + misses + pushes + dnps;
    const winRate = settled > 0 ? Math.round((hits / settled) * 100) : null;
    const lines = filtered.map(p => p.line).filter((v): v is number => typeof v === 'number');
    const averageLine = lines.length ? lines.reduce((a, b) => a + b, 0) / lines.length : null;
    return { total, hits, misses, pushes, dnps, winRate, averageLine };
  }, [filtered]);

  const grouped = useMemo(() => {
    const map = new Map<string, Pick[]>();
    for (const pick of filtered) {
      const key = pick.playerName || 'Unknown Player';
      map.set(key, [...(map.get(key) || []), pick]);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [filtered]);

  const activeSummary = useMemo(
    () =>
      [
        playerQuery ? `Player: ${playerQuery}` : null,
        opponentQuery ? `Opponent: ${opponentQuery}` : null,
        filters.position !== 'All' ? `Position: ${filters.position}` : null,
        filters.propType !== 'All' ? `Prop: ${filters.propType}` : null,
        filters.league !== 'All' ? `League: ${filters.league}` : null,
        filters.result !== 'All' ? `Result: ${filters.result}` : null,
      ].filter(Boolean) as string[],
    [playerQuery, opponentQuery, filters]
  );

  const hasFilters = playerQuery || opponentQuery || filters.position !== 'All' || filters.propType !== 'All' || filters.league !== 'All' || filters.result !== 'All';

  const handleSelect = (key: SelectKey, value: string) => {
    setFilters(prev => ({ ...prev, [key]: value }));
    setOpenDropdown(null);
  };

  const reset = () => {
    setPlayerQuery('');
    setOpponentQuery('');
    setFilters({ position: 'All', propType: 'All', league: 'All', result: 'All' });
    setReviewed(false);
  };

  return (
    <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <Text style={styles.title}>Matchups</Text>
        <Text style={styles.subtitle}>Search every settled pick by player, opponent, and more.</Text>
      </View>

      <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent} keyboardShouldPersistTaps="handled">
        <View style={styles.card}>
          <Text style={styles.cardLabel}>Player</Text>
          <View style={styles.inputWrap}>
            <TextInput
              value={playerQuery}
              onChangeText={setPlayerQuery}
              placeholder="Search player name"
              placeholderTextColor={Colors.textTertiary}
              style={styles.input}
            />
          </View>
        </View>

        <View style={styles.card}>
          <Text style={styles.cardLabel}>Opponent</Text>
          <View style={styles.inputWrap}>
            <TextInput
              value={opponentQuery}
              onChangeText={setOpponentQuery}
              placeholder="Search opponent team"
              placeholderTextColor={Colors.textTertiary}
              style={styles.input}
            />
          </View>
        </View>

        <View style={styles.dropdownCard}>
          {(Object.keys(filters) as SelectKey[]).map((key) => {
            const isOpen = openDropdown === key;
            const values = options[key];
            return (
              <View key={key} style={styles.dropdownSection}>
                <TouchableOpacity
                  onPress={() => setOpenDropdown(isOpen ? null : key)}
                  style={styles.dropdownBtn}
                  activeOpacity={0.75}
                >
                  <Text style={styles.dropdownLabel}>{LABELS[key]}</Text>
                  <View style={styles.dropdownRight}>
                    <Text style={styles.dropdownValue}>{filters[key]}</Text>
                    <Ionicons name={isOpen ? 'chevron-up' : 'chevron-down'} size={16} color={Colors.textTertiary} />
                  </View>
                </TouchableOpacity>

                {isOpen && (
                  <View style={styles.dropdownList}>
                    {values.map((value) => {
                      const selected = filters[key] === value;
                      return (
                        <TouchableOpacity
                          key={value}
                          onPress={() => handleSelect(key, value)}
                          style={[styles.dropdownOption, selected && styles.dropdownOptionActive]}
                          activeOpacity={0.75}
                        >
                          <Text style={[styles.dropdownOptionText, selected && styles.dropdownOptionTextActive]}>{value}</Text>
                          {selected && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                )}
              </View>
            );
          })}
        </View>

        <View style={styles.actions}>
          <TouchableOpacity onPress={() => setReviewed(true)} style={styles.reviewBtn} activeOpacity={0.75}>
            <Text style={styles.reviewBtnText}>Review</Text>
          </TouchableOpacity>
          {hasFilters && (
            <TouchableOpacity onPress={reset} style={styles.resetBtn} activeOpacity={0.75}>
              <Text style={styles.resetBtnText}>Reset</Text>
            </TouchableOpacity>
          )}
        </View>

        {reviewed && (
          <View style={styles.results}>
            <View style={styles.summaryCard}>
              <Text style={styles.summaryTitle}>Active filters</Text>
              <Text style={styles.summaryText}>{activeSummary.length ? activeSummary.join(' · ') : 'No filters selected'}</Text>
            </View>

            <View style={styles.statsGrid}>
              <StatTile label="Total" value={String(stats.total)} />
              <StatTile label="Hits" value={String(stats.hits)} accent={Colors.success} />
              <StatTile label="Misses" value={String(stats.misses)} accent={Colors.error} />
              <StatTile label="Pushes" value={String(stats.pushes)} accent={Colors.push} />
              <StatTile label="DNPs" value={String(stats.dnps)} accent={Colors.dnp} />
              <StatTile label="Win rate" value={stats.winRate != null ? `${stats.winRate}%` : '—'} accent={Colors.primary} />
              <StatTile label="Avg line" value={stats.averageLine != null ? stats.averageLine.toFixed(1) : '—'} accent={Colors.accent} />
            </View>

            {isLoading ? (
              <Text style={styles.emptyText}>Loading picks...</Text>
            ) : filtered.length === 0 ? (
              <View style={styles.emptyState}>
                <Ionicons name="search-outline" size={40} color={Colors.textTertiary} />
                <Text style={styles.emptyTitle}>No settled picks match</Text>
                <Text style={styles.emptyBody}>Try widening your search or removing a filter.</Text>
              </View>
            ) : (
              grouped.map(([player, playerPicks]) => {
                const playerHits = playerPicks.filter(p => normalizeResult(p) === 'Hit').length;
                const playerMisses = playerPicks.filter(p => normalizeResult(p) === 'Miss').length;
                const propAgg = new Map<string, { hits: number; total: number }>();
                for (const p of playerPicks) {
                  const key = p.propType || 'Unknown';
                  const curr = propAgg.get(key) || { hits: 0, total: 0 };
                  curr.total += 1;
                  if (normalizeResult(p) === 'Hit') curr.hits += 1;
                  propAgg.set(key, curr);
                }
                return (
                  <View key={player} style={styles.groupCard}>
                    <View style={styles.groupHeader}>
                      <Text style={styles.groupTitle}>{player}</Text>
                      <Text style={styles.groupMeta}>{playerHits}H · {playerMisses}M · {playerPicks.length} picks</Text>
                    </View>
                    {playerPicks.map((pick) => (
                      <View key={pick.pickId || pick._id || pick.id || `${player}-${pick.propType}-${pick.line}`} style={styles.pickRow}>
                        <View style={styles.pickRowTop}>
                          <Text style={styles.pickOpponent}>{pick.opponentName || 'Opponent unknown'}</Text>
                          <Text style={[styles.pickResult, { color: normalizeResult(pick) === 'Hit' ? Colors.success : normalizeResult(pick) === 'Miss' ? Colors.error : Colors.textSecondary }]}>
                            {normalizeResult(pick) || 'Pending'}
                          </Text>
                        </View>
                        <Text style={styles.pickMeta}>
                          Line {pick.line ?? '—'} · {pick.propType || 'Prop'} · {pick.position || '—'} · {getLeagueLabel(pick)}
                        </Text>
                      </View>
                    ))}
                    <Text style={styles.aggText}>
                      {Array.from(propAgg.entries())
                        .map(([k, v]) => `${k}: ${v.hits}/${v.total}`)
                        .join(' · ') || '—'}
                    </Text>
                  </View>
                );
              })
            )}
          </View>
        )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function StatTile({ label, value, accent = Colors.text }: { label: string; value: string; accent?: string }) {
  return (
    <View style={styles.statTile}>
      <Text style={[styles.statValue, { color: accent }]}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: { paddingHorizontal: 16, paddingBottom: 14 },
  title: { color: Colors.text, fontSize: 28, fontWeight: '800' },
  subtitle: { color: Colors.textSecondary, marginTop: 6, lineHeight: 20 },
  scroll: { flex: 1 },
  scrollContent: { padding: 16, paddingBottom: 40 },
  card: { marginBottom: 12, padding: 14, borderRadius: 16, backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border },
  cardLabel: { color: Colors.textSecondary, fontSize: 12, fontWeight: '700', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 },
  inputWrap: { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: Colors.cardSecondary, borderRadius: 12, paddingHorizontal: 12, borderWidth: 1, borderColor: Colors.borderSubtle },
  input: { flex: 1, color: Colors.text, paddingVertical: 12, fontSize: 15 },
  dropdownCard: { marginBottom: 12, padding: 14, borderRadius: 16, backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border },
  dropdownSection: { borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle, paddingVertical: 10 },
  dropdownSectionLast: { borderBottomWidth: 0 },
  dropdownBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  dropdownLabel: { color: Colors.textSecondary, fontSize: 12, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
  dropdownRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  dropdownValue: { color: Colors.text, fontWeight: '700', fontSize: 15 },
  dropdownList: { marginTop: 10, gap: 2 },
  dropdownOption: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 12, paddingHorizontal: 12, borderRadius: 10 },
  dropdownOptionActive: { backgroundColor: Colors.primaryDim },
  dropdownOptionText: { color: Colors.text, fontSize: 14 },
  dropdownOptionTextActive: { color: Colors.primary, fontWeight: '700' },
  actions: { flexDirection: 'row', gap: 10, marginBottom: 16 },
  reviewBtn: { flex: 1, backgroundColor: Colors.primary, borderRadius: 14, paddingVertical: 14, alignItems: 'center' },
  reviewBtnText: { color: '#000', fontWeight: '900', fontSize: 15 },
  resetBtn: { paddingHorizontal: 18, borderRadius: 14, backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border, alignItems: 'center', justifyContent: 'center' },
  resetBtnText: { color: Colors.text, fontWeight: '700', fontSize: 14 },
  results: { marginTop: 4 },
  summaryCard: { marginBottom: 14, padding: 14, borderRadius: 16, backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border },
  summaryTitle: { color: Colors.text, fontWeight: '800', marginBottom: 6, fontSize: 14 },
  summaryText: { color: Colors.textSecondary, lineHeight: 20, fontSize: 13 },
  statsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginBottom: 10 },
  statTile: { width: '31%', minWidth: 100, padding: 12, backgroundColor: Colors.card, borderRadius: 14, borderWidth: 1, borderColor: Colors.border },
  statValue: { fontSize: 18, fontWeight: '900' },
  statLabel: { color: Colors.textSecondary, fontSize: 11, marginTop: 4 },
  groupCard: { marginTop: 12, padding: 14, borderRadius: 16, backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border },
  groupHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  groupTitle: { color: Colors.text, fontSize: 16, fontWeight: '800' },
  groupMeta: { color: Colors.textSecondary, fontSize: 12, fontWeight: '600' },
  pickRow: { paddingVertical: 10, borderTopWidth: 1, borderTopColor: Colors.borderSubtle },
  pickRowTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  pickOpponent: { color: Colors.text, fontWeight: '700', fontSize: 14 },
  pickResult: { fontWeight: '800', fontSize: 13 },
  pickMeta: { color: Colors.textSecondary, marginTop: 4, lineHeight: 18, fontSize: 12 },
  aggText: { color: Colors.textSecondary, marginTop: 10, fontSize: 12, lineHeight: 18 },
  emptyState: { alignItems: 'center', marginTop: 30, padding: 20 },
  emptyTitle: { color: Colors.text, fontSize: 16, fontWeight: '800', marginTop: 12 },
  emptyBody: { color: Colors.textSecondary, fontSize: 13, marginTop: 6, textAlign: 'center' },
  emptyText: { color: Colors.textSecondary, fontSize: 14, textAlign: 'center', marginTop: 20 },
});
