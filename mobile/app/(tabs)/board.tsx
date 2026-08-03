/**
 * Board Tab — Professional prop discovery screen.
 *
 * Markets are sourced from SportsGameOdds (PrizePicks / Underdog) and
 * grouped by game. Tapping ANALYZE stores the market in boardStore and
 * navigates to the Predict tab, which picks it up on focus and pre-fills
 * the correct analysis form.
 *
 * SportsGameOdds is market-reference only. Projection math, fixtures, and
 * settlement remain on API-Football / Reverse Picks engines.
 */
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  View, Text, StyleSheet, SectionList, TouchableOpacity,
  TextInput, ActivityIndicator, RefreshControl, Platform,
  ScrollView, Animated,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useFocusEffect } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import * as Haptics from 'expo-haptics';
import Colors from '@/constants/colors';
import { getMarketBoard, MarketBoardItem } from '@/lib/api';
import { setBoardPendingMarket } from '@/lib/boardStore';
import { useAuth } from '@/contexts/AuthContext';

// ─── Sport tab config ────────────────────────────────────────────────────────
const SPORT_TABS = [
  { id: 'all',          label: 'All',       emoji: '🎯' },
  { id: 'mlb',          label: 'Baseball',  emoji: '⚾' },
  { id: 'nba',          label: 'Basketball',emoji: '🏀' },
  { id: 'soccer',       label: 'Soccer',    emoji: '⚽' },
  { id: 'nfl',          label: 'Football',  emoji: '🏈' },
  { id: 'nhl',          label: 'Hockey',    emoji: '🏒' },
  { id: 'wta',          label: 'Tennis',    emoji: '🎾' },
  { id: 'mma',          label: 'MMA',       emoji: '🥊' },
  { id: 'golf',         label: 'Golf',      emoji: '⛳' },
  { id: 'handball',     label: 'Handball',  emoji: '🤾' },
  { id: 'horse_racing', label: 'Racing',    emoji: '🏇' },
  { id: 'other',        label: 'Other',     emoji: '📊' },
] as const;

type SportId = (typeof SPORT_TABS)[number]['id'];

// ─── Bookmaker display config ─────────────────────────────────────────────────
const BOOKMAKER_CONFIG: Record<string, { label: string; color: string }> = {
  prizepicks: { label: 'PrizePicks', color: '#00d48b' },
  underdog:   { label: 'Underdog',   color: '#F97316' },
};

// ─── Game section (one per event) ────────────────────────────────────────────
interface GameSection {
  key: string;
  homeTeam: string;
  awayTeam: string;
  eventStart?: string;
  leagueName?: string;
  sportName?: string;
  sport?: string;
  providerCoverage: string[];
  data: MarketBoardItem[];
}

function buildSections(markets: MarketBoardItem[]): GameSection[] {
  const map = new Map<string, GameSection>();
  for (const m of markets) {
    const key = m.eventId || `${m.homeTeam}|${m.awayTeam}`;
    if (!map.has(key)) {
      map.set(key, {
        key,
        homeTeam: m.homeTeam || '',
        awayTeam: m.awayTeam || '',
        eventStart: m.eventStart,
        leagueName: m.leagueName,
        sportName: m.sportName,
        sport: m.sport,
        providerCoverage: m.providerCoverage || [],
        data: [],
      });
    }
    const sec = map.get(key)!;
    // Union bookmaker coverage across markets in the same game
    for (const b of (m.providerCoverage || [])) {
      if (!sec.providerCoverage.includes(b)) sec.providerCoverage.push(b);
    }
    sec.data.push(m);
  }
  // Sort sections by start time
  return Array.from(map.values()).sort((a, b) => {
    const ta = a.eventStart ? new Date(a.eventStart).getTime() : 0;
    const tb = b.eventStart ? new Date(b.eventStart).getTime() : 0;
    return ta - tb;
  });
}

function formatGameTime(iso?: string): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
      ' · ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  } catch { return ''; }
}

// ─── Skeleton card ────────────────────────────────────────────────────────────
function SkeletonCard() {
  const opacity = useRef(new Animated.Value(0.3)).current;
  useEffect(() => {
    const anim = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.7, duration: 800, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.3, duration: 800, useNativeDriver: true }),
      ])
    );
    anim.start();
    return () => anim.stop();
  }, []);
  return (
    <Animated.View style={[styles.skeletonCard, { opacity }]}>
      <View style={styles.skeletonRow}>
        <View style={styles.skeletonName} />
        <View style={styles.skeletonLine} />
      </View>
      <View style={styles.skeletonSub} />
      <View style={styles.skeletonDivider} />
      <View style={styles.skeletonBtn} />
    </Animated.View>
  );
}

