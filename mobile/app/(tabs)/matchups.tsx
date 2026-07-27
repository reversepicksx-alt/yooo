import React, { useMemo, useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery } from '@tanstack/react-query';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { getMatchups, Pick } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

type FilterKey = 'player' | 'opponent' | 'position' | 'propType' | 'league' | 'result';

interface FilterState {
  player: string;
  opponent: string;
  position: string;
  propType: string;
  league: string;
  result: string;
}

const FILTER_LABELS: Record<FilterKey, string> = {
  player: 'Player',
  opponent: 'Opponent',
  position: 'Position',
  propType: 'Prop Type',
  league: 'League',
  result: 'Result',
};

const DEFAULT_FILTERS: FilterState = {
  player: 'All',
  opponent: 'All',
  position: 'All',
  propType: 'All',
  league: 'All',
  result: 'All',
};

function getLeagueLabel(p: Pick) {
  return p.leagueName || (p.leagueId ? `League ${p.leagueId}` : 'Unknown');
}

function normalizeResult(p: Pick) {
  const r = p.result || '';
  if (r === 'hit' || r === 'won' || r === 'Hit') return 'Hit';
  if (r === 'miss' || r === 'lost' || r === 'Miss') return 'Miss';
  if (r === 'push' || r === 'Push') return 'Push';
  if (r === 'dnp' || r === 'DNP') return 'DNP';
  return '';
}

function getOptionCount(picks: Pick[], key: FilterKey, value: string) {
  return picks.filter((p) => {
    if (key === 'player') return p.playerName === value;
    if (key === 'opponent') return p.opponentName === value;
    if (key === 'position') return p.position === value;
    if (key === 'propType') return p.propType === value;
    if (key === 'league') return getLeagueLabel(p) === value;
    return normalizeResult(p) === value;
  }).length;
}

