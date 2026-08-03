/**
 * Board Tab — Reverse Picks native prop discovery screen.
 *
 * SOCCER: Shows our own pre-computed projections for upcoming Argentina
 *         fixtures. Lines come from our Bayesian rolling averages, not any
 *         third-party bookmaker. Users can type their own line to see
 *         whether our model leans OVER or UNDER it.
 *
 * OTHER SPORTS: Falls back to the SportsGameOdds provider board
 *              (PrizePicks / Underdog markets) with ANALYZE routing.
 *
 * Tapping ANALYZE stores the pick in boardStore and navigates to
 * the Predict tab, which pre-fills the correct analysis form on focus.
 */
import React, {
  useState, useEffect, useCallback, useMemo, useRef,
} from 'react';
import {
  View, Text, StyleSheet, SectionList, TouchableOpacity,
  TextInput, ActivityIndicator, RefreshControl, Platform,
  ScrollView, Animated, KeyboardAvoidingView,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useFocusEffect } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import * as Haptics from 'expo-haptics';
import Colors from '@/constants/colors';
import {
  getMarketBoard, MarketBoardItem,
  getNativeSoccerBoard, NativeSoccerBoard, NativeBoardPlayer, NativeBoardFixture, NativeBoardProp,
} from '@/lib/api';
import { setBoardPendingMarket } from '@/lib/boardStore';

// ─── Sport tab config ─────────────────────────────────────────────────────────
const SPORT_TABS = [
  { id: 'all',          label: 'All',       emoji: '🎯' },
  { id: 'soccer',       label: 'Soccer',    emoji: '⚽' },
  { id: 'mlb',          label: 'Baseball',  emoji: '⚾' },
  { id: 'nba',          label: 'Basketball',emoji: '🏀' },
  { id: 'nfl',          label: 'Football',  emoji: '🏈' },
  { id: 'nhl',          label: 'Hockey',    emoji: '🏒' },
  { id: 'wta',          label: 'Tennis',    emoji: '🎾' },
] as const;

type SportId = (typeof SPORT_TABS)[number]['id'];

// ─── Native prop config ───────────────────────────────────────────────────────
const NATIVE_PROPS: { key: keyof NativeBoardPlayer['props']; label: string }[] = [
  { key: 'shots',         label: 'Shots'   },
  { key: 'pass_attempts', label: 'Passes'  },
  { key: 'saves',         label: 'GK Saves'},
];

// ─── Bookmaker config ─────────────────────────────────────────────────────────
const BOOKMAKER_CONFIG: Record<string, { label: string; color: string }> = {
  prizepicks:    { label: 'PrizePicks', color: '#00d48b' },
  underdog:      { label: 'Underdog',   color: '#F97316' },
  reversepicks:  { label: 'RP Model',   color: Colors.primary },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────
function formatTime(iso?: string): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
           ' · ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  } catch { return ''; }
}

function hitRateColor(pct: number | null): string {
  if (pct === null) return Colors.textTertiary;
  if (pct >= 65)   return Colors.primary;
  if (pct >= 45)   return Colors.warning;
  return Colors.error;
}

// ─── Skeleton card ─────────────────────────────────────────────────────────────
function SkeletonCard() {
  const opacity = useRef(new Animated.Value(0.3)).current;
  useEffect(() => {
    let cancelled = false;
    const pulse = () => {
      if (cancelled) return;
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.7, duration: 800, useNativeDriver: false }),
        Animated.timing(opacity, { toValue: 0.3, duration: 800, useNativeDriver: false }),
      ]).start(({ finished }) => { if (finished && !cancelled) pulse(); });
    };
    pulse();
    return () => { cancelled = true; opacity.stopAnimation(); };
  }, []);
  return (
    <Animated.View style={[styles.card, { opacity }]}>
      <View style={styles.skeletonRow}>
        <View style={styles.skeletonName} />
        <View style={styles.skeletonLine} />
      </View>
      <View style={styles.skeletonSub} />
      <View style={styles.cardDivider} />
      <View style={styles.skeletonBtn} />
    </Animated.View>
  );
}

// ─── Section header (shared by both boards) ───────────────────────────────────
function SectionHeader({ section }: { section: any }) {
  const time  = formatTime(section.eventStart);
  const sport = SPORT_TABS.find(s => s.id === (section.sport || 'soccer'));
  const bms   = section.providerCoverage || [];

  return (
    <View style={styles.sectionHeader}>
      <View style={styles.sectionHeaderLeft}>
        <View style={styles.sectionTeamRow}>
          <Text style={styles.sectionEmoji}>{sport?.emoji ?? '⚽'}</Text>
          <Text style={styles.sectionTeams} numberOfLines={1}>
            {section.homeTeam} vs {section.awayTeam}
          </Text>
        </View>
        <Text style={styles.sectionMeta} numberOfLines={1}>
          {section.leagueName || ''}
          {time ? `  ·  ${time}` : ''}
        </Text>
      </View>
      <View style={styles.sectionBadges}>
        {bms.map((bm: string) => {
          const cfg = BOOKMAKER_CONFIG[bm];
          return cfg ? (
            <View key={bm} style={[styles.bookBadge, { borderColor: cfg.color + '55' }]}>
              <View style={[styles.bookDot, { backgroundColor: cfg.color }]} />
              <Text style={[styles.bookLabel, { color: cfg.color }]}>{cfg.label}</Text>
            </View>
          ) : null;
        })}
      </View>
    </View>
  );
}

