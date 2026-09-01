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

// ── Human-readable prop type labels ──────────────────────────────────────────
const PROP_LABELS: Record<string, string> = {
  // Soccer
  pass_attempts: 'Pass Attempts',
  passes: 'Passes',
  key_passes: 'Key Passes',
  shots: 'Shots',
  shots_on_target: 'Shots on Target',
  goals: 'Goals',
  assists: 'Assists',
  tackles: 'Tackles',
  interceptions: 'Interceptions',
  clearances: 'Clearances',
  dribbles: 'Dribbles',
  fouls: 'Fouls',
  yellow_cards: 'Yellow Cards',
  minutes_played: 'Minutes Played',
  saves: 'Saves',
  goals_conceded: 'Goals Conceded',
  possession_pct: 'Possession %',
  // MLB
  hits: 'Hits',
  home_runs: 'Home Runs',
  rbi: 'RBI',
  runs: 'Runs',
  walks: 'Walks',
  strikeouts: 'Strikeouts',
  total_bases: 'Total Bases',
  stolen_bases: 'Stolen Bases',
  doubles: 'Doubles',
  plate_appearances: 'Plate Appearances',
  hits_allowed: 'Hits Allowed',
  hits_runs_rbis: 'H+R+RBI',
  hitter_fantasy_points: 'Fantasy Pts (Hitter)',
  pitcher_fantasy_points: 'Fantasy Pts (Pitcher)',
  earned_runs: 'Earned Runs',
  innings_pitched: 'Innings Pitched',
  pitching_strikeouts: 'Strikeouts (Pitcher)',
  // CS2
  map1_kills: 'Kills (Map 1)',
  map1_deaths: 'Deaths (Map 1)',
  map1_assists: 'Assists (Map 1)',
  map1_adr: 'ADR (Map 1)',
  map1_rating: 'Rating (Map 1)',
  map1_first_kills: 'First Kills (Map 1)',
  map1_headshot_pct: 'HS% (Map 1)',
  map3_kills: 'Kills (Map 3)',
  map3_deaths: 'Deaths (Map 3)',
  map3_assists: 'Assists (Map 3)',
  map3_headshots: 'Headshots (Map 3)',
  map3_adr: 'ADR (Map 3)',
  maps_1_2_kills: 'Kills (Maps 1-2)',
  maps_1_2_deaths: 'Deaths (Maps 1-2)',
  maps_1_2_assists: 'Assists (Maps 1-2)',
  maps_1_2_headshots: 'Headshots (Maps 1-2)',
  maps_1_3_kills: 'Kills (Maps 1-3)',
  // WTA
  aces: 'Aces',
  double_faults: 'Double Faults',
  service_games_won: 'Service Games Won',
  games_won: 'Games Won',
};

