import React, { useMemo, useState } from 'react';
import {
  View, Text, StyleSheet, Modal, ScrollView, TouchableOpacity, Dimensions, Platform, Alert,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Svg, { Line, Path, Rect, G, Circle, Defs, LinearGradient, Stop } from 'react-native-svg';
import Colors from '@/constants/colors';
import { getOwnerAnalytics, getStorageHealth, getQuotaStatus, getBzzoiroStatus, resetQuotaBreaker, triggerStorageCleanup, Pick, AnalyticsData, BzzoiroStatus } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { useQuery, useQueryClient } from '@tanstack/react-query';

const LEAGUE_LABELS: Record<number, string> = {
  39: 'Premier League', 40: 'Championship', 140: 'La Liga', 141: 'La Liga 2',
  135: 'Serie A', 136: 'Serie B', 78: 'Bundesliga', 79: 'Bundesliga 2',
  61: 'Ligue 1', 62: 'Ligue 2', 71: 'Brasileirão', 128: 'Liga Profesional',
  253: 'MLS', 262: 'Liga MX', 254: 'NWSL', 2: 'Champions League', 3: 'Europa League',
  848: 'NWSL', 1: 'World Cup', 5: 'Nations League', 307: 'Saudi Pro',
  88: 'Eredivisie', 94: 'Primeira Liga', 144: 'Belgian Pro', 203: 'Süper Lig',
};

function getLeagueLabel(id?: number | null) {
  if (!id) return 'Unknown';
  return LEAGUE_LABELS[id] || `League ${id}`;
}

function getSport(p: Pick) {
  if (p.sport === 'mlb' || /pitcher|hits|strikeouts|innings|runs|rbi|walks/i.test(p.propType || '')) return 'MLB';
  if (p.sport === 'cs2' || /map|kills|deaths|adr|headshots|mvps|rating/i.test(p.propType || '')) return 'CS2';
  return 'Soccer';
}

function getRecDir(p: Pick): 'OVER' | 'UNDER' | null {
  if (p.recommendation === 'OVER' || p.recommendation === 'UNDER') return p.recommendation;
  const passLean = String(p.passLeaning || '').toUpperCase();
  if (passLean === 'OVER' || passLean === 'UNDER') return passLean;
  const proj = p.projection ?? (p as any).projectedValue ?? null;
  const line = typeof p.line === 'number' ? p.line : null;
  if (proj != null && line != null) {
    return proj > line ? 'OVER' : 'UNDER';
  }
  return null;
}

type Period = 'all' | 'today' | '30d' | '7d';

function filterByPeriod(picks: Pick[], period: Period) {
  if (period === 'all') return picks;
  if (period === 'today') {
    const now = new Date();
    const startOfTodayUtc = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
    return picks.filter(p => {
      const timestamp = new Date((p as any).settledAt || (p as any).timestamp || p.createdAt || 0).getTime();
      return Number.isFinite(timestamp) && timestamp >= startOfTodayUtc;
    });
  }
  const days = period === '30d' ? 30 : 7;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  return picks.filter(p => new Date(p.createdAt || p.settledAt || 0) >= cutoff);
}

function pickEventKey(p: Pick): string {
  const direction = getRecDir(p) || String(p.recommendation || '').toUpperCase();
  const base = [
    p.sport || 'soccer',
    p.fixtureId ?? p.fixtureDate ?? '',
    p.playerName || '',
    p.teamName || '',
    p.opponentName || '',
    p.propType || '',
    p.line ?? '',
    direction,
  ];
  if (base[1]) return base.join('|');
  return `${base.join('|')}|${(p.createdAt || '').slice(0, 16)}`;
}

function uniquePickEvents(picks: Pick[]): Pick[] {
  const chosen = new Map<string, Pick>();
  for (const pick of picks) {
    const key = pickEventKey(pick);
    const previous = chosen.get(key);
    if (!previous) {
      chosen.set(key, pick);
      continue;
    }
    const previousTime = new Date(previous.createdAt || 0).getTime();
    const currentTime = new Date(pick.createdAt || 0).getTime();
    if (currentTime >= previousTime) chosen.set(key, pick);
  }
  return Array.from(chosen.values());
}

export function computeAnalytics(picks: Pick[], period: Period) {
  const filtered = uniquePickEvents(filterByPeriod(picks, period));
  const settled = filtered.filter(p => ['hit', 'miss', 'push'].includes(p.result || ''));
  const decided = settled.filter(p => p.result === 'hit' || p.result === 'miss');
  const hits = decided.filter(p => p.result === 'hit').length;
  const misses = decided.filter(p => p.result === 'miss').length;
  const winRate = decided.length > 0 ? Math.round((hits / decided.length) * 100) : 0;

  // Streaks
  const sorted = [...decided].sort((a, b) =>
    new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime()
  );
  let currentStreak = 0;
  for (const p of sorted) {
    if (p.result === 'hit') currentStreak++;
    else break;
  }
  let longest = 0, run = 0;
  for (const p of sorted) {
    if (p.result === 'hit') { run++; longest = Math.max(longest, run); }
    else run = 0;
  }

  // Over/Under split
  const overPicks = decided.filter(p => getRecDir(p) === 'OVER');
  const underPicks = decided.filter(p => getRecDir(p) === 'UNDER');
  const overHit = overPicks.length ? Math.round((overPicks.filter(p => p.result === 'hit').length / overPicks.length) * 100) : 0;
  const underHit = underPicks.length ? Math.round((underPicks.filter(p => p.result === 'hit').length / underPicks.length) * 100) : 0;

  // Confidence tier performance
  const tierBuckets: Record<string, { hit: number; total: number }> = {};
  for (const p of decided) {
    const tier = (p.confidenceLevel || 'Unknown').trim() || 'Unknown';
    if (!tierBuckets[tier]) tierBuckets[tier] = { hit: 0, total: 0 };
    tierBuckets[tier].total++;
    if (p.result === 'hit') tierBuckets[tier].hit++;
  }
  const tiers = Object.entries(tierBuckets)
    .map(([tier, v]) => ({ tier, ...v, rate: v.total > 0 ? Math.round((v.hit / v.total) * 100) : 0 }))
    .sort((a, b) => b.total - a.total);

  // By dimension with over/under
  const byDim = (key: keyof Pick) => {
    const buckets: Record<string, { hit: number; miss: number; overHit: number; overTotal: number; underHit: number; underTotal: number }> = {};
    for (const p of decided) {
      const val = ((p[key] as any) || 'unknown') as string;
      if (!val || val === 'unknown') continue;
      if (!buckets[val]) buckets[val] = { hit: 0, miss: 0, overHit: 0, overTotal: 0, underHit: 0, underTotal: 0 };
      if (p.result === 'hit') buckets[val].hit++;
      if (p.result === 'miss') buckets[val].miss++;
      const dir = getRecDir(p);
      if (dir === 'OVER') { buckets[val].overTotal++; if (p.result === 'hit') buckets[val].overHit++; }
      if (dir === 'UNDER') { buckets[val].underTotal++; if (p.result === 'hit') buckets[val].underHit++; }
    }
    return Object.entries(buckets)
      .map(([k, v]) => ({
        label: k,
        total: v.hit + v.miss,
        rate: v.hit + v.miss > 0 ? Math.round((v.hit / (v.hit + v.miss)) * 100) : 0,
        overRate: v.overTotal > 0 ? Math.round((v.overHit / v.overTotal) * 100) : 0,
        underRate: v.underTotal > 0 ? Math.round((v.underHit / v.underTotal) * 100) : 0,
      }))
      .filter(v => v.total >= 2)
      .sort((a, b) => b.total - a.total);
  };

  // Win rate trend over time (daily buckets)
  const daily: Record<string, { hit: number; total: number }> = {};
  for (const p of decided) {
    const d = new Date(p.createdAt || p.settledAt || 0);
    if (isNaN(d.getTime())) continue;
    const key = d.toISOString().split('T')[0];
    if (!daily[key]) daily[key] = { hit: 0, total: 0 };
    daily[key].total++;
    if (p.result === 'hit') daily[key].hit++;
  }
  const trend = Object.entries(daily)
    .map(([date, v]) => ({ date, rate: v.total > 0 ? Math.round((v.hit / v.total) * 100) : 0, total: v.total }))
    .sort((a, b) => a.date.localeCompare(b.date));

  // Best/worst by league (min 5)
  const leagueStats = byDim('leagueId').map(x => ({ ...x, label: getLeagueLabel(Number(x.label)) })).filter(x => x.total >= 5);
  const bestLeagues = [...leagueStats].sort((a, b) => b.rate - a.rate).slice(0, 3);
  const worstLeagues = [...leagueStats].sort((a, b) => a.rate - b.rate).slice(0, 3);

  return {
    total: filtered.length,
    settled: settled.length,
    hits,
    misses,
    pushes: settled.filter(p => p.result === 'push').length,
    winRate,
    currentStreak,
    longestStreak: longest,
    overHit,
    underHit,
    overTotal: overPicks.length,
    underTotal: underPicks.length,
    tiers,
    trend,
    byLeague: leagueStats,
    byProp: byDim('propType'),
    bySport: byDim('sport'),
    bestLeagues,
    worstLeagues,
  };
}

const SCREEN_WIDTH = Dimensions.get('window').width;
const CHART_WIDTH = SCREEN_WIDTH - 64;
const CHART_HEIGHT = 120;

export default function AnalyticsDashboard({
  visible,
  picks,
  onClose,
}: {
  visible: boolean;
  picks: Pick[];
  onClose: () => void;
}) {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const [period, setPeriod] = useState<Period>('all');
  const [cleanupRunning, setCleanupRunning] = useState(false);
  const queryClient = useQueryClient();
  const localStats = useMemo(() => computeAnalytics(picks, period), [picks, period]);
  const { data: ownerData } = useQuery<AnalyticsData>({
    queryKey: ['ownerAnalytics', 'pick-insights', session?.email, period],
    queryFn: () => getOwnerAnalytics(session!.email, session!.token, period),
    enabled: visible && !!session,
    staleTime: 60_000,
  });
  const { data: storageHealth, refetch: refetchStorage } = useQuery({
    queryKey: ['storageHealth', session?.email],
    queryFn: () => getStorageHealth(session!.email, session!.token),
    enabled: visible && !!session,
    staleTime: 5 * 60_000,   // 5 minutes — lightweight call, no need to hammer
  });
  const { data: quotaStatus, refetch: refetchQuota } = useQuery({
    queryKey: ['quotaStatus', session?.email],
    queryFn: () => getQuotaStatus(session!.email, session!.token),
    enabled: visible && !!session,
    staleTime: 2 * 60_000,   // 2 minutes — show fresh counts
  });
  const { data: bzzoiroStatus } = useQuery<BzzoiroStatus>({
    queryKey: ['bzzoiroStatus', session?.email],
    queryFn: () => getBzzoiroStatus(session!.email, session!.token),
    enabled: visible && !!session,
    staleTime: 10 * 60_000,  // 10 minutes — env vars change rarely
  });
  const [quotaResetting, setQuotaResetting] = React.useState(false);

  const handleQuotaReset = async () => {
    if (!session || quotaResetting) return;
    setQuotaResetting(true);
    try {
      const result = await resetQuotaBreaker(session.email, session.token);
      await refetchQuota();
      Alert.alert('Quota Reset', result.message);
    } catch (err: any) {
      Alert.alert('Reset Failed', err?.message ?? 'Unknown error');
    } finally {
      setQuotaResetting(false);
    }
  };

  const handleTriggerCleanup = async () => {
    if (!session || cleanupRunning) return;
    setCleanupRunning(true);
    try {
      const result = await triggerStorageCleanup(session.email, session.token);
      await refetchStorage();
      queryClient.invalidateQueries({ queryKey: ['ownerAnalytics'] });
      Alert.alert(
        'Cleanup Complete',
        `Removed ${result.totalDeleted.toLocaleString()} stale cache documents.\n\nBreakdown:\n` +
          Object.entries(result.deleted)
            .filter(([k]) => !k.endsWith('_error'))
            .map(([k, v]) => `  ${k}: ${v}`)
            .join('\n'),
      );
    } catch (err: any) {
      Alert.alert('Cleanup Failed', err?.message ?? 'Unknown error');
    } finally {
      setCleanupRunning(false);
    }
  };
  // Insights is the ReversePicks system ledger. Personal picks remain the
  // source for Live/Settled Picks and are never used for this report when the
  // owner dataset is available.
  const stats = (ownerData?.insights ?? localStats) as ReturnType<typeof computeAnalytics>;

  const renderQuotaUsage = () => {
    if (!quotaStatus) return null;
    const { active, trippedDate, dailyCallCount, softLimit, hardLimit, date, lastResetAt } = quotaStatus;

    // The soft limit is the operative enforcement threshold — background API
    // calls are suspended once it is reached, long before the provider's
    // hard plan limit. Warnings and the progress bar reflect soft-limit
    // headroom so the owner sees actionable usage, not a misleadingly small
    // fraction of the 450 k provider ceiling.
    const softUsedPct  = softLimit > 0 ? Math.min(100, (dailyCallCount / softLimit) * 100) : 0;
    const hardUsedPct  = hardLimit > 0 ? Math.min(100, (dailyCallCount / hardLimit) * 100) : 0;
    const barColor     = active ? Colors.error : softUsedPct >= 80 ? '#f59e0b' : Colors.success;
    const statusLabel  = active ? '⚠ BREAKER TRIPPED' : softUsedPct >= 80 ? '⚠ BUDGET HIGH' : softUsedPct >= 50 ? '↑ MID USAGE' : '✓ OK';
    const statusColor  = active ? Colors.error : softUsedPct >= 80 ? '#f59e0b' : Colors.primary;

    return (
      <View style={[s.storageCard, active && s.storageCardDegraded]}>
        <View style={s.storageHeader}>
          <Text style={s.chartTitle}>API-FOOTBALL QUOTA</Text>
          <Text style={[s.storageStatus, { color: statusColor }]}>{statusLabel}</Text>
        </View>

        {/* Soft-budget bar — this is the operative enforcement limit */}
        <View style={s.storageBarRow}>
          <View style={s.storageBarTrack}>
            <View style={[s.storageBarFill, { width: `${softUsedPct}%`, backgroundColor: barColor }]} />
          </View>
          <Text style={[s.storageBarLabel, { color: barColor }]}>{softUsedPct.toFixed(0)}%</Text>
        </View>
        <Text style={s.storageBarMeta}>
          {dailyCallCount.toLocaleString()} / {softLimit.toLocaleString()} background budget today ({date})
        </Text>

        {/* Secondary: provider plan headroom */}
        <Text style={[s.storageBarMeta, { marginTop: 3 }]}>
          Provider plan: {dailyCallCount.toLocaleString()} / {hardLimit.toLocaleString()} ({hardUsedPct.toFixed(2)}% of {(hardLimit / 1000).toFixed(0)}k limit)
        </Text>

        {trippedDate ? (
          <Text style={[s.storageBarMeta, { color: Colors.error, marginTop: 4 }]}>
            Breaker tripped: {trippedDate} — all API calls suspended until midnight UTC
          </Text>
        ) : null}

        {lastResetAt ? (
          <Text style={[s.storageBarMeta, { color: Colors.textTertiary, marginTop: 3 }]}>
            Last reset: {(() => {
              try {
                const d = new Date(lastResetAt);
                const now = new Date();
                const diffMs = now.getTime() - d.getTime();
                const diffMins = Math.floor(diffMs / 60000);
                if (diffMins < 1) return 'just now';
                if (diffMins < 60) return `${diffMins}m ago`;
                const diffHrs = Math.floor(diffMins / 60);
                if (diffHrs < 24) return `${diffHrs}h ago`;
                return d.toLocaleString();
              } catch {
                return lastResetAt;
              }
            })()}
          </Text>
        ) : null}

        {active && (
          <TouchableOpacity
            style={[s.cleanupBtn, quotaResetting && s.cleanupBtnDisabled, { marginTop: 8 }]}
            onPress={handleQuotaReset}
            disabled={quotaResetting}
          >
            <Ionicons name="refresh-outline" size={13} color={quotaResetting ? Colors.textTertiary : Colors.error} />
            <Text style={[s.cleanupBtnText, { color: quotaResetting ? Colors.textTertiary : Colors.error }]}>
              {quotaResetting ? 'Resetting…' : 'Reset breaker now'}
            </Text>
          </TouchableOpacity>
        )}
      </View>
    );
  };

  const renderBzzoiroWarning = () => {
    if (!bzzoiroStatus) return null;
    // Only show the card when the token is set — if no token, Bzzoiro is
    // disabled and there's nothing to warn about.
    if (!bzzoiroStatus.tokenSet) return null;
    if (bzzoiroStatus.commercialConfirmed) {
      // Confirmed — show a compact OK row so the owner knows it's resolved.
      return (
        <View style={[s.storageCard, { borderColor: 'rgba(57,255,20,0.25)' }]}>
          <View style={s.storageHeader}>
            <Text style={s.chartTitle}>BZZOIRO ENRICHMENT</Text>
            <Text style={[s.storageStatus, { color: Colors.success }]}>✓ TERMS CONFIRMED</Text>
          </View>
          <Text style={s.storageBarMeta}>
            Commercial-use terms confirmed. Shadow enrichment active.
          </Text>
        </View>
      );
    }
    return (
      <View style={[s.storageCard, s.storageCardDegraded, { borderColor: 'rgba(245,158,11,0.4)', backgroundColor: 'rgba(245,158,11,0.07)' }]}>
        <View style={s.storageHeader}>
          <Text style={s.chartTitle}>BZZOIRO ENRICHMENT</Text>
          <Text style={[s.storageStatus, { color: '#f59e0b' }]}>⚠ TERMS UNCONFIRMED</Text>
        </View>
        <Text style={[s.storageBarMeta, { color: '#f59e0b', marginBottom: 6 }]}>
          BZZOIRO_API_TOKEN is set but commercial-use terms have not been verified.
          Bzzoiro data is shadow-only and must not influence production projections
          until terms are confirmed.
        </Text>
        <Text style={s.storageBarMeta}>
          To resolve: review Bzzoiro's commercial terms, then set{' '}
          <Text style={{ fontWeight: '700', color: Colors.text }}>
            BZZOIRO_COMMERCIAL_CONFIRMED=true
          </Text>{' '}
          in the production environment.
        </Text>
      </View>
    );
  };

  const renderStorageHealth = () => {
    if (!storageHealth) return null;
    const { usedPct, totalMb, limitMb, status, collections, degraded, warning } = storageHealth;
    const pct = usedPct ?? 0;
    const barColor = degraded ? Colors.error : warning ? '#f59e0b' : Colors.success;
    const statusLabel = status === 'DEGRADED' ? '⚠ WRITES BLOCKED' : status === 'WARNING' ? '⚠ HIGH USAGE' : '✓ OK';
    const statusColor = degraded ? Colors.error : warning ? '#f59e0b' : Colors.primary;

    // Top collections by storage
    const collEntries = Object.entries(collections ?? {})
      .filter(([, v]) => v != null)
      .sort((a, b) => ((b[1]?.storageMb ?? 0) - (a[1]?.storageMb ?? 0)))
      .slice(0, 6);

    return (
      <View style={[s.storageCard, degraded && s.storageCardDegraded]}>
        <View style={s.storageHeader}>
          <Text style={s.chartTitle}>ATLAS STORAGE</Text>
          <Text style={[s.storageStatus, { color: statusColor }]}>{statusLabel}</Text>
        </View>

        {/* Usage bar */}
        <View style={s.storageBarRow}>
          <View style={s.storageBarTrack}>
            <View style={[s.storageBarFill, { width: `${Math.min(100, pct)}%`, backgroundColor: barColor }]} />
          </View>
          <Text style={[s.storageBarLabel, { color: barColor }]}>{pct.toFixed(1)}%</Text>
        </View>
        <Text style={s.storageBarMeta}>
          {totalMb != null ? `${totalMb.toFixed(0)} MB used of ${limitMb} MB free-tier limit` : 'Storage size unavailable'}
        </Text>

        {/* Per-collection breakdown */}
        {collEntries.length > 0 && (
          <View style={s.storageCollList}>
            {collEntries.map(([name, v]) => (
              <View key={name} style={s.storageCollRow}>
                <Text style={s.storageCollName} numberOfLines={1}>{name.replace(/_/g, ' ')}</Text>
                <Text style={s.storageCollCount}>{v!.count.toLocaleString()}</Text>
                <Text style={s.storageCollMb}>{v!.storageMb.toFixed(1)} MB</Text>
              </View>
            ))}
          </View>
        )}

        {/* Manual cleanup trigger */}
        <TouchableOpacity
          style={[s.cleanupBtn, cleanupRunning && s.cleanupBtnDisabled]}
          onPress={handleTriggerCleanup}
          disabled={cleanupRunning}
        >
          <Ionicons name="trash-outline" size={13} color={cleanupRunning ? Colors.textTertiary : Colors.text} />
          <Text style={[s.cleanupBtnText, cleanupRunning && { color: Colors.textTertiary }]}>
            {cleanupRunning ? 'Cleaning up…' : 'Run cleanup now'}
          </Text>
        </TouchableOpacity>
      </View>
    );
  };

  const renderTrendChart = () => {
    if (stats.trend.length < 2) return (
      <View style={s.chartEmpty}>
        <Text style={s.chartEmptyText}>Need more settled picks to show trend</Text>
      </View>
    );
    const data = stats.trend;
    const maxY = 100;
    const minY = 0;
    const xStep = CHART_WIDTH / (data.length - 1);
    const points = data.map((d, i) => {
      const x = i * xStep;
      const y = CHART_HEIGHT - ((d.rate - minY) / (maxY - minY)) * CHART_HEIGHT;
      return { x, y, ...d };
    });
    const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
    const avgRate = data.reduce((a, b) => a + b.rate, 0) / data.length;
    const avgY = CHART_HEIGHT - ((avgRate - minY) / (maxY - minY)) * CHART_HEIGHT;

    return (
      <View style={s.chartCard}>
        <View style={s.chartHeader}>
          <Text style={s.chartTitle}>Win Rate Trend</Text>
          <Text style={s.chartSubtitle}>{data.length} days · avg {Math.round(avgRate)}%</Text>
        </View>
        <Svg width={CHART_WIDTH} height={CHART_HEIGHT + 24}>
          <Defs>
            <LinearGradient id="trendGrad" x1="0" y1="0" x2="0" y2="1">
              <Stop offset="0" stopColor={Colors.primary} stopOpacity="0.35" />
              <Stop offset="1" stopColor={Colors.primary} stopOpacity="0" />
            </LinearGradient>
          </Defs>
          <G>
            {[0, 25, 50, 75, 100].map((tick, i) => {
              const y = CHART_HEIGHT - (tick / 100) * CHART_HEIGHT;
              return (
                <G key={i}>
                  <Line x1="0" y1={y} x2={CHART_WIDTH} y2={y} stroke="rgba(255,255,255,0.06)" strokeWidth={1} />
                </G>
              );
            })}
            <Line x1="0" y1={avgY} x2={CHART_WIDTH} y2={avgY} stroke="rgba(255,255,255,0.3)" strokeWidth={1} strokeDasharray="4 4" />
            <Path d={`${pathD} L ${CHART_WIDTH} ${CHART_HEIGHT} L 0 ${CHART_HEIGHT} Z`} fill="url(#trendGrad)" />
            <Path d={pathD} fill="none" stroke={Colors.primary} strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />
            {points.map((p, i) => (
              <Circle key={i} cx={p.x} cy={p.y} r={3.5} fill={p.rate >= 50 ? Colors.success : Colors.error} stroke="#000" strokeWidth={1.5} />
            ))}
          </G>
        </Svg>
        <View style={s.trendLabels}>
          <Text style={s.trendLabel}>{data[0].date.slice(5)}</Text>
          <Text style={s.trendLabel}>{data[data.length - 1].date.slice(5)}</Text>
        </View>
      </View>
    );
  };

  const renderOverUnder = () => (
    <View style={s.chartCard}>
      <View style={s.chartHeader}>
        <Text style={s.chartTitle}>OVER vs UNDER</Text>
        <Text style={s.chartSubtitle}>Which direction is winning?</Text>
      </View>
      <View style={s.ouRow}>
        <View style={s.ouBar}>
          <Text style={s.ouLabel}>OVER</Text>
          <View style={s.ouTrack}>
            <View style={[s.ouFill, { width: `${stats.overHit}%`, backgroundColor: Colors.primary }]} />
          </View>
          <Text style={s.ouRate}>{stats.overHit}% <Text style={s.ouN}>({stats.overTotal})</Text></Text>
        </View>
        <View style={s.ouBar}>
          <Text style={s.ouLabel}>UNDER</Text>
          <View style={s.ouTrack}>
            <View style={[s.ouFill, { width: `${stats.underHit}%`, backgroundColor: Colors.error }]} />
          </View>
          <Text style={s.ouRate}>{stats.underHit}% <Text style={s.ouN}>({stats.underTotal})</Text></Text>
        </View>
      </View>
    </View>
  );

  const renderTierCards = () => {
    if (!stats.tiers.length) return null;
    return (
      <View style={s.chartCard}>
        <View style={s.chartHeader}>
          <Text style={s.chartTitle}>By Confidence Tier</Text>
          <Text style={s.chartSubtitle}>Are high-confidence picks actually hitting?</Text>
        </View>
        <View style={s.tierGrid}>
          {stats.tiers.map((t, i) => (
            <View key={i} style={s.tierCard}>
              <Text style={s.tierName}>{t.tier.toUpperCase()}</Text>
              <Text style={[s.tierRate, { color: t.rate >= 60 ? Colors.success : t.rate >= 50 ? Colors.primary : Colors.error }]}>{t.rate}%</Text>
              <Text style={s.tierN}>{t.total} picks</Text>
            </View>
          ))}
        </View>
      </View>
    );
  };

  const renderLeaderboard = (title: string, rows: { label: string; rate: number; total: number }[], color: string) => {
    if (!rows.length) return null;
    return (
      <View style={s.chartCard}>
        <View style={s.chartHeader}>
          <Text style={s.chartTitle}>{title}</Text>
        </View>
        {rows.map((r, i) => (
          <View key={i} style={s.leaderRow}>
            <Text style={s.leaderRank}>#{i + 1}</Text>
            <Text style={s.leaderLabel} numberOfLines={1}>{r.label}</Text>
            <View style={s.leaderBarTrack}>
              <View style={[s.leaderBarFill, { width: `${Math.max(4, r.rate)}%`, backgroundColor: color }]} />
            </View>
            <Text style={[s.leaderRate, { color }]}>{r.rate}%</Text>
            <Text style={s.leaderN}>({r.total})</Text>
          </View>
        ))}
      </View>
    );
  };

  const renderDimensionTable = (title: string, rows: ReturnType<typeof computeAnalytics>['byLeague']) => {
    if (!rows.length) return null;
    return (
      <View style={s.chartCard}>
        <View style={s.chartHeader}>
          <Text style={s.chartTitle}>{title}</Text>
        </View>
        {rows.slice(0, 8).map((r, i) => (
          <View key={i} style={s.dimRow}>
            <Text style={s.dimLabel} numberOfLines={1}>{r.label}</Text>
            <View style={s.dimBarTrack}>
              <View style={[s.dimBarFill, { width: `${Math.max(4, r.rate)}%`, backgroundColor: r.rate >= 60 ? Colors.success : r.rate >= 50 ? Colors.primary : Colors.error }]} />
            </View>
            <View style={s.dimOu}>
              <Text style={[s.dimOuText, { color: Colors.primary }]}>O {r.overRate}%</Text>
              <Text style={[s.dimOuText, { color: Colors.error }]}>U {r.underRate}%</Text>
            </View>
            <Text style={s.dimRate}>{r.rate}%</Text>
            <Text style={s.dimN}>({r.total})</Text>
          </View>
        ))}
      </View>
    );
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={[s.backdrop, { paddingTop: insets.top + 30 }]}>
        <View style={s.sheet}>
          <View style={s.sheetHeader}>
            <Text style={s.sheetTitle}>Pick Insights</Text>
            <TouchableOpacity onPress={onClose} style={s.closeBtn}>
              <Ionicons name="close" size={18} color={Colors.text} />
            </TouchableOpacity>
          </View>

          {/* Period filters */}
          <View style={s.periodRow}>
            {(['all', 'today', '30d', '7d'] as Period[]).map(p => (
              <TouchableOpacity
                key={p}
                onPress={() => setPeriod(p)}
                style={[s.periodBtn, period === p && s.periodBtnActive]}
              >
                <Text style={[s.periodBtnText, period === p && s.periodBtnTextActive]}>
                  {p === 'all' ? 'All Time' : p === 'today' ? 'Today' : p === '30d' ? 'Last 30 Days' : 'Last 7 Days'}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Summary cards */}
          <View style={s.summaryGrid}>
            <View style={s.summaryCard}>
              <Text style={s.summaryNum}>{stats.total}</Text>
              <Text style={s.summaryLbl}>PICKS</Text>
            </View>
            <View style={s.summaryCard}>
              <Text style={[s.summaryNum, { color: stats.winRate >= 60 ? Colors.success : Colors.primary }]}>{stats.winRate}%</Text>
              <Text style={s.summaryLbl}>WIN RATE</Text>
            </View>
            <View style={s.summaryCard}>
              <Text style={[s.summaryNum, { color: Colors.success }]}>{stats.currentStreak}W</Text>
              <Text style={s.summaryLbl}>STREAK</Text>
            </View>
            <View style={s.summaryCard}>
              <Text style={s.summaryNum}>{stats.pushes}</Text>
              <Text style={s.summaryLbl}>PUSHES</Text>
            </View>
          </View>

          <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 32 }}>
            {renderQuotaUsage()}
            {renderStorageHealth()}
            {renderBzzoiroWarning()}
            {ownerData?.scorecard && (
              <View style={s.ownerHealthCard}>
                <View style={s.chartHeader}>
                  <Text style={s.chartTitle}>REVERSEPICKS MODEL HEALTH</Text>
                  <Text style={s.chartSubtitle}>ALL USERS · SOCCER</Text>
                </View>
                {/* Deduplication pipeline — raw saves → unique events → scored */}
                <View style={s.dedupRow}>
                  <View style={s.dedupCell}>
                    <Text style={s.dedupNum}>
                      {ownerData.scorecard.rawN ?? ownerData.scope?.rawSettled ?? ownerData.overall.total}
                    </Text>
                    <Text style={s.dedupLabel}>RAW SAVES</Text>
                  </View>
                  <Text style={s.dedupArrow}>→</Text>
                  <View style={s.dedupCell}>
                    <Text style={[s.dedupNum, { color: Colors.primary }]}>
                      {ownerData.scorecard.n}
                    </Text>
                    <Text style={s.dedupLabel}>UNIQUE EVENTS</Text>
                  </View>
                  <Text style={s.dedupArrow}>→</Text>
                  <View style={s.dedupCell}>
                    <Text style={[s.dedupNum, { color: Colors.success }]}>
                      {ownerData.scorecard.scoredN ?? (ownerData.overall.hits + ownerData.overall.misses)}
                    </Text>
                    <Text style={s.dedupLabel}>SCORED HIT/MISS</Text>
                  </View>
                </View>
                {(ownerData.scorecard.duplicateRowsRemoved ?? ownerData.scope?.duplicateRowsRemoved ?? 0) > 0 && (
                  <Text style={s.dedupNote}>
                    {ownerData.scorecard.duplicateRowsRemoved ?? ownerData.scope?.duplicateRowsRemoved} duplicate rows collapsed
                  </Text>
                )}
                {ownerData.overall.passCalibration?.n ? (
                  <Text style={s.ownerHealthMeta}>
                    Legacy PASS metadata (audit only): {ownerData.overall.passCalibration.hits} avoided-direction hits · {ownerData.overall.passCalibration.misses} misses · {ownerData.overall.passCalibration.winPct}% directional accuracy
                  </Text>
                ) : null}
                <View style={s.ownerHealthGrid}>
                  <View>
                    <Text style={s.ownerHealthValue}>{ownerData.overall.hits}</Text>
                    <Text style={s.ownerHealthLabel}>HITS</Text>
                  </View>
                  <View>
                    <Text style={[s.ownerHealthValue, { color: Colors.error }]}>{ownerData.overall.misses}</Text>
                    <Text style={s.ownerHealthLabel}>MISSES</Text>
                  </View>
                  <View>
                    <Text style={s.ownerHealthValue}>{ownerData.overall.pushes ?? 0}</Text>
                    <Text style={s.ownerHealthLabel}>PUSHES</Text>
                  </View>
                  <View>
                    <Text style={s.ownerHealthValue}>{ownerData.overall.dnps ?? 0}</Text>
                    <Text style={s.ownerHealthLabel}>DNP</Text>
                  </View>
                </View>
                <Text style={s.ownerHealthMetrics}>
                  Log loss {ownerData.scorecard.classification.finalConfidence.logLoss?.toFixed(3) ?? '—'} · Brier {ownerData.scorecard.classification.finalConfidence.brierScore?.toFixed(3) ?? '—'} · MAE {ownerData.scorecard.projection.overall.mae?.toFixed(2) ?? '—'} · RMSE {ownerData.scorecard.projection.overall.rmse?.toFixed(2) ?? '—'}
                </Text>
                {ownerData.walkForwardReplay?.byDirection && (
                  <Text style={s.ownerHealthMeta}>
                    Walk-forward direction: OVER {ownerData.walkForwardReplay.byDirection.over?.hitRate ?? '—'}% ({ownerData.walkForwardReplay.byDirection.over?.n ?? 0}) · UNDER {ownerData.walkForwardReplay.byDirection.under?.hitRate ?? '—'}% ({ownerData.walkForwardReplay.byDirection.under?.n ?? 0})
                  </Text>
                )}
                {ownerData.walkForwardReplay?.bySport && ownerData.walkForwardReplay.bySport.length > 0 && (
                  <View style={s.ownerPropList}>
                    <Text style={s.ownerHealthLabel}>WALK-FORWARD ACCURACY BY SPORT</Text>
                    <View style={s.wfTableHeader}>
                      <Text style={[s.wfTableCell, s.wfTableCellSport]}>SPORT</Text>
                      <Text style={[s.wfTableCell, s.wfTableCellN]}>N</Text>
                      <Text style={[s.wfTableCell, s.wfTableCellNum]}>LOG-LOSS</Text>
                      <Text style={[s.wfTableCell, s.wfTableCellNum]}>BRIER</Text>
                      <Text style={[s.wfTableCell, s.wfTableCellNum]}>MAE</Text>
                    </View>
                    {ownerData.walkForwardReplay.bySport.map((row) => {
                      const cls = row.classification;
                      const prj = row.projection;
                      const logLossColor =
                        cls.logLoss == null ? Colors.textTertiary
                        : cls.logLoss < 0.65 ? Colors.success
                        : cls.logLoss < 0.693 ? '#F59E0B'
                        : Colors.error;
                      return (
                        <View key={row.sport} style={s.wfTableRow}>
                          <Text style={[s.wfTableCell, s.wfTableCellSport, { color: Colors.text }]}>
                            {row.sport.toUpperCase()}
                          </Text>
                          <Text style={[s.wfTableCell, s.wfTableCellN, { color: Colors.textSecondary }]}>
                            {cls.n || prj.n || 0}
                          </Text>
                          <Text style={[s.wfTableCell, s.wfTableCellNum, { color: logLossColor }]}>
                            {cls.logLoss != null ? cls.logLoss.toFixed(3) : '—'}
                          </Text>
                          <Text style={[s.wfTableCell, s.wfTableCellNum, { color: Colors.textSecondary }]}>
                            {cls.brierScore != null ? cls.brierScore.toFixed(3) : '—'}
                          </Text>
                          <Text style={[s.wfTableCell, s.wfTableCellNum, { color: Colors.textSecondary }]}>
                            {prj.mae != null ? prj.mae.toFixed(2) : '—'}
                          </Text>
                        </View>
                      );
                    })}
                  </View>
                )}
                {ownerData.walkForwardReplay?.byProp && ownerData.walkForwardReplay.byProp.length > 0 && (
                  <View style={s.ownerPropList}>
                    <Text style={s.ownerHealthLabel}>WALK-FORWARD MAE BY PROP TYPE</Text>
                    {ownerData.walkForwardReplay.byProp.slice(0, 8).map((row) => (
                      <Text key={`${row.sport}-${row.propType}`} style={s.ownerPropRow}>
                        {row.sport.toUpperCase()} · {row.propType.replace(/_/g, ' ')} · n={row.n} · MAE {row.mae?.toFixed(2) ?? '—'} · bias {row.meanError != null ? (row.meanError > 0 ? '+' : '') + row.meanError.toFixed(2) : '—'}
                      </Text>
                    ))}
                  </View>
                )}
                {ownerData.overall.outcomeCounts?.unknown ? (
                  <Text style={s.ownerHealthMeta}>
                    Unclassified settled rows: {ownerData.overall.outcomeCounts.unknown} · excluded from win rate and probability metrics
                  </Text>
                ) : null}
                {ownerData.scorecard.classification.calibration.length > 0 && (
                  <Text style={s.ownerHealthMeta}>
                    Calibration gaps: {ownerData.scorecard.classification.calibration
                      .slice(0, 3)
                      .map((bin) => `${bin.label} ${bin.gapPp > 0 ? '+' : ''}${bin.gapPp.toFixed(1)}pp`)
                      .join(' · ')}
                  </Text>
                )}
                {ownerData.scorecard.projection.byProp.length > 0 && (
                  <View style={s.ownerPropList}>
                    <Text style={s.ownerHealthLabel}>PROJECTION ERROR BY PROP</Text>
                    {ownerData.scorecard.projection.byProp.slice(0, 4).map((prop) => (
                      <Text key={`${prop.sport}-${prop.propType}`} style={s.ownerPropRow}>
                        {prop.propType.replace(/_/g, ' ')} · n={prop.n} · MAE {prop.mae?.toFixed(2) ?? '—'} · RMSE {prop.rmse?.toFixed(2) ?? '—'}
                      </Text>
                    ))}
                  </View>
                )}
                <Text style={s.ownerHealthReplay}>
                  Replay: {ownerData.scorecard.chronologicalHoldout.n > 0 ? 'historical holdout available' : 'not enough settled events'}
                </Text>
              </View>
            )}
            {renderTrendChart()}
            {renderOverUnder()}
            {renderTierCards()}
            {renderLeaderboard('Best Leagues', stats.bestLeagues, Colors.success)}
            {renderLeaderboard('Worst Leagues', stats.worstLeagues, Colors.error)}
            {renderDimensionTable('By League', stats.byLeague)}
            {renderDimensionTable('By Prop Type', stats.byProp)}
            {renderDimensionTable('By Sport', stats.bySport)}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const s = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.78)',
    justifyContent: 'flex-end',
  },
  sheet: {
    flex: 1,
    backgroundColor: Colors.background,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 16,
  },
  sheetHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 14,
  },
  sheetTitle: { fontSize: 20, fontWeight: '800', color: Colors.text },
  closeBtn: { padding: 7, borderRadius: 14, backgroundColor: Colors.cardSecondary },
  periodRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 14,
  },
  periodBtn: {
    flex: 1,
    paddingVertical: 8,
    borderRadius: 8,
    backgroundColor: Colors.card,
    alignItems: 'center',
  },
  periodBtnActive: { backgroundColor: Colors.primary },
  periodBtnText: { fontSize: 11, fontWeight: '700', color: Colors.textSecondary },
  periodBtnTextActive: { color: '#000' },
  summaryGrid: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 16,
  },
  summaryCard: {
    flex: 1,
    backgroundColor: Colors.card,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  summaryNum: { fontSize: 18, fontWeight: '800', color: Colors.text },
  summaryLbl: { fontSize: 9, fontWeight: '700', color: Colors.textTertiary, marginTop: 2, letterSpacing: 0.5 },
  ownerHealthCard: {
    backgroundColor: 'rgba(57,255,20,0.06)',
    borderRadius: 14,
    padding: 14,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.25)',
  },
  ownerHealthScope: { fontSize: 11, lineHeight: 16, color: Colors.textSecondary, marginBottom: 12 },
  dedupRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 8,
    paddingVertical: 10,
    paddingHorizontal: 12,
    backgroundColor: 'rgba(0,0,0,0.2)',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.12)',
  },
  dedupCell: { flex: 1, alignItems: 'center' },
  dedupNum: { fontSize: 18, fontWeight: '800', color: Colors.text },
  dedupLabel: { fontSize: 8, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 0.5, marginTop: 2 },
  dedupArrow: { fontSize: 14, color: Colors.textTertiary, marginHorizontal: 4 },
  dedupNote: { fontSize: 10, color: Colors.textTertiary, marginBottom: 8, textAlign: 'center' },
  ownerHealthGrid: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 11 },
  ownerHealthValue: { fontSize: 18, fontWeight: '800', color: Colors.primary },
  ownerHealthLabel: { fontSize: 8, fontWeight: '800', letterSpacing: 0.8, color: Colors.textTertiary, marginTop: 2 },
  ownerHealthMetrics: { fontSize: 11, color: Colors.textSecondary, lineHeight: 16 },
  ownerHealthMeta: { fontSize: 10, color: Colors.textTertiary, lineHeight: 15, marginTop: 4 },
  ownerPropList: { marginTop: 9, gap: 3 },
  ownerPropRow: { fontSize: 10, color: Colors.textSecondary, lineHeight: 14, textTransform: 'capitalize' },
  ownerHealthReplay: { fontSize: 10, color: Colors.primary, marginTop: 6, fontWeight: '700' },
  wfTableHeader: { flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle, paddingBottom: 3, marginBottom: 3 },
  wfTableRow: { flexDirection: 'row', paddingVertical: 2 },
  wfTableCell: { fontSize: 9, fontWeight: '600', color: Colors.textTertiary },
  wfTableCellSport: { flex: 2 },
  wfTableCellN: { flex: 1, textAlign: 'right' },
  wfTableCellNum: { flex: 2, textAlign: 'right' },
  chartCard: {
    backgroundColor: Colors.card,
    borderRadius: 14,
    padding: 14,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  chartHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    marginBottom: 12,
  },
  chartTitle: { fontSize: 13, fontWeight: '800', color: Colors.text },
  chartSubtitle: { fontSize: 10, fontWeight: '600', color: Colors.textTertiary },
  chartEmpty: { paddingVertical: 40, alignItems: 'center' },
  chartEmptyText: { fontSize: 12, color: Colors.textTertiary },
  trendLabels: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: 4,
  },
  trendLabel: { fontSize: 10, color: Colors.textTertiary },
  ouRow: { gap: 12 },
  ouBar: { gap: 5 },
  ouLabel: { fontSize: 11, fontWeight: '800', color: Colors.text },
  ouTrack: {
    height: 10,
    borderRadius: 5,
    backgroundColor: Colors.cardSecondary,
    overflow: 'hidden',
  },
  ouFill: { height: 10, borderRadius: 5 },
  ouRate: { fontSize: 12, fontWeight: '800', color: Colors.text },
  ouN: { fontSize: 10, color: Colors.textTertiary, fontWeight: '500' },
  tierGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  tierCard: {
    flex: 1,
    minWidth: 80,
    backgroundColor: Colors.background,
    borderRadius: 10,
    padding: 10,
    alignItems: 'center',
  },
  tierName: { fontSize: 10, fontWeight: '800', color: Colors.textTertiary, letterSpacing: 0.5 },
  tierRate: { fontSize: 16, fontWeight: '800', marginTop: 4 },
  tierN: { fontSize: 10, color: Colors.textTertiary, marginTop: 2 },
  leaderRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 9 },
  leaderRank: { width: 26, fontSize: 11, fontWeight: '800', color: Colors.textTertiary },
  leaderLabel: { width: 110, fontSize: 12, color: Colors.text, fontWeight: '600' },
  leaderBarTrack: { flex: 1, height: 7, borderRadius: 3.5, backgroundColor: Colors.cardSecondary, marginRight: 10, overflow: 'hidden' },
  leaderBarFill: { height: 7, borderRadius: 3.5 },
  leaderRate: { width: 36, fontSize: 12, fontWeight: '700', textAlign: 'right' },
  leaderN: { width: 38, fontSize: 11, color: Colors.textTertiary, textAlign: 'right', marginLeft: 4 },
  dimRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 9 },
  dimLabel: { width: 90, fontSize: 11, color: Colors.text, fontWeight: '600' },
  dimBarTrack: { flex: 1, height: 7, borderRadius: 3.5, backgroundColor: Colors.cardSecondary, marginRight: 8, overflow: 'hidden' },
  dimBarFill: { height: 7, borderRadius: 3.5 },
  dimOu: { marginRight: 8, alignItems: 'flex-end' },
  dimOuText: { fontSize: 9, fontWeight: '700' },
  dimRate: { width: 34, fontSize: 12, fontWeight: '700', color: Colors.text, textAlign: 'right' },
  dimN: { width: 34, fontSize: 10, color: Colors.textTertiary, textAlign: 'right', marginLeft: 2 },
  // Storage health card
  storageCard: {
    backgroundColor: Colors.card,
    borderRadius: 14,
    padding: 14,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  storageCardDegraded: {
    backgroundColor: 'rgba(239,68,68,0.07)',
    borderColor: 'rgba(239,68,68,0.3)',
  },
  storageHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  storageStatus: { fontSize: 11, fontWeight: '800', letterSpacing: 0.3 },
  storageBarRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 5,
  },
  storageBarTrack: {
    flex: 1,
    height: 8,
    borderRadius: 4,
    backgroundColor: Colors.cardSecondary,
    overflow: 'hidden',
  },
  storageBarFill: { height: 8, borderRadius: 4 },
  quotaSoftTick: {
    position: 'absolute',
    top: 0,
    width: 2,
    height: 8,
    backgroundColor: 'rgba(255,255,255,0.5)',
    borderRadius: 1,
  },
  storageBarLabel: { width: 44, fontSize: 12, fontWeight: '800', textAlign: 'right' },
  storageBarMeta: { fontSize: 10, color: Colors.textTertiary, marginBottom: 10 },
  storageCollList: { gap: 4, marginBottom: 10 },
  storageCollRow: { flexDirection: 'row', alignItems: 'center' },
  storageCollName: { flex: 1, fontSize: 10, color: Colors.textSecondary, textTransform: 'capitalize' },
  storageCollCount: { width: 60, fontSize: 10, color: Colors.textTertiary, textAlign: 'right' },
  storageCollMb: { width: 52, fontSize: 10, color: Colors.textSecondary, fontWeight: '700', textAlign: 'right' },
  cleanupBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 8,
    borderRadius: 8,
    backgroundColor: Colors.cardSecondary,
    marginTop: 4,
  },
  cleanupBtnDisabled: { opacity: 0.5 },
  cleanupBtnText: { fontSize: 12, fontWeight: '700', color: Colors.text },
});
