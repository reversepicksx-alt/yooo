import React, { useMemo, useState } from 'react';
import { View, Text, StyleSheet, TextInput, TouchableOpacity, Modal, Pressable, ScrollView, FlatList } from 'react-native';
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

function getLeagueLabel(p: Pick) {
  return p.leagueName || (p.leagueId ? `League ${p.leagueId}` : '');
}

export default function MatchupsScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const { data: picks = [] } = useQuery({
    queryKey: ['picks', session?.email],
    queryFn: async () => (session ? listPicks(session.email, session.token) : []),
    enabled: !!session,
    staleTime: 10000,
  });

  const [playerQuery, setPlayerQuery] = useState('');
  const [opponentQuery, setOpponentQuery] = useState('');
  const [filters, setFilters] = useState<Record<SelectKey, string>>({ position: 'All', propType: 'All', league: 'All', result: 'All' });
  const [picker, setPicker] = useState<SelectKey | null>(null);
  const [pickerSearch, setPickerSearch] = useState('');
  const [reviewed, setReviewed] = useState(false);

  const options = useMemo(() => {
    const uniq = (vals: Array<string | null | undefined>) => ['All', ...[...new Set(vals.map(v => v?.trim()).filter(Boolean) as string[])].sort((a, b) => a.localeCompare(b))];
    return {
      position: uniq(picks.map(p => p.position)),
      propType: uniq(picks.map(p => p.propType)),
      league: uniq(picks.map(p => getLeagueLabel(p))),
      result: RESULT_OPTIONS,
    };
  }, [picks]);

  const filtered = useMemo(() => picks.filter((p) => {
    const playerMatch = !playerQuery.trim() || (p.playerName || '').toLowerCase().includes(playerQuery.trim().toLowerCase());
    const oppMatch = !opponentQuery.trim() || (p.opponentName || '').toLowerCase().includes(opponentQuery.trim().toLowerCase());
    const posMatch = filters.position === 'All' || (p.position || '') === filters.position;
    const propMatch = filters.propType === 'All' || (p.propType || '') === filters.propType;
    const leagueMatch = filters.league === 'All' || getLeagueLabel(p) === filters.league;
    const resMatch = filters.result === 'All' || normalizeResult(p) === filters.result;
    return playerMatch && oppMatch && posMatch && propMatch && leagueMatch && resMatch;
  }), [picks, playerQuery, opponentQuery, filters]);

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

  const activeSummary = useMemo(() => [
    playerQuery ? `Player: ${playerQuery}` : null,
    opponentQuery ? `Opponent: ${opponentQuery}` : null,
    filters.position !== 'All' ? `Position: ${filters.position}` : null,
    filters.propType !== 'All' ? `Prop: ${filters.propType}` : null,
    filters.league !== 'All' ? `League: ${filters.league}` : null,
    filters.result !== 'All' ? `Result: ${filters.result}` : null,
  ].filter(Boolean) as string[], [playerQuery, opponentQuery, filters]);

  const pickerValues = picker ? options[picker].filter(v => pickerSearch.trim() ? v.toLowerCase().includes(pickerSearch.trim().toLowerCase()) : true) : [];

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Text style={styles.title}>Matchups</Text>
        <Text style={styles.subtitle}>Search your settled picks by player and opponent.</Text>
      </View>

      <View style={styles.searchCard}>
        <SearchField label="Player" value={playerQuery} onChangeText={setPlayerQuery} placeholder="Search player name" />
        <SearchField label="Opponent" value={opponentQuery} onChangeText={setOpponentQuery} placeholder="Search opponent name" />
      </View>

      <View style={styles.selectGrid}>
        {(Object.keys(filters) as SelectKey[]).map((key) => (
          <TouchableOpacity key={key} onPress={() => { setPicker(key); setPickerSearch(''); }} style={styles.selectBtn}>
            <Text style={styles.selectLabel}>{key === 'propType' ? 'Prop Type' : key.charAt(0).toUpperCase() + key.slice(1)}</Text>
            <Text style={styles.selectValue}>{filters[key]}</Text>
            <Ionicons name="chevron-down" size={16} color={Colors.textTertiary} />
          </TouchableOpacity>
        ))}
      </View>

      <TouchableOpacity onPress={() => setReviewed(true)} style={styles.reviewBtn}>
        <Text style={styles.reviewBtnText}>Review</Text>
      </TouchableOpacity>

      {reviewed && (
        <ScrollView style={styles.results} contentContainerStyle={{ paddingBottom: 28 }}>
          <SummaryCard title="Active filters" subtitle={activeSummary.length ? activeSummary.join(' · ') : 'No filters selected'} />
          <View style={styles.statsGrid}>
            <StatTile label="Total" value={String(stats.total)} />
            <StatTile label="Hits" value={String(stats.hits)} accent={Colors.success} />
            <StatTile label="Misses" value={String(stats.misses)} accent={Colors.error} />
            <StatTile label="Pushes" value={String(stats.pushes)} accent={Colors.push} />
            <StatTile label="DNPs" value={String(stats.dnps)} accent={Colors.dnp} />
            <StatTile label="Win rate" value={stats.winRate != null ? `${stats.winRate}%` : '—'} accent={Colors.primary} />
            <StatTile label="Avg line" value={stats.averageLine != null ? stats.averageLine.toFixed(1) : '—'} accent={Colors.accent} />
          </View>
          {grouped.map(([player, playerPicks]) => {
            const playerHits = playerPicks.filter(p => normalizeResult(p) === 'Hit').length;
            const propAgg = new Map<string, number>();
            for (const p of playerPicks) propAgg.set(p.propType || 'Unknown', (propAgg.get(p.propType || 'Unknown') || 0) + (normalizeResult(p) === 'Hit' ? 1 : 0));
            return (
              <View key={player} style={styles.groupCard}>
                <Text style={styles.groupTitle}>{player}</Text>
                <Text style={styles.groupMeta}>{playerPicks.length} picks · {playerHits} hits</Text>
                <View style={{ gap: 10 }}>
                  {playerPicks.map((pick) => (
                    <View key={pick.pickId || pick._id || pick.id || `${player}-${pick.propType}-${pick.line}`} style={styles.pickRow}>
                      <Text style={styles.pickName}>{pick.opponentName || 'Opponent unknown'}</Text>
                      <Text style={styles.pickMeta}>
                        Line {pick.line ?? '—'} · {normalizeResult(pick) || 'Pending'} · {pick.propType || 'Prop'} · {pick.position || '—'} · {getLeagueLabel(pick)}
                      </Text>
                    </View>
                  ))}
                </View>
                <Text style={styles.aggText}>
                  Per-prop hit counts: {Array.from(propAgg.entries()).map(([k, v]) => `${k}: ${v}`).join(' · ') || '—'}
                </Text>
              </View>
            );
          })}
        </ScrollView>
      )}

      <Modal visible={!!picker} transparent animationType="slide" onRequestClose={() => setPicker(null)}>
        <View style={styles.modalWrap}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setPicker(null)} />
          <View style={styles.modalSheet}>
            <View style={styles.modalHandle} />
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>{picker === 'propType' ? 'Prop Type' : picker ? picker.charAt(0).toUpperCase() + picker.slice(1) : ''}</Text>
              <TouchableOpacity onPress={() => setPicker(null)}><Ionicons name="close" size={20} color={Colors.textSecondary} /></TouchableOpacity>
            </View>
            <View style={styles.modalSearch}>
              <Ionicons name="search" size={16} color={Colors.textTertiary} />
              <TextInput value={pickerSearch} onChangeText={setPickerSearch} placeholder="Search options" placeholderTextColor={Colors.textTertiary} style={styles.modalSearchInput} />
            </View>
            <FlatList
              data={pickerValues}
              keyExtractor={(item) => item}
              renderItem={({ item }) => (
                <TouchableOpacity onPress={() => { if (picker) setFilters(prev => ({ ...prev, [picker]: item })); setPicker(null); }}>
                  <Text style={styles.optionText}>{item}</Text>
                </TouchableOpacity>
              )}
            />
          </View>
        </View>
      </Modal>
    </View>
  );
}