export default function MatchupsScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['matchups', session?.email],
    queryFn: async () => (session ? getMatchups(session.email, session.token) : { picks: [], options: { players: [], opponents: [], positions: [], propTypes: [], leagues: [], results: [] } }),
    enabled: !!session,
    staleTime: 30000,
  });

  const picks = data?.picks || [];
  const options = data?.options || { players: [], opponents: [], positions: [], propTypes: [], leagues: [], results: [] };

  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [openDropdown, setOpenDropdown] = useState<FilterKey | null>(null);
  const [dropdownSearch, setDropdownSearch] = useState('');
  const [reviewed, setReviewed] = useState(false);

  const optionLists: Record<FilterKey, string[]> = {
    player: ['All', ...options.players],
    opponent: ['All', ...options.opponents],
    position: ['All', ...options.positions],
    propType: ['All', ...options.propTypes],
    league: ['All', ...options.leagues],
    result: ['All', ...options.results],
  };

  const filteredOptions = useMemo(() => {
    if (!openDropdown || !dropdownSearch.trim()) return optionLists;
    const term = dropdownSearch.trim().toLowerCase();
    return {
      ...optionLists,
      [openDropdown]: optionLists[openDropdown].filter((o) => o.toLowerCase().includes(term)),
    };
  }, [optionLists, openDropdown, dropdownSearch]);

  const filteredPicks = useMemo(() => {
    return picks.filter((p) => {
      const playerMatch = filters.player === 'All' || p.playerName === filters.player;
      const oppMatch = filters.opponent === 'All' || p.opponentName === filters.opponent;
      const posMatch = filters.position === 'All' || p.position === filters.position;
      const propMatch = filters.propType === 'All' || p.propType === filters.propType;
      const leagueMatch = filters.league === 'All' || getLeagueLabel(p) === filters.league;
      const resMatch = filters.result === 'All' || normalizeResult(p) === filters.result;
      return playerMatch && oppMatch && posMatch && propMatch && leagueMatch && resMatch;
    });
  }, [picks, filters]);

  const stats = useMemo(() => {
    const total = filteredPicks.length;
    const hits = filteredPicks.filter((p) => normalizeResult(p) === 'Hit').length;
    const misses = filteredPicks.filter((p) => normalizeResult(p) === 'Miss').length;
    const pushes = filteredPicks.filter((p) => normalizeResult(p) === 'Push').length;
    const dnps = filteredPicks.filter((p) => normalizeResult(p) === 'DNP').length;
    const settled = hits + misses + pushes + dnps;
    const winRate = settled > 0 ? Math.round((hits / settled) * 100) : null;
    const lines = filteredPicks.map((p) => p.line).filter((v): v is number => typeof v === 'number');
    const averageLine = lines.length ? lines.reduce((a, b) => a + b, 0) / lines.length : null;
    return { total, hits, misses, pushes, dnps, winRate, averageLine };
  }, [filteredPicks]);

  const grouped = useMemo(() => {
    const map = new Map<string, Pick[]>();
    for (const pick of filteredPicks) {
      const key = pick.playerName || 'Unknown Player';
      map.set(key, [...(map.get(key) || []), pick]);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [filteredPicks]);

  const activeSummary = useMemo(
    () =>
      Object.entries(filters)
        .filter(([_, v]) => v !== 'All')
        .map(([k, v]) => `${FILTER_LABELS[k as FilterKey]}: ${v}`),
    [filters]
  );

  const hasFilters = Object.values(filters).some((v) => v !== 'All');

  const handleOpenDropdown = useCallback((key: FilterKey) => {
    setDropdownSearch('');
    setOpenDropdown((prev) => (prev === key ? null : key));
  }, []);

  const handleSelect = useCallback((key: FilterKey, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setOpenDropdown(null);
    setDropdownSearch('');
  }, []);

  const reset = useCallback(() => {
    setFilters(DEFAULT_FILTERS);
    setReviewed(false);
    setOpenDropdown(null);
    setDropdownSearch('');
  }, []);

  const renderDropdown = (key: FilterKey) => {
    const isOpen = openDropdown === key;
    const values = filteredOptions[key];
    const selected = filters[key];
    const hasSearch = key === 'player' || key === 'opponent' || key === 'league';

    return (
      <View key={key} style={styles.dropdownSection}>
        <TouchableOpacity
          onPress={() => handleOpenDropdown(key)}
          style={styles.dropdownBtn}
          activeOpacity={0.75}
        >
          <Text style={styles.dropdownLabel}>{FILTER_LABELS[key]}</Text>
          <View style={styles.dropdownRight}>
            <Text style={[styles.dropdownValue, selected !== 'All' && styles.dropdownValueActive]} numberOfLines={1}>
              {selected}
            </Text>
            <Ionicons name={isOpen ? 'chevron-up' : 'chevron-down'} size={18} color={Colors.textTertiary} />
          </View>
        </TouchableOpacity>

        {isOpen && (
          <View style={styles.dropdownList}>
            {hasSearch && (
              <View style={styles.dropdownSearchWrap}>
                <Ionicons name="search" size={16} color={Colors.textTertiary} />
                <TextInput
                  value={dropdownSearch}
                  onChangeText={setDropdownSearch}
                  placeholder={`Search ${FILTER_LABELS[key].toLowerCase()}`}
                  placeholderTextColor={Colors.textTertiary}
                  style={styles.dropdownSearchInput}
                  autoFocus={false}
                />
              </View>
            )}
            <ScrollView style={styles.dropdownScroll} nestedScrollEnabled keyboardShouldPersistTaps="handled">
              {values.length === 0 ? (
                <Text style={styles.dropdownEmpty}>No options</Text>
              ) : (
                values.map((value) => {
                  const isSelected = selected === value;
                  const count = value === 'All' ? picks.length : getOptionCount(picks, key, value);
                  return (
                    <TouchableOpacity
                      key={value}
                      onPress={() => handleSelect(key, value)}
                      style={[styles.dropdownOption, isSelected && styles.dropdownOptionActive]}
                      activeOpacity={0.75}
                    >
                      <View style={styles.dropdownOptionLeft}>
                        <Text style={[styles.dropdownOptionText, isSelected && styles.dropdownOptionTextActive]} numberOfLines={1}>
                          {value}
                        </Text>
                        {value !== 'All' && (
                          <Text style={styles.dropdownOptionCount}>{count} pick{count !== 1 ? 's' : ''}</Text>
                        )}
                      </View>
                      {isSelected && <Ionicons name="checkmark" size={18} color={Colors.primary} />}
                    </TouchableOpacity>
                  );
                })
              )}
            </ScrollView>
          </View>
        )}
      </View>
    );
  };

  return (
    <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <Text style={styles.title}>Matchups</Text>
        <Text style={styles.subtitle}>Search every settled pick by player, opponent, position, prop, league, and result.</Text>
      </View>

      <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent} keyboardShouldPersistTaps="handled">
        <View style={styles.dropdownCard}>
          {(Object.keys(FILTER_LABELS) as FilterKey[]).map((key) => renderDropdown(key))}
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

        {isLoading && (
          <View style={styles.loadingWrap}>
            <ActivityIndicator color={Colors.primary} />
            <Text style={styles.loadingText}>Loading settled picks...</Text>
          </View>
        )}

        {error && !isLoading && (
          <View style={styles.errorWrap}>
            <Ionicons name="alert-circle-outline" size={32} color={Colors.error} />
            <Text style={styles.errorTitle}>Could not load matchups</Text>
            <Text style={styles.errorBody}>{error instanceof Error ? error.message : 'Something went wrong'}</Text>
            <TouchableOpacity onPress={() => refetch()} style={styles.retryBtn} activeOpacity={0.75}>
              <Text style={styles.retryBtnText}>Retry</Text>
            </TouchableOpacity>
          </View>
        )}

        {!isLoading && !error && reviewed && (
          <View style={styles.results}>
            <View style={styles.summaryCard}>
              <Text style={styles.summaryTitle}>Active filters</Text>
              <Text style={styles.summaryText}>
                {activeSummary.length ? activeSummary.join(' · ') : 'No filters selected'}
              </Text>
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

            {filteredPicks.length === 0 ? (
              <View style={styles.emptyState}>
                <Ionicons name="search-outline" size={40} color={Colors.textTertiary} />
                <Text style={styles.emptyTitle}>No settled picks match</Text>
                <Text style={styles.emptyBody}>Try widening your search or removing a filter.</Text>
              </View>
            ) : (
              grouped.map(([player, playerPicks]) => {
                const playerHits = playerPicks.filter((p) => normalizeResult(p) === 'Hit').length;
                const playerMisses = playerPicks.filter((p) => normalizeResult(p) === 'Miss').length;
                const playerPushes = playerPicks.filter((p) => normalizeResult(p) === 'Push').length;
                const playerDnps = playerPicks.filter((p) => normalizeResult(p) === 'DNP').length;
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
                      <Text style={styles.groupMeta}>
                        {playerHits}H · {playerMisses}M · {playerPushes}P · {playerDnps}DNP · {playerPicks.length} picks
                      </Text>
                    </View>
                    {playerPicks.map((pick) => (
                      <View key={pick.pickId || `${player}-${pick.propType}-${pick.line}`} style={styles.pickRow}>
                        <View style={styles.pickRowTop}>
                          <Text style={styles.pickOpponent}>{pick.opponentName || 'Opponent unknown'}</Text>
                          <Text
                            style={[
                              styles.pickResult,
                              {
                                color:
                                  normalizeResult(pick) === 'Hit'
                                    ? Colors.success
                                    : normalizeResult(pick) === 'Miss'
                                    ? Colors.error
                                    : Colors.textSecondary,
                              },
                            ]}
                          >
                            {normalizeResult(pick) || 'Pending'}
                          </Text>
                        </View>
                        <Text style={styles.pickMeta}>
                          Line {pick.line ?? '—'} · {pick.propType || 'Prop'} · {pick.position || '—'} · {getLeagueLabel(pick)}
                          {pick.matchScore ? ` · ${pick.matchScore}` : ''}
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

        {!isLoading && !error && !reviewed && picks.length > 0 && (
          <View style={styles.emptyState}>
            <Ionicons name="options-outline" size={40} color={Colors.textTertiary} />
            <Text style={styles.emptyTitle}>Select filters and tap Review</Text>
            <Text style={styles.emptyBody}>{picks.length} settled pick{picks.length !== 1 ? 's' : ''} available to search.</Text>
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
  dropdownCard: { marginBottom: 12, padding: 14, borderRadius: 16, backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border },
  dropdownSection: { borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle, paddingVertical: 10 },
  dropdownSectionLast: { borderBottomWidth: 0 },
  dropdownBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  dropdownLabel: { color: Colors.textSecondary, fontSize: 12, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
  dropdownRight: { flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 1, justifyContent: 'flex-end' },
  dropdownValue: { color: Colors.text, fontWeight: '700', fontSize: 15, maxWidth: 180, textAlign: 'right' },
  dropdownValueActive: { color: Colors.primary },
  dropdownList: { marginTop: 10, backgroundColor: Colors.cardSecondary, borderRadius: 12, overflow: 'hidden' },
  dropdownScroll: { maxHeight: 260 },
  dropdownSearchWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
  },
  dropdownSearchInput: { flex: 1, color: Colors.text, fontSize: 14, paddingVertical: 4 },
  dropdownOption: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 12, paddingHorizontal: 12, borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle },
  dropdownOptionActive: { backgroundColor: Colors.primaryDim },
  dropdownOptionText: { color: Colors.text, fontSize: 14, flexShrink: 1 },
  dropdownOptionTextActive: { color: Colors.primary, fontWeight: '700' },
  dropdownOptionLeft: { flexDirection: 'column', flex: 1, marginRight: 8 },
  dropdownOptionCount: { color: Colors.textTertiary, fontSize: 11, marginTop: 2 },
  dropdownEmpty: { color: Colors.textTertiary, fontSize: 13, padding: 16, textAlign: 'center' },
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
  groupTitle: { color: Colors.text, fontSize: 16, fontWeight: '800', flex: 1, marginRight: 8 },
  groupMeta: { color: Colors.textSecondary, fontSize: 12, fontWeight: '600' },
  pickRow: { paddingVertical: 10, borderTopWidth: 1, borderTopColor: Colors.borderSubtle },
  pickRowTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  pickOpponent: { color: Colors.text, fontWeight: '700', fontSize: 14, flex: 1, marginRight: 8 },
  pickResult: { fontWeight: '800', fontSize: 13 },
  pickMeta: { color: Colors.textSecondary, marginTop: 4, lineHeight: 18, fontSize: 12 },
  aggText: { color: Colors.textSecondary, marginTop: 10, fontSize: 12, lineHeight: 18 },
  emptyState: { alignItems: 'center', marginTop: 30, padding: 20 },
  emptyTitle: { color: Colors.text, fontSize: 16, fontWeight: '800', marginTop: 12 },
  emptyBody: { color: Colors.textSecondary, fontSize: 13, marginTop: 6, textAlign: 'center' },
  loadingWrap: { alignItems: 'center', marginTop: 40 },
  loadingText: { color: Colors.textSecondary, fontSize: 14, marginTop: 12 },
  errorWrap: { alignItems: 'center', marginTop: 40, padding: 20 },
  errorTitle: { color: Colors.text, fontSize: 16, fontWeight: '800', marginTop: 12 },
  errorBody: { color: Colors.textSecondary, fontSize: 13, marginTop: 6, textAlign: 'center', marginBottom: 16 },
  retryBtn: { backgroundColor: Colors.primary, paddingHorizontal: 24, paddingVertical: 12, borderRadius: 14 },
  retryBtnText: { color: '#000', fontWeight: '800', fontSize: 14 },
});