// ─── Game section header ──────────────────────────────────────────────────────
function SectionHeader({ section }: { section: GameSection }) {
  const bookmakers = section.providerCoverage;
  const time = formatGameTime(section.eventStart);
  const sportTab = SPORT_TABS.find(s => s.id === section.sport);
  return (
    <View style={styles.sectionHeader}>
      <View style={styles.sectionHeaderLeft}>
        <View style={styles.sectionTeamRow}>
          {sportTab && <Text style={styles.sectionEmoji}>{sportTab.emoji}</Text>}
          <Text style={styles.sectionTeams} numberOfLines={1}>
            {section.homeTeam} vs {section.awayTeam}
          </Text>
        </View>
        <Text style={styles.sectionMeta} numberOfLines={1}>
          {section.leagueName || section.sportName || ''}
          {time ? `  ·  ${time}` : ''}
        </Text>
      </View>
      <View style={styles.sectionBadges}>
        {bookmakers.map(bm => {
          const cfg = BOOKMAKER_CONFIG[bm];
          if (!cfg) return null;
          return (
            <View key={bm} style={[styles.bookBadge, { borderColor: cfg.color + '55' }]}>
              <View style={[styles.bookDot, { backgroundColor: cfg.color }]} />
              <Text style={[styles.bookLabel, { color: cfg.color }]}>{cfg.label}</Text>
            </View>
          );
        })}
      </View>
    </View>
  );
}

// ─── Player market card ───────────────────────────────────────────────────────
function MarketCard({
  market,
  onAnalyze,
}: {
  market: MarketBoardItem;
  onAnalyze: (m: MarketBoardItem) => void;
}) {
  const supported = market.analysisSupported;
  const lineDisplay = market.marketLine != null
    ? String(market.marketLine)
    : (market.marketSelection ?? '—');

  const primaryBm = (market.providerCoverage || [])[0] || 'prizepicks';
  const bmCfg = BOOKMAKER_CONFIG[primaryBm] ?? { label: primaryBm, color: Colors.primary };

  return (
    <View style={styles.card}>
      {/* Top row: player + line */}
      <View style={styles.cardTop}>
        <View style={styles.cardPlayerCol}>
          <Text style={styles.cardPlayer} numberOfLines={1}>{market.playerName || 'Player'}</Text>
          <Text style={styles.cardTeam} numberOfLines={1}>
            {market.homeTeam || market.awayTeam || ''}
          </Text>
        </View>
        <View style={styles.cardLineBadge}>
          <Text style={[styles.cardLine, !supported && styles.cardLineDim]}>
            {lineDisplay}
          </Text>
          <Text style={styles.cardPropLabel} numberOfLines={1}>
            {market.propLabel || market.propType || ''}
          </Text>
        </View>
      </View>

      {/* Divider */}
      <View style={styles.cardDivider} />

      {/* Bottom row: bookmaker + action */}
      <View style={styles.cardBottom}>
        <View style={styles.cardBmRow}>
          <View style={[styles.cardBmDot, { backgroundColor: bmCfg.color }]} />
          <Text style={[styles.cardBmLabel, { color: bmCfg.color }]}>{bmCfg.label}</Text>
          {(market.providerCoverage || []).length > 1 && (
            <Text style={styles.cardBmExtra}>
              {' +' + ((market.providerCoverage || []).length - 1)}
            </Text>
          )}
        </View>
        <TouchableOpacity
          style={[styles.analyzeBtn, !supported && styles.analyzeBtnDim]}
          onPress={() => supported && onAnalyze(market)}
          activeOpacity={supported ? 0.78 : 1}
        >
          <Text style={[styles.analyzeBtnText, !supported && styles.analyzeBtnTextDim]}>
            {supported ? 'ANALYZE' : 'MARKET ONLY'}
          </Text>
          <Ionicons
            name="arrow-forward"
            size={12}
            color={supported ? Colors.primary : Colors.textTertiary}
          />
        </TouchableOpacity>
      </View>
    </View>
  );
}