function SearchField({ label, ...props }: { label: string } & React.ComponentProps<typeof TextInput>) {
  return (
    <View style={styles.searchField}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <View style={styles.searchInputWrap}>
        <TextInput {...props} style={styles.searchInput} placeholderTextColor={Colors.textTertiary} />
      </View>
    </View>
  );
}

function SummaryCard({ title, subtitle }: { title: string; subtitle: string }) {
  return <View style={styles.summaryCard}><Text style={styles.summaryTitle}>{title}</Text><Text style={styles.summaryText}>{subtitle}</Text></View>;
}

function StatTile({ label, value, accent = Colors.text }: { label: string; value: string; accent?: string }) {
  return <View style={styles.statTile}><Text style={[styles.statValue, { color: accent }]}>{value}</Text><Text style={styles.statLabel}>{label}</Text></View>;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: { paddingHorizontal: 16, paddingBottom: 14 },
  title: { color: Colors.text, fontSize: 28, fontWeight: '800' },
  subtitle: { color: Colors.textSecondary, marginTop: 6, lineHeight: 20 },
  searchCard: { marginHorizontal: 16, gap: 12, padding: 14, borderRadius: Colors.radiusLg, backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border },
  searchField: { gap: 6 },
  fieldLabel: { color: Colors.textSecondary, fontSize: 12, fontWeight: '700' },
  searchInputWrap: { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: Colors.cardSecondary, borderRadius: 12, paddingHorizontal: 12, borderWidth: 1, borderColor: Colors.borderSubtle },
  searchInput: { flex: 1, color: Colors.text, paddingVertical: 12 },
  selectGrid: { paddingHorizontal: 16, paddingTop: 14, gap: 10 },
  selectBtn: { backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border, borderRadius: 14, padding: 12, flexDirection: 'row', alignItems: 'center', gap: 8 },
  selectLabel: { color: Colors.textSecondary, fontSize: 12, fontWeight: '700', flex: 1 },
  selectValue: { color: Colors.text, fontWeight: '700' },
  reviewBtn: { marginHorizontal: 16, marginTop: 14, backgroundColor: Colors.primary, borderRadius: 14, paddingVertical: 14, alignItems: 'center' },
  reviewBtnText: { color: '#000', fontWeight: '900', fontSize: 15 },
  results: { flex: 1, marginTop: 14 },
  summaryCard: { marginHorizontal: 16, marginBottom: 14, padding: 14, borderRadius: 16, backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border },
  summaryTitle: { color: Colors.text, fontWeight: '800', marginBottom: 6 },
  summaryText: { color: Colors.textSecondary, lineHeight: 20 },
  statsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, paddingHorizontal: 16, marginBottom: 10 },
  statTile: { width: '31%', minWidth: 100, padding: 12, backgroundColor: Colors.card, borderRadius: 14, borderWidth: 1, borderColor: Colors.border },
  statValue: { fontSize: 18, fontWeight: '900' },
  statLabel: { color: Colors.textSecondary, fontSize: 11, marginTop: 4 },
  groupCard: { marginHorizontal: 16, marginTop: 12, padding: 14, borderRadius: 16, backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border },
  groupTitle: { color: Colors.text, fontSize: 16, fontWeight: '800' },
  groupMeta: { color: Colors.textSecondary, marginTop: 4, marginBottom: 10 },
  pickRow: { paddingVertical: 10, borderTopWidth: 1, borderTopColor: Colors.borderSubtle },
  pickName: { color: Colors.text, fontWeight: '700' },
  pickMeta: { color: Colors.textSecondary, marginTop: 4, lineHeight: 18 },
  aggText: { color: Colors.textSecondary, marginTop: 10, fontSize: 12 },
  modalWrap: { flex: 1, justifyContent: 'flex-end' },
  modalSheet: { maxHeight: '70%', backgroundColor: Colors.card, borderTopLeftRadius: 20, borderTopRightRadius: 20, borderWidth: 1, borderColor: Colors.borderSubtle },
  modalHandle: { width: 40, height: 4, borderRadius: 2, backgroundColor: Colors.border, alignSelf: 'center', marginTop: 10 },
  modalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 16 },
  modalTitle: { color: Colors.text, fontSize: 16, fontWeight: '800' },
  modalSearch: { marginHorizontal: 16, marginBottom: 10, flexDirection: 'row', alignItems: 'center', gap: 8, borderRadius: 12, backgroundColor: Colors.cardSecondary, paddingHorizontal: 12, borderWidth: 1, borderColor: Colors.borderSubtle },
  modalSearchInput: { flex: 1, color: Colors.text, paddingVertical: 10 },
  optionText: { color: Colors.text, paddingHorizontal: 16, paddingVertical: 14, borderTopWidth: 1, borderTopColor: Colors.borderSubtle },
});