function propLabel(raw: string): string {
  return PROP_LABELS[raw] || raw.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// Strip accents so "Moisés" and "Moises" group together
function normalizeAccents(s: string): string {
  return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
}

type FilterKey = 'player' | 'opponent' | 'venue' | 'position' | 'propType' | 'league' | 'result';

interface FilterState {
  player: string;
  opponent: string;
  venue: string;
  position: string;
  propType: string;
  league: string;
  result: string;
}

const FILTER_LABELS: Record<FilterKey, string> = {
  player: 'Player',
  opponent: 'Opponent',
  venue: 'Venue',
  position: 'Position',
  propType: 'Prop Type',
  league: 'League',
  result: 'Result',
};

const DEFAULT_FILTERS: FilterState = {
  player: 'All',
  opponent: 'All',
  venue: 'All',
  position: 'All',
  propType: 'All',
  league: 'All',
  result: 'All',
};

function getLeagueLabel(p: Pick) {
  return p.leagueName || (p.leagueId ? `League ${p.leagueId}` : 'Unknown');
}

function normalizeResult(p: Pick): string {
  const r = (p.result || '').toLowerCase();
  if (r === 'hit' || r === 'won') return 'Hit';
  if (r === 'miss' || r === 'lost') return 'Miss';
  if (r === 'push') return 'Push';
  if (r === 'dnp') return 'DNP';
  return '';
}

function recLabel(rec?: string): string {
  if (!rec) return '';
  const u = rec.toUpperCase();
  if (u === 'OVER') return 'OVER';
  if (u === 'UNDER') return 'UNDER';
  return u;
}

// Build a canonical display name from a set of names that normalise to same key
function canonicalName(names: string[]): string {
  // Prefer the one with the most characters (usually the accented version)
  return names.reduce((a, b) => (a.length >= b.length ? a : b));
}

export default function MatchupsScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['matchups', session?.email],
    queryFn: async () =>
      session
        ? getMatchups(session.email, session.token)
        : { picks: [], options: { players: [], opponents: [], positions: [], propTypes: [], leagues: [], results: [] } },
    enabled: !!session,
    staleTime: 60000,
    retry: 3,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
  });

  const allPicks = data?.picks || [];

  // ── Normalize + deduplicate player names by accent-stripped key ────────────
  const { normalizedPicks, playerDisplayMap } = useMemo(() => {
    // Map accent-normalised key → canonical display name
    const keyToNames = new Map<string, string[]>();
    for (const p of allPicks) {
      const name = (p.playerName || '').trim();
      if (!name) continue;
      const key = normalizeAccents(name);
      const arr = keyToNames.get(key) || [];
      if (!arr.includes(name)) arr.push(name);
      keyToNames.set(key, arr);
    }
    const displayMap = new Map<string, string>(); // any variant → canonical
    for (const [, names] of keyToNames) {
      const canonical = canonicalName(names);
      for (const n of names) displayMap.set(n, canonical);
    }
    // Replace each pick's playerName with its canonical form and deduplicate by pickId
    const deduped = [];
    const seenPickIds = new Set<string>();
    for (const p of allPicks) {
      const id = (p.pickId || '').toString();
      if (id && seenPickIds.has(id)) continue;
      if (id) seenPickIds.add(id);
      deduped.push({
        ...p,
        playerName: displayMap.get(p.playerName || '') || p.playerName,
      });
    }
    return { normalizedPicks: deduped, playerDisplayMap: displayMap };
  }, [allPicks]);

  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [openDropdown, setOpenDropdown] = useState<FilterKey | null>(null);
  const [dropdownSearch, setDropdownSearch] = useState('');
  const [reviewed, setReviewed] = useState(false);

  // ── Picks matching all filters EXCEPT the one currently open ─────────────
  // This makes each dropdown show options that are still reachable given
  // the other active filters (so CS2 props disappear when opponent=Liverpool).
  const picksForDropdown = useCallback(
    (excludeKey: FilterKey): Pick[] => {
      return normalizedPicks.filter((p) => {
        const venue = p.playerIsHome ? 'Home' : p.playerIsHome === false ? 'Away' : '';
        const checks: Record<FilterKey, boolean> = {
          player: filters.player === 'All' || p.playerName === filters.player,
          opponent: filters.opponent === 'All' || p.opponentName === filters.opponent,
          venue: filters.venue === 'All' || venue === filters.venue,
          position: filters.position === 'All' || p.position === filters.position,
          propType: filters.propType === 'All' || p.propType === filters.propType,
          league: filters.league === 'All' || getLeagueLabel(p) === filters.league,
          result: filters.result === 'All' || normalizeResult(p) === filters.result,
        };
        // Exclude the current dropdown's own filter so it shows all available options
        return Object.entries(checks)
          .filter(([k]) => k !== excludeKey)
          .every(([, v]) => v);
      });
    },
    [normalizedPicks, filters]
  );

  // ── Compute option lists dynamically based on other active filters ────────
  const optionLists = useMemo((): Record<FilterKey, string[]> => {
    const forKey = (key: FilterKey) => {
      const subset = picksForDropdown(key);
      let values: string[] = [];
      if (key === 'player') values = [...new Set(subset.map((p) => p.playerName || '').filter(Boolean))].sort();
      else if (key === 'opponent') values = [...new Set(subset.map((p) => p.opponentName || '').filter(Boolean))].sort();
      else if (key === 'position') values = [...new Set(subset.map((p) => p.position || '').filter(Boolean))].sort();
      else if (key === 'propType')
        values = [...new Set(subset.map((p) => p.propType || '').filter(Boolean))].sort((a, b) =>
          propLabel(a).localeCompare(propLabel(b))
        );
      else if (key === 'league') values = [...new Set(subset.map((p) => getLeagueLabel(p)).filter((l) => l !== 'Unknown'))].sort();
      else if (key === 'venue') {
        const venueSet = new Set<string>();
        for (const p of subset) {
          const v = p.playerIsHome ? 'Home' : p.playerIsHome === false ? 'Away' : '';
          if (v) venueSet.add(v);
        }
        values = [...venueSet].sort();
      } else values = ['Hit', 'Miss', 'Push', 'DNP'];
      return ['All', ...values];
    };
    return {
      player: forKey('player'),
      opponent: forKey('opponent'),
      venue: forKey('venue'),
      position: forKey('position'),
      propType: forKey('propType'),
      league: forKey('league'),
      result: forKey('result'),
    };
  }, [picksForDropdown]);

  const filteredOptions = useMemo(() => {
    if (!openDropdown || !dropdownSearch.trim()) return optionLists;
    const term = dropdownSearch.trim().toLowerCase();
    return {
      ...optionLists,
      [openDropdown]: optionLists[openDropdown].filter((o) => {
        if (o === 'All') return true;
        const display = openDropdown === 'propType' ? propLabel(o) : o;
        return display.toLowerCase().includes(term);
      }),
    };
  }, [optionLists, openDropdown, dropdownSearch]);

  const filteredPicks = useMemo(() => {
    return normalizedPicks.filter((p) => {
      const venue = p.playerIsHome ? 'Home' : p.playerIsHome === false ? 'Away' : '';
      return (
        (filters.player === 'All' || p.playerName === filters.player) &&
        (filters.opponent === 'All' || p.opponentName === filters.opponent) &&
        (filters.venue === 'All' || venue === filters.venue) &&
        (filters.position === 'All' || p.position === filters.position) &&
        (filters.propType === 'All' || p.propType === filters.propType) &&
        (filters.league === 'All' || getLeagueLabel(p) === filters.league) &&
        (filters.result === 'All' || normalizeResult(p) === filters.result)
      );
    });
  }, [normalizedPicks, filters]);

  const stats = useMemo(() => {
    const total = filteredPicks.length;
    const hits = filteredPicks.filter((p) => normalizeResult(p) === 'Hit').length;
    const misses = filteredPicks.filter((p) => normalizeResult(p) === 'Miss').length;
    const pushes = filteredPicks.filter((p) => normalizeResult(p) === 'Push').length;
    const dnps = filteredPicks.filter((p) => normalizeResult(p) === 'DNP').length;
    const settled = hits + misses;
    const winRate = settled > 0 ? Math.round((hits / settled) * 100) : null;
    const lines = filteredPicks.map((p) => p.line).filter((v): v is number => typeof v === 'number');
    const averageLine = lines.length ? lines.reduce((a, b) => a + b, 0) / lines.length : null;
    const actuals = filteredPicks.map((p) => p.actualValue).filter((v): v is number => typeof v === 'number' && v > 0);
    const averageActual = actuals.length ? actuals.reduce((a, b) => a + b, 0) / actuals.length : null;
    return { total, hits, misses, pushes, dnps, winRate, averageLine, averageActual };
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
        .filter(([, v]) => v !== 'All')
        .map(([k, v]) => {
          const label = FILTER_LABELS[k as FilterKey];
          const display = k === 'propType' ? propLabel(v) : v;
          return `${label}: ${display}`;
        }),
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
    const hasSearch = key === 'player' || key === 'opponent' || key === 'league' || key === 'propType';
    const selectedDisplay = selected === 'All' ? 'All' : key === 'propType' ? propLabel(selected) : selected;

    return (
      <View key={key} style={styles.dropdownSection}>
        <TouchableOpacity onPress={() => handleOpenDropdown(key)} style={styles.dropdownBtn} activeOpacity={0.75}>
          <Text style={styles.dropdownLabel}>{FILTER_LABELS[key]}</Text>
          <View style={styles.dropdownRight}>
            <Text
              style={[styles.dropdownValue, selected !== 'All' && styles.dropdownValueActive]}
              numberOfLines={1}
            >
              {selectedDisplay}
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
                  autoCorrect={false}
                  autoCapitalize="none"
                  spellCheck={false}
                />
              </View>
            )}
            <ScrollView style={styles.dropdownScroll} nestedScrollEnabled keyboardShouldPersistTaps="handled">
              {values.length === 0 ? (
                <Text style={styles.dropdownEmpty}>No options</Text>
              ) : (
                values.map((value) => {
                  const isSelected = selected === value;
                  const display = value === 'All' ? 'All' : key === 'propType' ? propLabel(value) : value;
                  // Count picks matching this option + all other active filters
                  const subsetForCount = picksForDropdown(key);
                  const count =
                    value === 'All'
                      ? subsetForCount.length
                      : subsetForCount.filter((p) => {
                          if (key === 'player') return p.playerName === value;
                          if (key === 'opponent') return p.opponentName === value;
                          if (key === 'venue') {
                            const v = p.playerIsHome ? 'Home' : p.playerIsHome === false ? 'Away' : '';
                            return v === value;
                          }
                          if (key === 'position') return p.position === value;
                          if (key === 'propType') return p.propType === value;
                          if (key === 'league') return getLeagueLabel(p) === value;
                          return normalizeResult(p) === value;
                        }).length;
                  return (
                    <TouchableOpacity
                      key={value}
                      onPress={() => handleSelect(key, value)}
                      style={[styles.dropdownOption, isSelected && styles.dropdownOptionActive]}
                      activeOpacity={0.75}
                    >
                      <View style={styles.dropdownOptionLeft}>
                        <Text
                          style={[styles.dropdownOptionText, isSelected && styles.dropdownOptionTextActive]}
                          numberOfLines={1}
                        >
                          {display}
                        </Text>
                        {value !== 'All' && (
                          <Text style={styles.dropdownOptionCount}>
                            {count} pick{count !== 1 ? 's' : ''}
                          </Text>
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
        <Text style={styles.subtitle}>Search every settled pick by player, opponent, venue, position, prop, league, and result.</Text>
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
            <Text style={styles.loadingText}>Loading settled picks…</Text>
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
            {/* Active filter summary */}
            {activeSummary.length > 0 && (
              <View style={styles.summaryCard}>
                <Text style={styles.summaryTitle}>Active filters</Text>
                <Text style={styles.summaryText}>{activeSummary.join(' · ')}</Text>
              </View>
            )}

            {/* Stats grid */}
            <View style={styles.statsGrid}>
              <StatTile label="Total" value={String(stats.total)} />
              <StatTile label="Hits" value={String(stats.hits)} accent={Colors.success} />
              <StatTile label="Misses" value={String(stats.misses)} accent={Colors.error} />
              <StatTile label="Hit rate" value={stats.winRate != null ? `${stats.winRate}%` : '—'} accent={Colors.primary} />
              {stats.pushes > 0 && <StatTile label="Pushes" value={String(stats.pushes)} accent={Colors.push} />}
              {stats.dnps > 0 && <StatTile label="DNPs" value={String(stats.dnps)} accent={Colors.dnp} />}
              {stats.averageLine != null && <StatTile label="Avg line" value={stats.averageLine.toFixed(1)} accent={Colors.accent} />}
              {stats.averageActual != null && <StatTile label="Avg actual" value={stats.averageActual.toFixed(1)} accent={Colors.textSecondary} />}
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
                const settled = playerHits + playerMisses;
                const winRate = settled > 0 ? Math.round((playerHits / settled) * 100) : null;

                // Per-prop breakdown
                const propAgg = new Map<string, { hits: number; total: number; actuals: number[] }>();
                for (const p of playerPicks) {
                  const key = p.propType || 'Unknown';
                  const curr = propAgg.get(key) || { hits: 0, total: 0, actuals: [] };
                  curr.total += 1;
                  if (normalizeResult(p) === 'Hit') curr.hits += 1;
                  if (typeof p.actualValue === 'number' && p.actualValue > 0) curr.actuals.push(p.actualValue);
                  propAgg.set(key, curr);
                }

                return (
                  <View key={player} style={styles.groupCard}>
                    {/* Player header */}
                    <View style={styles.groupHeader}>
                      <Text style={styles.groupTitle}>{player}</Text>
                      <Text style={styles.groupMeta}>
                        {playerPicks.length} matchup{playerPicks.length !== 1 ? 's' : ''}
                      </Text>
                    </View>

                    {/* Matchup rows — one per unique opponent+prop combo */}
                    {playerPicks.map((pick) => {
                      const rec = recLabel(pick.recommendation);
                      const actual = typeof pick.actualValue === 'number' && pick.actualValue > 0 ? pick.actualValue : null;
                      const venue = pick.playerIsHome ? 'Home' : pick.playerIsHome === false ? 'Away' : '';
                      const count = pick.count || 1;
                      const hits = pick.hits || 0;
                      const misses = pick.misses || 0;
                      const winRate = pick.winRate ?? (hits + misses > 0 ? Math.round((hits / (hits + misses)) * 100) : 0);
                      const resColor = winRate > 50 ? Colors.success : winRate < 50 ? Colors.error : Colors.textSecondary;

                      return (
                        <View key={pick.pickId || `${player}-${pick.propType}-${pick.line}`} style={styles.pickRow}>
                          <View style={styles.pickRowTop}>
                            <View style={styles.pickRowLeft}>
                              <Text style={styles.pickOpponent}>
                                {pick.opponentName || 'Opponent unknown'}
                                {venue ? (
                                  <Text style={[styles.venueBadge, venue === 'Home' ? styles.venueHome : styles.venueAway]}>
                                    {' '}{venue}
                                  </Text>
                                ) : null}
                              </Text>
                              {pick.matchScore ? (
                                <Text style={styles.pickScore}>{pick.matchScore}</Text>
                              ) : null}
                            </View>
                            <View style={styles.pickRowRight}>
                              {rec ? (
                                <View style={[styles.recBadge, rec === 'OVER' ? styles.recOver : styles.recUnder]}>
                                  <Text style={styles.recText}>{rec}</Text>
                                </View>
                              ) : null}
                              <Text style={[styles.pickResult, { color: resColor }]}>{winRate}%</Text>
                            </View>
                          </View>
                          <Text style={styles.pickMeta}>
                            {propLabel(pick.propType || '')} · Avg line {pick.line ?? '—'}
                            {actual != null ? `  →  Avg actual: ${actual}` : ''}
                            {pick.position ? `  ·  ${pick.position}` : ''}
                            {getLeagueLabel(pick) !== 'Unknown' ? `  ·  ${getLeagueLabel(pick)}` : ''}
                          </Text>
                          <Text style={styles.matchupCount}>
                            {count} pick{count !== 1 ? 's' : ''}: {hits}H · {misses}M
                            {(pick.pushes || 0) > 0 ? ` · ${pick.pushes}P` : ''}
                            {(pick.dnps || 0) > 0 ? ` · ${pick.dnps}DNP` : ''}
                          </Text>
                        </View>
                      );
                    })}
                  </View>
                );
              })
            )}
          </View>
        )}

        {!isLoading && !error && !reviewed && allPicks.length > 0 && (
          <View style={styles.emptyState}>
            <Ionicons name="options-outline" size={40} color={Colors.textTertiary} />
            <Text style={styles.emptyTitle}>Set filters and tap Review</Text>
            <Text style={styles.emptyBody}>
              {allPicks.length} settled pick{allPicks.length !== 1 ? 's' : ''} available to search.
            </Text>
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
  dropdownCard: {
    marginBottom: 12,
    padding: 14,
    borderRadius: 16,
    backgroundColor: Colors.card,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  dropdownSection: { borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle, paddingVertical: 10 },
  dropdownBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  dropdownLabel: {
    color: Colors.textSecondary,
    fontSize: 12,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
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
  dropdownSearchInput: { flex: 1, color: Colors.text, fontSize: 16, paddingVertical: 4 },
  dropdownOption: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 12,
    paddingHorizontal: 12,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
  },
  dropdownOptionActive: { backgroundColor: Colors.primaryDim },
  dropdownOptionText: { color: Colors.text, fontSize: 14, flexShrink: 1 },
  dropdownOptionTextActive: { color: Colors.primary, fontWeight: '700' },
  dropdownOptionLeft: { flexDirection: 'column', flex: 1, marginRight: 8 },
  dropdownOptionCount: { color: Colors.textTertiary, fontSize: 11, marginTop: 2 },
  dropdownEmpty: { color: Colors.textTertiary, fontSize: 13, padding: 16, textAlign: 'center' },
  actions: { flexDirection: 'row', gap: 10, marginBottom: 16 },
  reviewBtn: { flex: 1, backgroundColor: Colors.primary, borderRadius: 14, paddingVertical: 14, alignItems: 'center' },
  reviewBtnText: { color: '#000', fontWeight: '900', fontSize: 15 },
  resetBtn: {
    paddingHorizontal: 18,
    borderRadius: 14,
    backgroundColor: Colors.card,
    borderWidth: 1,
    borderColor: Colors.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  resetBtnText: { color: Colors.text, fontWeight: '700', fontSize: 14 },
  results: { marginTop: 4 },
  summaryCard: {
    marginBottom: 14,
    padding: 14,
    borderRadius: 16,
    backgroundColor: Colors.card,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  summaryTitle: { color: Colors.text, fontWeight: '800', marginBottom: 6, fontSize: 14 },
  summaryText: { color: Colors.textSecondary, lineHeight: 20, fontSize: 13 },
  statsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginBottom: 10 },
  statTile: {
    width: '31%',
    minWidth: 100,
    padding: 12,
    backgroundColor: Colors.card,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  statValue: { fontSize: 18, fontWeight: '900' },
  statLabel: { color: Colors.textSecondary, fontSize: 11, marginTop: 4 },
  groupCard: {
    marginTop: 12,
    padding: 14,
    borderRadius: 16,
    backgroundColor: Colors.card,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  groupHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  groupTitle: { color: Colors.text, fontSize: 16, fontWeight: '800', flex: 1, marginRight: 8 },
  groupMeta: { color: Colors.textSecondary, fontSize: 12, fontWeight: '600' },
  pickRow: { paddingVertical: 10, borderTopWidth: 1, borderTopColor: Colors.borderSubtle },
  pickRowTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  pickRowLeft: { flex: 1, marginRight: 8 },
  pickRowRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  pickOpponent: { color: Colors.text, fontWeight: '700', fontSize: 14 },
  venueBadge: { fontSize: 10, fontWeight: '800', paddingHorizontal: 5, paddingVertical: 1, borderRadius: 4, overflow: 'hidden' },
  venueHome: { backgroundColor: 'rgba(16, 185, 129, 0.25)', color: Colors.success },
  venueAway: { backgroundColor: 'rgba(99, 102, 241, 0.25)', color: '#818cf8' },
  pickScore: { color: Colors.textTertiary, fontSize: 11, marginTop: 2 },
  pickResult: { fontWeight: '800', fontSize: 13 },
  pickMeta: { color: Colors.textSecondary, marginTop: 5, lineHeight: 18, fontSize: 12 },
  matchupCount: { color: Colors.textTertiary, marginTop: 3, fontSize: 11, fontWeight: '600' },
  recBadge: {
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 6,
  },
  recOver: { backgroundColor: 'rgba(16, 185, 129, 0.2)' },
  recUnder: { backgroundColor: 'rgba(99, 102, 241, 0.2)' },
  recText: { fontSize: 11, fontWeight: '800', color: Colors.text },
  aggSection: {
    marginTop: 12,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: Colors.borderSubtle,
    gap: 6,
  },
  aggRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  aggProp: { color: Colors.textSecondary, fontSize: 12, flex: 1 },
  aggStat: { color: Colors.text, fontSize: 12, fontWeight: '700' },
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