// ─── Main screen ──────────────────────────────────────────────────────────────
export default function BoardScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();

  const [markets, setMarkets] = useState<MarketBoardItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const [sportFilter, setSportFilter] = useState<SportId>('all');
  const [propFilter, setPropFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');

  // ── Load markets ────────────────────────────────────────────────────────────
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const data = await getMarketBoard({ hours: 72, limit: 200 });
      setMarkets(data?.markets || []);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load board');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // Load on first mount
  useEffect(() => { load(); }, [load]);

  // Silent refresh when tab is focused (stale after > 90 s)
  const lastLoadRef = useRef<number>(0);
  useFocusEffect(useCallback(() => {
    const now = Date.now();
    if (now - lastLoadRef.current > 90_000) {
      lastLoadRef.current = now;
      load(true);
    }
  }, [load]));

  // ── Derived data ─────────────────────────────────────────────────────────────
  const sportFiltered = useMemo(() =>
    sportFilter === 'all' ? markets : markets.filter(m => m.sport === sportFilter),
    [markets, sportFilter]
  );

  const propFiltered = useMemo(() =>
    propFilter === 'all' ? sportFiltered : sportFiltered.filter(m =>
      (m.propLabel || m.propType || '') === propFilter
    ),
    [sportFiltered, propFilter]
  );

  const searched = useMemo(() => {
    if (!searchQuery.trim()) return propFiltered;
    const q = searchQuery.toLowerCase();
    return propFiltered.filter(m =>
      (m.playerName || '').toLowerCase().includes(q) ||
      (m.homeTeam || '').toLowerCase().includes(q) ||
      (m.awayTeam || '').toLowerCase().includes(q)
    );
  }, [propFiltered, searchQuery]);

  const sections = useMemo(() => buildSections(searched), [searched]);

  // Dynamic prop labels from sport-filtered markets (so the filter stays relevant)
  const propOptions = useMemo(() => {
    const labels = new Set<string>();
    for (const m of sportFiltered) {
      const lbl = m.propLabel || m.propType;
      if (lbl) labels.add(lbl);
    }
    return ['all', ...Array.from(labels).sort()];
  }, [sportFiltered]);

  // Reset prop filter when sport changes
  useEffect(() => { setPropFilter('all'); }, [sportFilter]);

  // ── Which sport tabs have live markets ──────────────────────────────────────
  const sportsWithMarkets = useMemo(() => {
    const s = new Set<string>(markets.map(m => m.sport || ''));
    return s;
  }, [markets]);

  // ── Analyze handler ─────────────────────────────────────────────────────────
  const handleAnalyze = useCallback((market: MarketBoardItem) => {
    Haptics.selectionAsync();
    setBoardPendingMarket(market);
    router.push('/(tabs)/scan');
  }, []);

  // ── Refresh ─────────────────────────────────────────────────────────────────
  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    load(true);
  }, [load]);

  // ── Render helpers ───────────────────────────────────────────────────────────
  const renderItem = useCallback(({ item }: { item: MarketBoardItem }) => (
    <MarketCard market={item} onAnalyze={handleAnalyze} />
  ), [handleAnalyze]);

  const renderSectionHeader = useCallback(({ section }: { section: GameSection }) => (
    <SectionHeader section={section} />
  ), []);

  const keyExtractor = useCallback((item: MarketBoardItem, i: number) =>
    `${item.eventId || 'ev'}-${item.playerProviderId || item.playerName}-${item.propType}-${i}`,
    []
  );

  const topPad = Platform.OS === 'web' ? 67 : insets.top;

  // ── Loading skeleton ─────────────────────────────────────────────────────────
  if (loading && markets.length === 0) {
    return (
      <View style={[styles.root, { paddingTop: topPad }]}>
        <Header
          lastUpdated={lastUpdated}
          onRefresh={() => load()}
          loading={true}
        />
        <View style={styles.skeletonList}>
          {[...Array(5)].map((_, i) => <SkeletonCard key={i} />)}
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      <Header lastUpdated={lastUpdated} onRefresh={() => load()} loading={loading} />

      {/* ── Search ─────────────────────────────────────────────────────── */}
      <View style={styles.searchWrap}>
        <Ionicons name="search-outline" size={16} color={Colors.textTertiary} />
        <TextInput
          style={[styles.searchInput, Platform.OS === 'web' && { outlineWidth: 0 } as object]}
          placeholder="Search players, teams…"
          placeholderTextColor={Colors.textTertiary}
          value={searchQuery}
          onChangeText={setSearchQuery}
          returnKeyType="search"
          autoCapitalize="none"
          autoCorrect={false}
          clearButtonMode="while-editing"
        />
        {searchQuery.length > 0 && Platform.OS !== 'ios' && (
          <TouchableOpacity onPress={() => setSearchQuery('')} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Ionicons name="close-circle" size={16} color={Colors.textTertiary} />
          </TouchableOpacity>
        )}
      </View>

      {/* ── Sport tabs ──────────────────────────────────────────────────── */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.sportTabsRow}
        style={styles.sportTabsScroll}
      >
        {SPORT_TABS.map(tab => {
          const active = sportFilter === tab.id;
          const hasMarkets = tab.id === 'all' || sportsWithMarkets.has(tab.id);
          return (
            <TouchableOpacity
              key={tab.id}
              style={[styles.sportTab, active && styles.sportTabActive, !hasMarkets && styles.sportTabEmpty]}
              onPress={() => { setSportFilter(tab.id); Haptics.selectionAsync(); }}
              activeOpacity={0.75}
            >
              <Text style={styles.sportTabEmoji}>{tab.emoji}</Text>
              <Text style={[styles.sportTabLabel, active && styles.sportTabLabelActive, !hasMarkets && styles.sportTabLabelEmpty]}>
                {tab.label}
              </Text>
              {hasMarkets && tab.id !== 'all' && (
                <View style={[styles.sportTabDot, active && styles.sportTabDotActive]} />
              )}
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      {/* ── Prop type filter ────────────────────────────────────────────── */}
      {propOptions.length > 1 && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.propFilterRow}
          style={styles.propFilterScroll}
        >
          {propOptions.map(opt => (
            <TouchableOpacity
              key={opt}
              style={[styles.propChip, propFilter === opt && styles.propChipActive]}
              onPress={() => { setPropFilter(opt); Haptics.selectionAsync(); }}
              activeOpacity={0.75}
            >
              <Text style={[styles.propChipText, propFilter === opt && styles.propChipTextActive]}>
                {opt === 'all' ? 'All Props' : opt}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      )}

      {/* ── Error ───────────────────────────────────────────────────────── */}
      {error && (
        <View style={styles.errorBanner}>
          <Ionicons name="cloud-offline-outline" size={16} color={Colors.warning} />
          <Text style={styles.errorText}>{error}</Text>
          <TouchableOpacity onPress={() => load()} style={styles.retryBtn}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      )}

      {/* ── Main list ───────────────────────────────────────────────────── */}
      {sections.length === 0 && !loading ? (
        <EmptyState
          sportFilter={sportFilter}
          searchQuery={searchQuery}
          totalMarkets={markets.length}
          onRefresh={() => load()}
        />
      ) : (
        <SectionList
          sections={sections}
          keyExtractor={keyExtractor}
          renderItem={renderItem}
          renderSectionHeader={renderSectionHeader}
          stickySectionHeadersEnabled={false}
          contentContainerStyle={styles.listContent}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={handleRefresh}
              tintColor={Colors.primary}
              colors={[Colors.primary]}
            />
          }
          ItemSeparatorComponent={() => <View style={styles.itemSep} />}
          SectionSeparatorComponent={() => <View style={styles.sectionSep} />}
        />
      )}
    </View>
  );
}

// ─── Header component ─────────────────────────────────────────────────────────
function Header({
  lastUpdated,
  onRefresh,
  loading,
}: {
  lastUpdated: Date | null;
  onRefresh: () => void;
  loading: boolean;
}) {
  const updatedStr = lastUpdated
    ? lastUpdated.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
    : null;

  return (
    <View style={styles.header}>
      <View>
        <Text style={styles.headerTitle}>BOARD</Text>
        <Text style={styles.headerSub}>
          PrizePicks &amp; Underdog markets
          {updatedStr ? `  ·  ${updatedStr}` : ''}
        </Text>
      </View>
      <TouchableOpacity
        onPress={onRefresh}
        style={styles.refreshBtn}
        disabled={loading}
        activeOpacity={0.7}
      >
        {loading
          ? <ActivityIndicator size="small" color={Colors.primary} />
          : <Ionicons name="refresh-outline" size={20} color={Colors.primary} />
        }
      </TouchableOpacity>
    </View>
  );
}

// ─── Empty state ──────────────────────────────────────────────────────────────
function EmptyState({
  sportFilter,
  searchQuery,
  totalMarkets,
  onRefresh,
}: {
  sportFilter: SportId;
  searchQuery: string;
  totalMarkets: number;
  onRefresh: () => void;
}) {
  const sport = SPORT_TABS.find(s => s.id === sportFilter);
  let title = 'No live markets right now';
  let body = 'PrizePicks and Underdog haven\'t posted player lines for this window yet. Pull down to refresh or check back closer to game time.';

  if (searchQuery) {
    title = `No results for "${searchQuery}"`;
    body = 'Try a different name or clear the search to see all available markets.';
  } else if (sportFilter !== 'all' && totalMarkets > 0) {
    title = `No ${sport?.label || 'sport'} markets right now`;
    body = 'Switch to All Sports to browse every available line.';
  }

  return (
    <View style={styles.emptyWrap}>
      <Text style={styles.emptyEmoji}>
        {searchQuery ? '🔍' : (sport?.emoji ?? '📋')}
      </Text>
      <Text style={styles.emptyTitle}>{title}</Text>
      <Text style={styles.emptyBody}>{body}</Text>
      {!searchQuery && (
        <TouchableOpacity style={styles.emptyRefresh} onPress={onRefresh} activeOpacity={0.8}>
          <Ionicons name="refresh-outline" size={14} color={Colors.primary} />
          <Text style={styles.emptyRefreshText}>Refresh board</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: Colors.background,
  },

  // Header
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(57,255,20,0.1)',
  },
  headerTitle: {
    color: Colors.text,
    fontSize: 22,
    fontWeight: '900',
    letterSpacing: 2,
  },
  headerSub: {
    color: Colors.textTertiary,
    fontSize: 11,
    marginTop: 2,
    letterSpacing: 0.3,
  },
  refreshBtn: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: 'rgba(57,255,20,0.07)',
    alignItems: 'center',
    justifyContent: 'center',
  },

  // Search
  searchWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginHorizontal: 16,
    marginTop: 12,
    marginBottom: 4,
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    paddingHorizontal: 14,
    height: 42,
  },
  searchInput: {
    flex: 1,
    color: Colors.text,
    fontSize: 14,
    fontWeight: '500',
  },

  // Sport tabs
  sportTabsScroll: {
    marginTop: 10,
  },
  sportTabsRow: {
    paddingHorizontal: 16,
    gap: 8,
    paddingBottom: 2,
  },
  sportTab: {
    alignItems: 'center',
    gap: 3,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 24,
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.07)',
    minWidth: 68,
    position: 'relative',
  },
  sportTabActive: {
    backgroundColor: 'rgba(57,255,20,0.12)',
    borderColor: 'rgba(57,255,20,0.4)',
  },
  sportTabEmpty: {
    opacity: 0.4,
  },
  sportTabEmoji: {
    fontSize: 18,
  },
  sportTabLabel: {
    color: Colors.textSecondary,
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 0.3,
  },
  sportTabLabelActive: {
    color: Colors.primary,
  },
  sportTabLabelEmpty: {
    color: Colors.textTertiary,
  },
  sportTabDot: {
    position: 'absolute',
    top: 6,
    right: 8,
    width: 5,
    height: 5,
    borderRadius: 3,
    backgroundColor: Colors.textTertiary,
  },
  sportTabDotActive: {
    backgroundColor: Colors.primary,
  },

  // Prop filter
  propFilterScroll: {
    marginTop: 10,
  },
  propFilterRow: {
    paddingHorizontal: 16,
    gap: 7,
    paddingBottom: 2,
  },
  propChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.07)',
  },
  propChipActive: {
    backgroundColor: 'rgba(57,255,20,0.12)',
    borderColor: 'rgba(57,255,20,0.4)',
  },
  propChipText: {
    color: Colors.textSecondary,
    fontSize: 11,
    fontWeight: '700',
  },
  propChipTextActive: {
    color: Colors.primary,
  },

  // Error
  errorBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginHorizontal: 16,
    marginTop: 10,
    padding: 12,
    backgroundColor: 'rgba(255,149,0,0.1)',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: 'rgba(255,149,0,0.25)',
  },
  errorText: {
    color: Colors.warning,
    fontSize: 12,
    flex: 1,
    lineHeight: 16,
  },
  retryBtn: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    backgroundColor: 'rgba(255,149,0,0.15)',
    borderRadius: 8,
  },
  retryText: {
    color: Colors.warning,
    fontSize: 11,
    fontWeight: '700',
  },

  // Section list
  listContent: {
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 40,
  },
  itemSep: { height: 8 },
  sectionSep: { height: 4 },

  // Section header
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    paddingTop: 18,
    paddingBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(57,255,20,0.1)',
    marginBottom: 2,
  },
  sectionHeaderLeft: {
    flex: 1,
    marginRight: 10,
  },
  sectionTeamRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  sectionEmoji: {
    fontSize: 16,
  },
  sectionTeams: {
    color: Colors.text,
    fontSize: 15,
    fontWeight: '800',
    flex: 1,
  },
  sectionMeta: {
    color: Colors.textTertiary,
    fontSize: 11,
    marginTop: 3,
    letterSpacing: 0.2,
    paddingLeft: 22,
  },
  sectionBadges: {
    gap: 5,
    alignItems: 'flex-end',
  },
  bookBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 8,
    borderWidth: 1,
    backgroundColor: 'rgba(255,255,255,0.03)',
  },
  bookDot: {
    width: 5,
    height: 5,
    borderRadius: 3,
  },
  bookLabel: {
    fontSize: 9,
    fontWeight: '800',
    letterSpacing: 0.4,
  },

  // Market card
  card: {
    backgroundColor: '#111111',
    borderRadius: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    padding: 14,
    overflow: 'hidden',
  },
  cardTop: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  cardPlayerCol: {
    flex: 1,
    marginRight: 12,
  },
  cardPlayer: {
    color: Colors.text,
    fontSize: 16,
    fontWeight: '800',
    letterSpacing: 0.2,
  },
  cardTeam: {
    color: Colors.textSecondary,
    fontSize: 11,
    marginTop: 3,
    fontWeight: '500',
  },
  cardLineBadge: {
    alignItems: 'flex-end',
    minWidth: 56,
  },
  cardLine: {
    color: Colors.primary,
    fontSize: 28,
    fontWeight: '900',
    lineHeight: 30,
  },
  cardLineDim: {
    color: Colors.textTertiary,
    fontSize: 22,
  },
  cardPropLabel: {
    color: Colors.textTertiary,
    fontSize: 9,
    fontWeight: '700',
    letterSpacing: 0.5,
    marginTop: 2,
    textAlign: 'right',
    textTransform: 'uppercase',
  },
  cardDivider: {
    height: 1,
    backgroundColor: 'rgba(255,255,255,0.06)',
    marginVertical: 10,
  },
  cardBottom: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  cardBmRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
  },
  cardBmDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
  },
  cardBmLabel: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.3,
  },
  cardBmExtra: {
    color: Colors.textTertiary,
    fontSize: 10,
    fontWeight: '600',
  },
  analyzeBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 10,
    backgroundColor: 'rgba(57,255,20,0.1)',
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.3)',
  },
  analyzeBtnDim: {
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderColor: 'rgba(255,255,255,0.08)',
  },
  analyzeBtnText: {
    color: Colors.primary,
    fontSize: 11,
    fontWeight: '900',
    letterSpacing: 0.8,
  },
  analyzeBtnTextDim: {
    color: Colors.textTertiary,
  },

  // Skeleton
  skeletonList: {
    padding: 16,
    gap: 10,
  },
  skeletonCard: {
    backgroundColor: '#111',
    borderRadius: 14,
    padding: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.06)',
  },
  skeletonRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  skeletonName: {
    width: '45%',
    height: 16,
    borderRadius: 6,
    backgroundColor: 'rgba(255,255,255,0.12)',
  },
  skeletonLine: {
    width: 44,
    height: 28,
    borderRadius: 6,
    backgroundColor: 'rgba(57,255,20,0.15)',
  },
  skeletonSub: {
    width: '35%',
    height: 10,
    borderRadius: 5,
    backgroundColor: 'rgba(255,255,255,0.07)',
    marginBottom: 10,
  },
  skeletonDivider: {
    height: 1,
    backgroundColor: 'rgba(255,255,255,0.06)',
    marginBottom: 10,
  },
  skeletonBtn: {
    width: 90,
    height: 28,
    borderRadius: 10,
    backgroundColor: 'rgba(57,255,20,0.1)',
    alignSelf: 'flex-end',
  },

  // Empty
  emptyWrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
    paddingBottom: 60,
  },
  emptyEmoji: {
    fontSize: 48,
    marginBottom: 16,
  },
  emptyTitle: {
    color: Colors.text,
    fontSize: 17,
    fontWeight: '800',
    textAlign: 'center',
    marginBottom: 10,
  },
  emptyBody: {
    color: Colors.textSecondary,
    fontSize: 13,
    textAlign: 'center',
    lineHeight: 20,
  },
  emptyRefresh: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 22,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 20,
    backgroundColor: 'rgba(57,255,20,0.1)',
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.3)',
  },
  emptyRefreshText: {
    color: Colors.primary,
    fontSize: 13,
    fontWeight: '700',
  },
});