// ─── NATIVE SOCCER CARD ───────────────────────────────────────────────────────
function NativePlayerCard({
  player,
  fixture,
  onAnalyze,
}: {
  player: NativeBoardPlayer;
  fixture: NativeBoardFixture;
  onAnalyze: (player: NativeBoardPlayer, fixture: NativeBoardFixture, propKey: string, userLine: number) => void;
}) {
  // Determine available props for this player
  const availableProps = NATIVE_PROPS.filter(p => player.props[p.key] != null);
  const [selectedProp, setSelectedProp] = useState<keyof NativeBoardPlayer['props']>(
    availableProps[0]?.key ?? 'shots'
  );
  const [userLineStr, setUserLineStr] = useState('');

  const propData: NativeBoardProp | undefined = player.props[selectedProp];
  const proj = propData?.projected ?? null;
  const userLine = parseFloat(userLineStr);
  const hasUserLine = !isNaN(userLine) && userLineStr.trim() !== '';

  // Direction of our model relative to the user's line
  let direction: 'OVER' | 'UNDER' | null = null;
  let diff = 0;
  if (hasUserLine && proj !== null) {
    diff = proj - userLine;
    direction = diff > 0.05 ? 'OVER' : diff < -0.05 ? 'UNDER' : null;
  }

  const posLabel = player.position === 'G' ? 'GK'
    : player.position === 'D' ? 'DEF'
    : player.position === 'M' ? 'MID'
    : player.position === 'F' ? 'FWD' : player.position;

  return (
    <View style={styles.card}>
      {/* ── Top: player info + prop switcher ─────────────────────────── */}
      <View style={styles.nativeCardTop}>
        <View style={styles.nativePlayerInfo}>
          <Text style={styles.nativePlayerName} numberOfLines={1}>
            {player.playerName}
          </Text>
          <View style={styles.nativePlayerMeta}>
            <Text style={styles.nativePosBadge}>{posLabel}</Text>
            <Text style={styles.nativeTeamName} numberOfLines={1}>
              {player.teamName}
            </Text>
            <View style={[styles.venueChip, player.isHome ? styles.venueHome : styles.venueAway]}>
              <Text style={styles.venueText}>{player.isHome ? 'HOME' : 'AWAY'}</Text>
            </View>
          </View>
        </View>
        {/* Prop switcher */}
        <View style={styles.propSwitcher}>
          {availableProps.map(p => (
            <TouchableOpacity
              key={p.key}
              style={[styles.propTab, selectedProp === p.key && styles.propTabActive]}
              onPress={() => { setSelectedProp(p.key); Haptics.selectionAsync(); }}
              activeOpacity={0.75}
            >
              <Text style={[styles.propTabText, selectedProp === p.key && styles.propTabTextActive]}>
                {p.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      {/* ── Projection ───────────────────────────────────────────────── */}
      <View style={styles.nativeProjRow}>
        <View style={styles.nativeProjBox}>
          {proj !== null ? (
            <>
              <Text style={styles.nativeProjNum}>{proj.toFixed(1)}</Text>
              <Text style={styles.nativeProjLabel}>
                {NATIVE_PROPS.find(p => p.key === selectedProp)?.label.toUpperCase()} · RP PROJECTION
              </Text>
            </>
          ) : (
            <Text style={styles.nativeNoData}>No data</Text>
          )}
        </View>

        {/* Hit rates */}
        <View style={styles.hitRateCol}>
          {propData?.l5HitPct !== null && propData?.l5HitPct !== undefined ? (
            <View style={styles.hitRatePill}>
              <Text style={styles.hitRateLabel}>L5</Text>
              <Text style={[styles.hitRateVal, { color: hitRateColor(propData.l5HitPct) }]}>
                {propData.l5HitPct}%
              </Text>
            </View>
          ) : null}
          {propData?.l10HitPct !== null && propData?.l10HitPct !== undefined ? (
            <View style={styles.hitRatePill}>
              <Text style={styles.hitRateLabel}>L10</Text>
              <Text style={[styles.hitRateVal, { color: hitRateColor(propData.l10HitPct) }]}>
                {propData.l10HitPct}%
              </Text>
            </View>
          ) : null}
          {propData?.settledPicks ? (
            <Text style={styles.hitRateSub}>{propData.settledPicks} picks</Text>
          ) : (
            <Text style={styles.hitRateSub}>No history yet</Text>
          )}
        </View>
      </View>

      {/* ── Your line input ───────────────────────────────────────────── */}
      <View style={styles.cardDivider} />
      <View style={styles.yourLineRow}>
        <Text style={styles.yourLineLabel}>Your line</Text>
        <TextInput
          style={[styles.yourLineInput, Platform.OS === 'web' && { outlineWidth: 0 } as any]}
          value={userLineStr}
          onChangeText={setUserLineStr}
          placeholder={proj != null ? proj.toFixed(1) : '—'}
          placeholderTextColor={Colors.textTertiary}
          keyboardType="decimal-pad"
          returnKeyType="done"
          maxLength={6}
        />
        {hasUserLine && direction && proj !== null && (
          <View style={[
            styles.directionChip,
            direction === 'OVER' ? styles.directionOver : styles.directionUnder,
          ]}>
            <Text style={styles.directionText}>
              {direction === 'OVER' ? '↑ OVER' : '↓ UNDER'} ({diff > 0 ? '+' : ''}{diff.toFixed(1)})
            </Text>
          </View>
        )}
        {hasUserLine && direction === null && proj !== null && (
          <View style={styles.directionNeutral}>
            <Text style={styles.directionNeutralText}>≈ AT THE LINE</Text>
          </View>
        )}
      </View>

      {/* ── Bottom: analyze ───────────────────────────────────────────── */}
      <View style={styles.cardBottom}>
        <View style={styles.cardBmRow}>
          <View style={[styles.cardBmDot, { backgroundColor: Colors.primary }]} />
          <Text style={[styles.cardBmLabel, { color: Colors.primary }]}>RP Model</Text>
          {propData?.samples ? (
            <Text style={styles.cardBmExtra}> · {propData.samples} games</Text>
          ) : null}
        </View>
        <TouchableOpacity
          style={styles.analyzeBtn}
          onPress={() => {
            const line = hasUserLine ? userLine : (proj ?? 0);
            onAnalyze(player, fixture, selectedProp as string, line);
          }}
          activeOpacity={0.78}
        >
          <Text style={styles.analyzeBtnText}>ANALYZE</Text>
          <Ionicons name="arrow-forward" size={12} color={Colors.primary} />
        </TouchableOpacity>
      </View>
    </View>
  );
}

// ─── SGO market card (for non-soccer sports) ──────────────────────────────────
function MarketCard({
  market,
  onAnalyze,
}: {
  market: MarketBoardItem;
  onAnalyze: (m: MarketBoardItem) => void;
}) {
  const supported   = market.analysisSupported;
  const lineDisplay = market.marketLine != null
    ? String(market.marketLine)
    : (market.marketSelection ?? '—');
  const primaryBm = (market.providerCoverage || [])[0] || 'prizepicks';
  const bmCfg     = BOOKMAKER_CONFIG[primaryBm] ?? { label: primaryBm, color: Colors.primary };

  return (
    <View style={styles.card}>
      <View style={styles.cardTop}>
        <View style={styles.cardPlayerCol}>
          <Text style={styles.cardPlayer} numberOfLines={1}>{market.playerName || 'Player'}</Text>
          <Text style={styles.cardTeam} numberOfLines={1}>{market.homeTeam || market.awayTeam || ''}</Text>
        </View>
        <View style={styles.cardLineBadge}>
          <Text style={[styles.cardLine, !supported && styles.cardLineDim]}>{lineDisplay}</Text>
          <Text style={styles.cardPropLabel} numberOfLines={1}>
            {market.propLabel || market.propType || ''}
          </Text>
        </View>
      </View>
      <View style={styles.cardDivider} />
      <View style={styles.cardBottom}>
        <View style={styles.cardBmRow}>
          <View style={[styles.cardBmDot, { backgroundColor: bmCfg.color }]} />
          <Text style={[styles.cardBmLabel, { color: bmCfg.color }]}>{bmCfg.label}</Text>
          {(market.providerCoverage || []).length > 1 && (
            <Text style={styles.cardBmExtra}> +{(market.providerCoverage || []).length - 1}</Text>
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
          <Ionicons name="arrow-forward" size={12}
            color={supported ? Colors.primary : Colors.textTertiary} />
        </TouchableOpacity>
      </View>
    </View>
  );
}

// ─── Section/item wrappers ────────────────────────────────────────────────────
// We use a discriminated union so SectionList can handle both card types.
type BoardSection =
  | { kind: 'native';  key: string; homeTeam: string; awayTeam: string;
      eventStart?: string; leagueName?: string; sport?: string;
      providerCoverage: string[];
      data: Array<{ kind: 'native'; player: NativeBoardPlayer; fixture: NativeBoardFixture }> }
  | { kind: 'market';  key: string; homeTeam: string; awayTeam: string;
      eventStart?: string; leagueName?: string; sport?: string;
      providerCoverage: string[];
      data: Array<{ kind: 'market'; market: MarketBoardItem }> };

// ─── Main screen ──────────────────────────────────────────────────────────────
export default function BoardScreen() {
  const insets = useSafeAreaInsets();

  // SGO board state (non-soccer sports)
  const [markets,         setMarkets]         = useState<MarketBoardItem[]>([]);
  const [marketsLoading,  setMarketsLoading]  = useState(false);
  const [marketsError,    setMarketsError]    = useState<string | null>(null);

  // Native soccer board state
  const [nativeBoard,        setNativeBoard]        = useState<NativeSoccerBoard | null>(null);
  const [nativeBoardLoading, setNativeBoardLoading] = useState(false);
  const [nativeBoardError,   setNativeBoardError]   = useState<string | null>(null);

  const [lastUpdated,  setLastUpdated]  = useState<Date | null>(null);
  const [refreshing,   setRefreshing]   = useState(false);
  const [sportFilter,  setSportFilter]  = useState<SportId>('soccer');
  const [propFilter,   setPropFilter]   = useState('all');
  const [searchQuery,  setSearchQuery]  = useState('');

  // ── Loaders ─────────────────────────────────────────────────────────────────
  const loadNative = useCallback(async (silent = false) => {
    if (!silent) setNativeBoardLoading(true);
    setNativeBoardError(null);
    try {
      const data = await getNativeSoccerBoard();
      setNativeBoard(data);
      setLastUpdated(new Date());
    } catch (e) {
      setNativeBoardError(e instanceof Error ? e.message : 'Could not load soccer board');
    } finally {
      setNativeBoardLoading(false);
    }
  }, []);

  const loadMarkets = useCallback(async (silent = false) => {
    if (!silent) setMarketsLoading(true);
    setMarketsError(null);
    try {
      const data = await getMarketBoard({ hours: 72, limit: 100 });
      setMarkets(data?.markets || []);
      if (!nativeBoard) setLastUpdated(new Date());
    } catch (e) {
      setMarketsError(e instanceof Error ? e.message : 'Could not load board');
    } finally {
      setMarketsLoading(false);
      setRefreshing(false);
    }
  }, [nativeBoard]);

  const loadAll = useCallback(async (silent = false) => {
    await Promise.all([loadNative(silent), loadMarkets(silent)]);
    setRefreshing(false);
  }, [loadNative, loadMarkets]);

  useEffect(() => { loadAll(); }, []);

  // Silent refresh on focus when stale
  const lastFocusLoad = useRef<number>(0);
  useFocusEffect(useCallback(() => {
    const now = Date.now();
    if (now - lastFocusLoad.current > 90_000) {
      lastFocusLoad.current = now;
      loadAll(true);
    }
  }, [loadAll]));

  // ── Derived sections ─────────────────────────────────────────────────────────
  const isSoccerView = sportFilter === 'soccer' || sportFilter === 'all';

  // Native soccer sections
  const nativeSections = useMemo((): BoardSection[] => {
    if (!nativeBoard?.fixtures) return [];
    return nativeBoard.fixtures
      .map(fixture => {
        const players = fixture.players.filter(p => {
          if (!searchQuery.trim()) return true;
          const q = searchQuery.toLowerCase();
          return p.playerName.toLowerCase().includes(q) ||
                 p.teamName.toLowerCase().includes(q);
        });
        if (!players.length) return null;
        return {
          kind: 'native' as const,
          key: String(fixture.fixtureId),
          homeTeam: fixture.homeTeam,
          awayTeam: fixture.awayTeam,
          eventStart: fixture.startTime,
          leagueName: fixture.leagueName,
          sport: 'soccer',
          providerCoverage: ['reversepicks'],
          data: players.map(player => ({ kind: 'native' as const, player, fixture })),
        };
      })
      .filter(Boolean) as BoardSection[];
  }, [nativeBoard, searchQuery]);

  // SGO sections (non-soccer sports)
  const sgoSections = useMemo((): BoardSection[] => {
    let filtered = markets.filter(m => {
      if (sportFilter === 'all') return m.sport !== 'soccer'; // soccer handled by native
      if (sportFilter === 'soccer') return false;             // soccer handled by native
      return m.sport === sportFilter;
    });
    if (propFilter !== 'all') filtered = filtered.filter(m =>
      (m.propLabel || m.propType || '') === propFilter
    );
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      filtered = filtered.filter(m =>
        (m.playerName || '').toLowerCase().includes(q) ||
        (m.homeTeam   || '').toLowerCase().includes(q) ||
        (m.awayTeam   || '').toLowerCase().includes(q)
      );
    }
    const map = new Map<string, { meta: any; items: MarketBoardItem[] }>();
    for (const m of filtered) {
      const key = m.eventId || `${m.homeTeam}|${m.awayTeam}`;
      if (!map.has(key)) map.set(key, { meta: m, items: [] });
      map.get(key)!.items.push(m);
    }
    return Array.from(map.values()).map(({ meta, items }) => ({
      kind: 'market' as const,
      key: meta.eventId || `${meta.homeTeam}|${meta.awayTeam}`,
      homeTeam: meta.homeTeam || '',
      awayTeam: meta.awayTeam || '',
      eventStart: meta.eventStart,
      leagueName: meta.leagueName,
      sport: meta.sport,
      providerCoverage: meta.providerCoverage || [],
      data: items.map(market => ({ kind: 'market' as const, market })),
    }));
  }, [markets, sportFilter, propFilter, searchQuery]);

  // Which sections to show
  const showNative = sportFilter === 'soccer' || sportFilter === 'all';
  const sections: BoardSection[] = useMemo(() => [
    ...(showNative ? nativeSections : []),
    ...sgoSections,
  ], [showNative, nativeSections, sgoSections]);

  // Dynamic prop options for SGO filter
  const propOptions = useMemo(() => {
    const labels = new Set<string>();
    for (const m of markets) {
      if (sportFilter !== 'all' && m.sport !== sportFilter) continue;
      const lbl = m.propLabel || m.propType;
      if (lbl) labels.add(lbl);
    }
    return ['all', ...Array.from(labels).sort()];
  }, [markets, sportFilter]);

  useEffect(() => { setPropFilter('all'); }, [sportFilter]);

  const sportsWithMarkets = useMemo(() => {
    const s = new Set<string>(markets.map(m => m.sport || ''));
    if (nativeBoard?.fixtures?.length) s.add('soccer');
    return s;
  }, [markets, nativeBoard]);

  // ── Handlers ─────────────────────────────────────────────────────────────────
  const handleAnalyzeNative = useCallback((
    player: NativeBoardPlayer,
    fixture: NativeBoardFixture,
    propKey: string,
    userLine: number,
  ) => {
    Haptics.selectionAsync();
    const propLabel = NATIVE_PROPS.find(p => p.key === propKey)?.label ?? propKey;
    setBoardPendingMarket({
      playerName:        player.playerName,
      sport:             'soccer',
      propType:          propKey,
      propLabel,
      marketLine:        userLine,
      leagueId:          fixture.leagueId,
      leagueName:        fixture.leagueName,
      homeTeam:          fixture.homeTeam,
      awayTeam:          fixture.awayTeam,
      eventStart:        fixture.startTime,
      analysisSupported: true,
      providerCoverage:  ['reversepicks'],
    });
    router.push('/(tabs)/scan');
  }, []);

  const handleAnalyzeSGO = useCallback((market: MarketBoardItem) => {
    Haptics.selectionAsync();
    setBoardPendingMarket(market);
    router.push('/(tabs)/scan');
  }, []);

  // ── Render ───────────────────────────────────────────────────────────────────
  const topPad = Platform.OS === 'web' ? 67 : insets.top;
  const isLoading = (nativeBoardLoading && !nativeBoard) ||
                    (marketsLoading && !markets.length);

  const renderItem = useCallback(({ item }: { item: BoardSection['data'][number] }) => {
    if (item.kind === 'native') {
      return (
        <NativePlayerCard
          player={item.player}
          fixture={item.fixture}
          onAnalyze={handleAnalyzeNative}
        />
      );
    }
    return <MarketCard market={item.market} onAnalyze={handleAnalyzeSGO} />;
  }, [handleAnalyzeNative, handleAnalyzeSGO]);

  const renderSectionHeader = useCallback(
    ({ section }: { section: BoardSection }) => <SectionHeader section={section} />,
    []
  );

  const keyExtractor = useCallback((item: any, i: number) => {
    if (item.kind === 'native') return `native-${item.player.playerId}-${i}`;
    return `sgo-${item.market.eventId || 'ev'}-${item.market.playerName}-${i}`;
  }, []);

  if (isLoading) {
    return (
      <View style={[styles.root, { paddingTop: topPad }]}>
        <BoardHeader lastUpdated={lastUpdated} onRefresh={loadAll} loading={true} />
        <View style={styles.skeletonList}>
          {[...Array(4)].map((_, i) => <SkeletonCard key={i} />)}
        </View>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={[styles.root, { paddingTop: topPad }]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={topPad}
    >
      <BoardHeader lastUpdated={lastUpdated} onRefresh={loadAll} loading={nativeBoardLoading || marketsLoading} />

      {/* Search */}
      <View style={styles.searchWrap}>
        <Ionicons name="search-outline" size={16} color={Colors.textTertiary} />
        <TextInput
          style={[styles.searchInput, Platform.OS === 'web' && { outlineWidth: 0 } as any]}
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
          <TouchableOpacity onPress={() => setSearchQuery('')}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Ionicons name="close-circle" size={16} color={Colors.textTertiary} />
          </TouchableOpacity>
        )}
      </View>

      {/* Sport tabs */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.sportTabsRow} style={styles.sportTabsScroll}>
        {SPORT_TABS.map(tab => {
          const active   = sportFilter === tab.id;
          const hasData  = tab.id === 'all' || sportsWithMarkets.has(tab.id);
          return (
            <TouchableOpacity key={tab.id}
              style={[styles.sportTab, active && styles.sportTabActive, !hasData && styles.sportTabEmpty]}
              onPress={() => { setSportFilter(tab.id); Haptics.selectionAsync(); }}
              activeOpacity={0.75}>
              <Text style={styles.sportTabEmoji}>{tab.emoji}</Text>
              <Text style={[styles.sportTabLabel, active && styles.sportTabLabelActive,
                !hasData && styles.sportTabLabelEmpty]}>
                {tab.label}
              </Text>
              {hasData && tab.id !== 'all' && (
                <View style={[styles.sportTabDot, active && styles.sportTabDotActive]} />
              )}
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      {/* Prop filter (SGO non-soccer only) */}
      {sportFilter !== 'soccer' && propOptions.length > 1 && (
        <ScrollView horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.propFilterRow} style={styles.propFilterScroll}>
          {propOptions.map(opt => (
            <TouchableOpacity key={opt}
              style={[styles.propChip, propFilter === opt && styles.propChipActive]}
              onPress={() => { setPropFilter(opt); Haptics.selectionAsync(); }}
              activeOpacity={0.75}>
              <Text style={[styles.propChipText, propFilter === opt && styles.propChipTextActive]}>
                {opt === 'all' ? 'All Props' : opt}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      )}

      {/* Soccer board header hint */}
      {(sportFilter === 'soccer' || sportFilter === 'all') && nativeBoard && (
        <View style={styles.nativeBoardHint}>
          <Ionicons name="analytics-outline" size={13} color={Colors.primary} />
          <Text style={styles.nativeBoardHintText}>
            Lines are our Bayesian projections · Set your own line to compare
          </Text>
        </View>
      )}

      {/* Errors */}
      {nativeBoardError && (
        <View style={styles.errorBanner}>
          <Ionicons name="cloud-offline-outline" size={14} color={Colors.warning} />
          <Text style={styles.errorText}>{nativeBoardError}</Text>
          <TouchableOpacity onPress={() => loadNative()} style={styles.retryBtn}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      )}

      {/* Main list */}
      {sections.length === 0 ? (
        <EmptyState sportFilter={sportFilter} searchQuery={searchQuery}
          hasNativeData={!!nativeBoard?.fixtures?.length}
          hasMarketData={markets.length > 0}
          onRefresh={() => loadAll()} />
      ) : (
        <SectionList
          sections={sections as any}
          keyExtractor={keyExtractor}
          renderItem={renderItem}
          renderSectionHeader={renderSectionHeader}
          stickySectionHeadersEnabled={false}
          contentContainerStyle={styles.listContent}
          showsVerticalScrollIndicator={false}
          keyboardDismissMode="on-drag"
          keyboardShouldPersistTaps="handled"
          refreshControl={
            <RefreshControl refreshing={refreshing}
              onRefresh={() => { setRefreshing(true); loadAll(true); }}
              tintColor={Colors.primary} colors={[Colors.primary]} />
          }
          ItemSeparatorComponent={() => <View style={styles.itemSep} />}
          SectionSeparatorComponent={() => <View style={styles.sectionSep} />}
        />
      )}
    </KeyboardAvoidingView>
  );
}

// ─── Board header ─────────────────────────────────────────────────────────────
function BoardHeader({ lastUpdated, onRefresh, loading }: {
  lastUpdated: Date | null; onRefresh: () => void; loading: boolean;
}) {
  const updatedStr = lastUpdated
    ? lastUpdated.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
    : null;
  return (
    <View style={styles.header}>
      <View>
        <Text style={styles.headerTitle}>BOARD</Text>
        <Text style={styles.headerSub}>
          Reverse Picks Soccer{updatedStr ? `  ·  ${updatedStr}` : ''}
        </Text>
      </View>
      <TouchableOpacity onPress={onRefresh} style={styles.refreshBtn}
        disabled={loading} activeOpacity={0.7}>
        {loading
          ? <ActivityIndicator size="small" color={Colors.primary} />
          : <Ionicons name="refresh-outline" size={20} color={Colors.primary} />}
      </TouchableOpacity>
    </View>
  );
}

// ─── Empty state ──────────────────────────────────────────────────────────────
function EmptyState({ sportFilter, searchQuery, hasNativeData, hasMarketData, onRefresh }: {
  sportFilter: SportId; searchQuery: string;
  hasNativeData: boolean; hasMarketData: boolean;
  onRefresh: () => void;
}) {
  const sport = SPORT_TABS.find(s => s.id === sportFilter);
  let title = 'No fixtures in the next 72 hours';
  let body  = 'The board will populate once upcoming matches are scheduled. Pull down to refresh.';

  if (searchQuery) {
    title = `No results for "${searchQuery}"`;
    body  = 'Try a different name or clear the search.';
  } else if (sportFilter !== 'soccer' && sportFilter !== 'all') {
    title = `No ${sport?.label || 'sport'} markets right now`;
    body  = 'PrizePicks and Underdog haven\'t posted lines yet. Check back closer to game time.';
  }

  return (
    <View style={styles.emptyWrap}>
      <Text style={styles.emptyEmoji}>{searchQuery ? '🔍' : (sport?.emoji ?? '📋')}</Text>
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
  root: { flex: 1, backgroundColor: Colors.background },

  // Header
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 20, paddingTop: 12, paddingBottom: 10,
    borderBottomWidth: 1, borderBottomColor: 'rgba(57,255,20,0.1)',
  },
  headerTitle: { color: Colors.text, fontSize: 22, fontWeight: '900', letterSpacing: 2 },
  headerSub:   { color: Colors.textTertiary, fontSize: 11, marginTop: 2, letterSpacing: 0.3 },
  refreshBtn:  {
    width: 38, height: 38, borderRadius: 19,
    backgroundColor: 'rgba(57,255,20,0.07)',
    alignItems: 'center', justifyContent: 'center',
  },

  // Search
  searchWrap: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    marginHorizontal: 16, marginTop: 12, marginBottom: 4,
    backgroundColor: 'rgba(255,255,255,0.04)', borderRadius: 12,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
    paddingHorizontal: 14, height: 42,
  },
  searchInput: { flex: 1, color: Colors.text, fontSize: 14, fontWeight: '500' },

  // Sport tabs
  sportTabsScroll: { marginTop: 10 },
  sportTabsRow:    { paddingHorizontal: 16, gap: 8, paddingBottom: 2 },
  sportTab: {
    alignItems: 'center', gap: 3, paddingHorizontal: 14, paddingVertical: 8,
    borderRadius: 24, backgroundColor: 'rgba(255,255,255,0.04)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.07)', minWidth: 68, position: 'relative',
  },
  sportTabActive:       { backgroundColor: 'rgba(57,255,20,0.12)', borderColor: 'rgba(57,255,20,0.4)' },
  sportTabEmpty:        { opacity: 0.4 },
  sportTabEmoji:        { fontSize: 18 },
  sportTabLabel:        { color: Colors.textSecondary, fontSize: 10, fontWeight: '700', letterSpacing: 0.3 },
  sportTabLabelActive:  { color: Colors.primary },
  sportTabLabelEmpty:   { color: Colors.textTertiary },
  sportTabDot: {
    position: 'absolute', top: 6, right: 8,
    width: 5, height: 5, borderRadius: 3, backgroundColor: Colors.textTertiary,
  },
  sportTabDotActive: { backgroundColor: Colors.primary },

  // Prop filter
  propFilterScroll: { marginTop: 10 },
  propFilterRow:    { paddingHorizontal: 16, gap: 7, paddingBottom: 2 },
  propChip: {
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.07)',
  },
  propChipActive:     { backgroundColor: 'rgba(57,255,20,0.12)', borderColor: 'rgba(57,255,20,0.4)' },
  propChipText:       { color: Colors.textSecondary, fontSize: 11, fontWeight: '700' },
  propChipTextActive: { color: Colors.primary },

  // Native board hint
  nativeBoardHint: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    marginHorizontal: 16, marginTop: 10,
    paddingHorizontal: 12, paddingVertical: 7,
    backgroundColor: 'rgba(57,255,20,0.06)', borderRadius: 8,
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.15)',
  },
  nativeBoardHintText: { color: Colors.textSecondary, fontSize: 11, lineHeight: 16, flex: 1 },

  // Errors
  errorBanner: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    marginHorizontal: 16, marginTop: 10, padding: 12,
    backgroundColor: 'rgba(255,149,0,0.1)', borderRadius: 10,
    borderWidth: 1, borderColor: 'rgba(255,149,0,0.25)',
  },
  errorText:  { color: Colors.warning, fontSize: 12, flex: 1, lineHeight: 16 },
  retryBtn:   { paddingHorizontal: 10, paddingVertical: 5, backgroundColor: 'rgba(255,149,0,0.15)', borderRadius: 8 },
  retryText:  { color: Colors.warning, fontSize: 11, fontWeight: '700' },

  // Section list
  listContent: { paddingHorizontal: 16, paddingTop: 14, paddingBottom: 60 },
  itemSep:     { height: 8 },
  sectionSep:  { height: 4 },

  // Section header
  sectionHeader: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    paddingTop: 20, paddingBottom: 10,
    borderBottomWidth: 1, borderBottomColor: 'rgba(57,255,20,0.1)', marginBottom: 2,
  },
  sectionHeaderLeft: { flex: 1, marginRight: 10 },
  sectionTeamRow:    { flexDirection: 'row', alignItems: 'center', gap: 6 },
  sectionEmoji:      { fontSize: 16 },
  sectionTeams:      { color: Colors.text, fontSize: 15, fontWeight: '800', flex: 1 },
  sectionMeta:       { color: Colors.textTertiary, fontSize: 11, marginTop: 3, paddingLeft: 22 },
  sectionBadges:     { gap: 5, alignItems: 'flex-end' },
  bookBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 7, paddingVertical: 3, borderRadius: 8,
    borderWidth: 1, backgroundColor: 'rgba(255,255,255,0.03)',
  },
  bookDot:   { width: 5, height: 5, borderRadius: 3 },
  bookLabel: { fontSize: 9, fontWeight: '800', letterSpacing: 0.4 },

  // Base card (shared)
  card: {
    backgroundColor: '#111111', borderRadius: 14,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)', padding: 14,
  },
  cardDivider:  { height: 1, backgroundColor: 'rgba(255,255,255,0.06)', marginVertical: 10 },
  cardBottom:   { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  cardBmRow:    { flexDirection: 'row', alignItems: 'center', gap: 5 },
  cardBmDot:    { width: 7, height: 7, borderRadius: 4 },
  cardBmLabel:  { fontSize: 11, fontWeight: '700', letterSpacing: 0.3 },
  cardBmExtra:  { color: Colors.textTertiary, fontSize: 10, fontWeight: '600' },
  analyzeBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 10,
    backgroundColor: 'rgba(57,255,20,0.1)', borderWidth: 1, borderColor: 'rgba(57,255,20,0.3)',
  },
  analyzeBtnDim:      { backgroundColor: 'rgba(255,255,255,0.04)', borderColor: 'rgba(255,255,255,0.08)' },
  analyzeBtnText:     { color: Colors.primary, fontSize: 11, fontWeight: '900', letterSpacing: 0.8 },
  analyzeBtnTextDim:  { color: Colors.textTertiary },

  // SGO market card
  cardTop:       { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  cardPlayerCol: { flex: 1, marginRight: 12 },
  cardPlayer:    { color: Colors.text, fontSize: 16, fontWeight: '800' },
  cardTeam:      { color: Colors.textSecondary, fontSize: 11, marginTop: 3 },
  cardLineBadge: { alignItems: 'flex-end', minWidth: 56 },
  cardLine:      { color: Colors.primary, fontSize: 28, fontWeight: '900', lineHeight: 30 },
  cardLineDim:   { color: Colors.textTertiary, fontSize: 22 },
  cardPropLabel: {
    color: Colors.textTertiary, fontSize: 9, fontWeight: '700',
    letterSpacing: 0.5, marginTop: 2, textAlign: 'right', textTransform: 'uppercase',
  },

  // Native card
  nativeCardTop:   { gap: 10 },
  nativePlayerInfo: { flex: 1 },
  nativePlayerName: { color: Colors.text, fontSize: 16, fontWeight: '800', letterSpacing: 0.2 },
  nativePlayerMeta: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 4 },
  nativePosBadge: {
    color: Colors.textTertiary, fontSize: 10, fontWeight: '800',
    backgroundColor: 'rgba(255,255,255,0.06)', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 5,
  },
  nativeTeamName: { color: Colors.textSecondary, fontSize: 11, fontWeight: '500', flex: 1 },
  venueChip:      { paddingHorizontal: 5, paddingVertical: 2, borderRadius: 4 },
  venueHome:      { backgroundColor: 'rgba(57,255,20,0.12)' },
  venueAway:      { backgroundColor: 'rgba(255,255,255,0.06)' },
  venueText:      { fontSize: 9, fontWeight: '800', color: Colors.primary, letterSpacing: 0.5 },

  // Prop switcher
  propSwitcher: { flexDirection: 'row', gap: 6, flexWrap: 'wrap', marginTop: 8 },
  propTab: {
    paddingHorizontal: 10, paddingVertical: 5, borderRadius: 8,
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
  },
  propTabActive:     { backgroundColor: 'rgba(57,255,20,0.12)', borderColor: 'rgba(57,255,20,0.4)' },
  propTabText:       { color: Colors.textSecondary, fontSize: 11, fontWeight: '700' },
  propTabTextActive: { color: Colors.primary },

  // Projection + hit rates
  nativeProjRow: { flexDirection: 'row', alignItems: 'center', marginVertical: 10, gap: 12 },
  nativeProjBox: { flex: 1, alignItems: 'flex-start' },
  nativeProjNum: { color: Colors.primary, fontSize: 44, fontWeight: '900', lineHeight: 48 },
  nativeProjLabel: { color: Colors.textTertiary, fontSize: 9, fontWeight: '700', letterSpacing: 0.5, marginTop: 2 },
  nativeNoData:    { color: Colors.textTertiary, fontSize: 14, fontWeight: '600' },
  hitRateCol:      { alignItems: 'flex-end', gap: 5 },
  hitRatePill:     { flexDirection: 'row', alignItems: 'center', gap: 6 },
  hitRateLabel:    { color: Colors.textTertiary, fontSize: 10, fontWeight: '700', width: 22 },
  hitRateVal:      { fontSize: 15, fontWeight: '900' },
  hitRateSub:      { color: Colors.textTertiary, fontSize: 9, marginTop: 2 },

  // Your line
  yourLineRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 10 },
  yourLineLabel: { color: Colors.textTertiary, fontSize: 12, fontWeight: '700', width: 64 },
  yourLineInput: {
    flex: 1, maxWidth: 80, color: Colors.text, fontSize: 16, fontWeight: '800',
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.25)', borderRadius: 8,
    paddingHorizontal: 10, paddingVertical: 7, textAlign: 'center',
    backgroundColor: 'rgba(57,255,20,0.05)',
  },
  directionChip: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: 8, flex: 1 },
  directionOver: { backgroundColor: 'rgba(57,255,20,0.12)', borderWidth: 1, borderColor: 'rgba(57,255,20,0.3)' },
  directionUnder: { backgroundColor: 'rgba(10,132,255,0.12)', borderWidth: 1, borderColor: 'rgba(10,132,255,0.3)' },
  directionText: { color: Colors.primary, fontSize: 11, fontWeight: '800', letterSpacing: 0.4 },
  directionNeutral: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: 8,
    backgroundColor: 'rgba(255,255,255,0.05)', flex: 1 },
  directionNeutralText: { color: Colors.textTertiary, fontSize: 11, fontWeight: '700' },

  // Skeleton
  skeletonList: { padding: 16, gap: 10 },
  skeletonRow:  { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  skeletonName: { width: '45%', height: 16, borderRadius: 6, backgroundColor: 'rgba(255,255,255,0.12)' },
  skeletonLine: { width: 44, height: 28, borderRadius: 6, backgroundColor: 'rgba(57,255,20,0.15)' },
  skeletonSub:  { width: '35%', height: 10, borderRadius: 5, backgroundColor: 'rgba(255,255,255,0.07)', marginBottom: 10 },
  skeletonBtn:  { width: 90, height: 28, borderRadius: 10, backgroundColor: 'rgba(57,255,20,0.1)', alignSelf: 'flex-end' },

  // Empty
  emptyWrap:        { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32, paddingBottom: 60 },
  emptyEmoji:       { fontSize: 48, marginBottom: 16 },
  emptyTitle:       { color: Colors.text, fontSize: 17, fontWeight: '800', textAlign: 'center', marginBottom: 10 },
  emptyBody:        { color: Colors.textSecondary, fontSize: 13, textAlign: 'center', lineHeight: 20 },
  emptyRefresh:     {
    flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 22,
    paddingHorizontal: 20, paddingVertical: 10, borderRadius: 20,
    backgroundColor: 'rgba(57,255,20,0.1)', borderWidth: 1, borderColor: 'rgba(57,255,20,0.3)',
  },
  emptyRefreshText: { color: Colors.primary, fontSize: 13, fontWeight: '700' },
});
