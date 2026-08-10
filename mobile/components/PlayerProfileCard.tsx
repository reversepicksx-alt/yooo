import React, { useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Modal,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { Pick, getPlayerAdvancedStats } from '@/lib/api';

interface PlayerProfileCardProps {
  visible: boolean;
  onClose: () => void;
  playerName: string;
  playerId?: number;
  picks: Pick[];
}

const formatProp = (prop: string) => {
  if (!prop) return 'Unknown';
  return prop
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
};

const isHit = (r?: string) => !!r && ['hit', 'won'].includes(r.toLowerCase());
const isMiss = (r?: string) => !!r && ['miss', 'lost'].includes(r.toLowerCase());
const isPush = (r?: string) => r === 'push';
const isDnp = (r?: string) => r === 'dnp';

function StatBox({
  label,
  value,
  subtext,
  highlightColor,
}: {
  label: string;
  value: string | number;
  subtext?: string;
  highlightColor?: string;
}) {
  return (
    <View style={styles.statBox}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text
        style={[styles.statValue, highlightColor ? { color: highlightColor } : null]}
        numberOfLines={1}
        adjustsFontSizeToFit
      >
        {value}
      </Text>
      {subtext ? (
        <Text style={styles.statSubtext} numberOfLines={1}>
          {subtext}
        </Text>
      ) : null}
    </View>
  );
}

function formatDate(iso?: string) {
  if (!iso) return 'Unknown';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' });
}

function PickRow({ pick, index }: { pick: Pick; index: number }) {
  const result = pick.result || '';
  const won = isHit(result);
  const lost = isMiss(result);
  const push = isPush(result);
  const dnp = isDnp(result);
  const pending = !won && !lost && !push && !dnp;

  const resultColor = won
    ? Colors.success
    : lost
    ? Colors.error
    : push
    ? Colors.push
    : dnp
    ? Colors.dnp
    : Colors.textTertiary;
  const resultText = won ? 'W' : lost ? 'L' : push ? 'P' : dnp ? 'DNP' : '-';

  const rec = (pick.recommendation || '').toUpperCase();
  const recColor = rec === 'OVER' ? Colors.success : rec === 'UNDER' ? Colors.error : Colors.textSecondary;

  const venue = (pick.venue || '').toLowerCase();
  const isHome = venue === 'home';
  const isAway = venue === 'away';
  const venueText = isHome ? 'Home' : isAway ? 'Away' : venue ? venue[0].toUpperCase() + venue.slice(1) : 'Unknown';
  const opponentName = pick.opponentName || 'Unknown';
  const teamName = pick.teamName || '';
  const leagueName = pick.leagueName || '';
  const confidence = pick.confidence ?? pick.projection;
  const confidenceLevel = pick.confidenceLevel || '';

  const score = pick.matchScore || (pick.finalHomeGoals != null && pick.finalAwayGoals != null
    ? `${pick.finalHomeGoals} - ${pick.finalAwayGoals}`
    : '');

  // Resolve opponent possession from pick fields
  let oppPoss: number | null = null;
  let oppPossLabel = '';
  let oppAvgPoss: number | null = null;
  if (isHome && pick.awayPoss != null) {
    oppPoss = pick.awayPoss;
  } else if (isAway && pick.homePoss != null) {
    oppPoss = pick.homePoss;
  } else if (pick.homePoss != null && pick.awayPoss != null) {
    // fallback if venue unknown: just pick the one that isn't the player's team
    oppPoss = isHome ? pick.awayPoss : pick.homePoss;
  }
  if (pick.oppAvgPoss != null) oppAvgPoss = pick.oppAvgPoss;
  if (oppPoss != null) oppPossLabel = `${oppPoss}%`;
  if (oppAvgPoss != null) oppPossLabel += ` (avg ${oppAvgPoss}%)`;

  const actual = pick.actualValue ?? pick.currentValue;

  return (
    <View style={styles.pickRow}>
      {/* Header row: date, league, result */}
      <View style={styles.pickRowHeader}>
        <View style={styles.pickRowHeaderLeft}>
          <Text style={styles.pickDate}>{formatDate(pick.createdAt || pick.settledAt)}</Text>
          {leagueName ? <Text style={styles.pickLeague} numberOfLines={1}>{leagueName}</Text> : null}
        </View>
        <View
          style={[
            styles.resultBadge,
            {
              borderColor: resultColor,
              backgroundColor: pending ? 'transparent' : resultColor + '1A',
            },
          ]}
        >
          <Text style={[styles.resultText, { color: resultColor }]}>{pending ? 'PENDING' : resultText}</Text>
        </View>
      </View>

      {/* Match context */}
      <View style={styles.matchContext}>
        <View style={[styles.venueBadge, isHome ? styles.venueHome : isAway ? styles.venueAway : styles.venueNeutral]}>
          <Text style={styles.venueText}>{venueText}</Text>
        </View>
        <Text style={styles.opponentText} numberOfLines={1}>vs {opponentName}</Text>
        {teamName ? <Text style={styles.teamText} numberOfLines={1}>for {teamName}</Text> : null}
        {score ? <Text style={styles.scoreText}>{score}</Text> : null}
      </View>

      {/* Prop line */}
      <View style={styles.propLine}>
        <Text style={styles.pickProp} numberOfLines={1}>{formatProp(pick.propType || '')}</Text>
        <Text style={styles.pickRec}>
          <Text style={{ color: recColor, fontWeight: '700' }}>{rec || '—'}</Text>
          <Text style={{ color: Colors.textSecondary }}> {pick.line}</Text>
        </Text>
      </View>

      {/* Stats row */}
      <View style={styles.statsRowCompact}>
        {actual != null && (
          <View style={styles.statPill}>
            <Text style={styles.statPillLabel}>Actual</Text>
            <Text style={styles.statPillValue}>{actual}</Text>
          </View>
        )}
        {pick.projection != null && (
          <View style={styles.statPill}>
            <Text style={styles.statPillLabel}>Proj</Text>
            <Text style={styles.statPillValue}>{pick.projection.toFixed(1)}</Text>
          </View>
        )}
        {pick.confidence != null && (
          <View style={styles.statPill}>
            <Text style={styles.statPillLabel}>Conf</Text>
            <Text style={styles.statPillValue}>{Math.round(pick.confidence)}%</Text>
          </View>
        )}
        {confidenceLevel ? (
          <View style={styles.statPill}>
            <Text style={styles.statPillLabel}>Tier</Text>
            <Text style={styles.statPillValue}>{confidenceLevel}</Text>
          </View>
        ) : null}
      </View>

      {/* Possession context */}
      {oppPossLabel ? (
        <View style={styles.possRow}>
          <Ionicons name="shield" size={12} color={Colors.textTertiary} />
          <Text style={styles.possText}>Opponent possession: {oppPossLabel}</Text>
        </View>
      ) : null}

      {/* Game script / alerts */}
      {pick.paceMismatch && pick.paceWarning ? (
        <View style={styles.alertRow}>
          <Ionicons name="warning" size={12} color={Colors.warning} />
          <Text style={styles.alertText}>{pick.paceWarning}</Text>
        </View>
      ) : null}
      {pick.sharpSummary ? (
        <Text style={styles.sharpSummary} numberOfLines={2}>{pick.sharpSummary}</Text>
      ) : null}
    </View>
  );
}

export default function PlayerProfileCard({
  visible,
  onClose,
  playerName,
  playerId,
  picks,
}: PlayerProfileCardProps) {
  const [advancedStats, setAdvancedStats] = React.useState<Awaited<ReturnType<typeof getPlayerAdvancedStats>> | null>(null);
  const [statsLoading, setStatsLoading] = React.useState(false);

  const {
    playerPicks,
    hitRate,
    hits,
    misses,
    bestProp,
    worstProp,
    recentForm,
    pastPicks,
    propBreakdown,
    overUnder,
  } = useMemo(() => {
    const nameMatches = picks.filter(
      (p) => p.playerName?.trim().toLowerCase() === playerName.trim().toLowerCase(),
    );
    // A verified player ID is authoritative. Name-only history is retained
    // for legacy rows, but never merges two identified players sharing a
    // display name (for example multiple players named "Reinaldo").
    const pPicks = playerId
      ? nameMatches.filter((p) => !p.playerId || p.playerId === playerId)
      : (() => {
          const identifiedIds = new Set(
            nameMatches
              .map((p) => p.playerId)
              .filter((id): id is number => typeof id === 'number' && id > 0),
          );
          return identifiedIds.size <= 1 ? nameMatches : nameMatches.filter((p) => !p.playerId);
        })();

    const settled = pPicks.filter(
      (p) => isHit(p.result) || isMiss(p.result) || isPush(p.result) || isDnp(p.result)
    );
    const decided = pPicks.filter((p) => isHit(p.result) || isMiss(p.result));
    const hitsCount = decided.filter((p) => isHit(p.result)).length;
    const missesCount = decided.filter((p) => isMiss(p.result)).length;
    const rate = decided.length > 0 ? Math.round((hitsCount / decided.length) * 100) : 0;

    const propStats: Record<string, { hits: number; misses: number; total: number; overHits: number; overTotal: number; underHits: number; underTotal: number }> = {};
    settled.forEach((p) => {
      const prop = p.propType || 'Unknown';
      const rec = (p.recommendation || '').toUpperCase();
      if (!propStats[prop]) propStats[prop] = { hits: 0, misses: 0, total: 0, overHits: 0, overTotal: 0, underHits: 0, underTotal: 0 };
      if (isPush(p.result) || isDnp(p.result)) return;
      propStats[prop].total++;
      if (isHit(p.result)) propStats[prop].hits++;
      if (isMiss(p.result)) propStats[prop].misses++;
      if (rec === 'OVER') {
        propStats[prop].overTotal++;
        if (isHit(p.result)) propStats[prop].overHits++;
      }
      if (rec === 'UNDER') {
        propStats[prop].underTotal++;
        if (isHit(p.result)) propStats[prop].underHits++;
      }
    });

    const propsList = Object.entries(propStats).map(([prop, stats]) => ({
      prop,
      rate: stats.total > 0 ? Math.round((stats.hits / stats.total) * 100) : 0,
      overRate: stats.overTotal > 0 ? Math.round((stats.overHits / stats.overTotal) * 100) : 0,
      underRate: stats.underTotal > 0 ? Math.round((stats.underHits / stats.underTotal) * 100) : 0,
      total: stats.total,
      overTotal: stats.overTotal,
      underTotal: stats.underTotal,
    }));

    let best = null;
    let worst = null;
    if (propsList.length > 0) {
      const reliable = propsList.filter((p) => p.total >= 3);
      const target = reliable.length > 0 ? reliable : propsList;
      best = [...target].sort((a, b) => b.rate - a.rate || b.total - a.total)[0];
      worst = [...target].sort((a, b) => a.rate - b.rate || b.total - a.total)[0];
    }

    const overUnder = {
      over: { hits: 0, total: 0 },
      under: { hits: 0, total: 0 },
    };
    settled.forEach((p) => {
      const rec = (p.recommendation || '').toUpperCase();
      if (rec === 'OVER' && !isPush(p.result) && !isDnp(p.result)) {
        overUnder.over.total++;
        if (isHit(p.result)) overUnder.over.hits++;
      }
      if (rec === 'UNDER' && !isPush(p.result) && !isDnp(p.result)) {
        overUnder.under.total++;
        if (isHit(p.result)) overUnder.under.hits++;
      }
    });

    const form = [...settled]
      .sort((a, b) => new Date(b.createdAt || b.settledAt || 0).getTime() - new Date(a.createdAt || a.settledAt || 0).getTime())
      .slice(0, 5)
      .map((p) => (isHit(p.result) ? 'W' : isMiss(p.result) ? 'L' : isPush(p.result) ? 'P' : 'D'))
      .reverse();

    const sortedPicks = [...pPicks].sort(
      (a, b) => new Date(b.createdAt || b.settledAt || 0).getTime() - new Date(a.createdAt || a.settledAt || 0).getTime()
    );

    return {
      playerPicks: pPicks,
      hitRate: rate,
      hits: hitsCount,
      misses: missesCount,
      bestProp: best,
      worstProp: worst,
      recentForm: form,
      pastPicks: sortedPicks,
      propBreakdown: propsList,
      overUnder,
    };
  }, [picks, playerName, playerId]);

  // Fetch advanced stats whenever the active player has a known ID
  React.useEffect(() => {
    let cancelled = false;
    setStatsLoading(true);
    const nameMatches = picks.filter(
      (p) => p.playerName?.trim().toLowerCase() === playerName.trim().toLowerCase(),
    );
    const resolvedPlayerId = playerId || (
      new Set(nameMatches.map((p) => p.playerId).filter((id): id is number => typeof id === 'number' && id > 0)).size === 1
        ? nameMatches.find((p) => p.playerId)?.playerId
        : undefined
    );
    if (!resolvedPlayerId) {
      setAdvancedStats(null);
      setStatsLoading(false);
      return;
    }
    getPlayerAdvancedStats(resolvedPlayerId)
      .then((stats) => {
        if (!cancelled) setAdvancedStats(stats);
      })
      .catch(() => {
        if (!cancelled) setAdvancedStats(null);
      })
      .finally(() => {
        if (!cancelled) setStatsLoading(false);
      });
    return () => { cancelled = true; };
  }, [picks, playerName, playerId]);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={close}>
      <View style={styles.modalOverlay}>
        <TouchableOpacity style={StyleSheet.absoluteFill} activeOpacity={1} onPress={close} />
        <View style={styles.modalContent}>
          <View style={styles.header}>
            <Text style={styles.playerName} numberOfLines={1}>{playerName}</Text>
            <TouchableOpacity onPress={onClose} style={styles.iconBtn}>
              <Ionicons name="close" size={24} color={Colors.textSecondary} />
            </TouchableOpacity>
          </View>

          <ScrollView
            style={styles.scrollArea}
            contentContainerStyle={styles.scrollContent}
            showsVerticalScrollIndicator={false}
          >
            {/* Stats Grid */}
            <View style={styles.statsRow}>
              <StatBox label="Total Picks" value={playerPicks.length.toString()} />
              <StatBox
                label="Hit Rate"
                value={`${hitRate}%`}
                highlightColor={hitRate >= 55 ? Colors.success : Colors.text}
                subtext={`${hits}W - ${misses}L`}
              />
            </View>

            <View style={styles.statsRow}>
              <StatBox
                label="Best Prop"
                value={bestProp ? formatProp(bestProp.prop) : 'N/A'}
                subtext={bestProp ? `${bestProp.rate}% (${bestProp.total} picks)` : ''}
                highlightColor={Colors.primary}
              />
              <StatBox
                label="Worst Prop"
                value={worstProp ? formatProp(worstProp.prop) : 'N/A'}
                subtext={worstProp ? `${worstProp.rate}% (${worstProp.total} picks)` : ''}
                highlightColor={Colors.error}
              />
            </View>

            {/* OVER/UNDER split */}
            <View style={styles.ouCard}>
              <Text style={styles.sectionTitle}>Over / Under</Text>
              <View style={styles.ouRow}>
                <View style={styles.ouBlock}>
                  <Text style={styles.ouValue}>
                    {overUnder.over.total > 0 ? Math.round((overUnder.over.hits / overUnder.over.total) * 100) : 0}%
                  </Text>
                  <Text style={styles.ouLabel}>Over {overUnder.over.hits}/{overUnder.over.total}</Text>
                </View>
                <View style={styles.ouDivider} />
                <View style={styles.ouBlock}>
                  <Text style={styles.ouValue}>
                    {overUnder.under.total > 0 ? Math.round((overUnder.under.hits / overUnder.under.total) * 100) : 0}%
                  </Text>
                  <Text style={styles.ouLabel}>Under {overUnder.under.hits}/{overUnder.under.total}</Text>
                </View>
              </View>
            </View>

            {/* Prop breakdown */}
            {propBreakdown.length > 0 && (
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>By Prop</Text>
                {propBreakdown.map((pb) => (
                  <View key={pb.prop} style={styles.propBreakdownRow}>
                    <Text style={styles.propBreakdownName} numberOfLines={1}>{formatProp(pb.prop)}</Text>
                    <View style={styles.propBreakdownBars}>
                      {pb.overTotal > 0 && (
                        <View style={styles.propBreakdownBadge}>
                          <Text style={styles.propBreakdownBadgeText}>O {pb.overRate}%</Text>
                        </View>
                      )}
                      {pb.underTotal > 0 && (
                        <View style={styles.propBreakdownBadge}>
                          <Text style={styles.propBreakdownBadgeText}>U {pb.underRate}%</Text>
                        </View>
                      )}
                      <Text style={styles.propBreakdownTotal}>{pb.total} picks</Text>
                    </View>
                  </View>
                ))}
              </View>
            )}

            {/* Recent Form */}
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Recent Form</Text>
              {recentForm.length > 0 ? (
                <View style={styles.formContainer}>
                  {recentForm.map((res, i) => (
                    <View
                      key={i}
                      style={[
                        styles.formBadge,
                        res === 'W' ? styles.formW : res === 'L' ? styles.formL : res === 'P' ? styles.formP : styles.formD,
                      ]}
                    >
                      <Text style={styles.formBadgeText}>{res}</Text>
                    </View>
                  ))}
                </View>
              ) : (
                <Text style={styles.emptyStateText}>No recent settled picks</Text>
              )}
            </View>

            {/* Advanced Stats */}
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Per-90 Advanced Stats</Text>
              {statsLoading ? (
                <ActivityIndicator color={Colors.primary} style={{ marginVertical: 16 }} />
              ) : advancedStats ? (
                <View style={styles.advStatsGrid}>
                  <View style={styles.advStatBox}>
                    <Text style={styles.advStatValue}>{advancedStats.xG.toFixed(2)}</Text>
                    <Text style={styles.advStatLabel}>xG</Text>
                  </View>
                  <View style={styles.advStatBox}>
                    <Text style={styles.advStatValue}>{advancedStats.xA.toFixed(2)}</Text>
                    <Text style={styles.advStatLabel}>xA</Text>
                  </View>
                  <View style={styles.advStatBox}>
                    <Text style={styles.advStatValue}>{advancedStats.shots.toFixed(1)}</Text>
                    <Text style={styles.advStatLabel}>Shots</Text>
                  </View>
                  <View style={styles.advStatBox}>
                    <Text style={styles.advStatValue}>{advancedStats.shotsOnTarget.toFixed(1)}</Text>
                    <Text style={styles.advStatLabel}>SOT</Text>
                  </View>
                  <View style={styles.advStatBox}>
                    <Text style={styles.advStatValue}>{advancedStats.keyPasses.toFixed(1)}</Text>
                    <Text style={styles.advStatLabel}>Key Pass</Text>
                  </View>
                  <View style={styles.advStatBox}>
                    <Text style={styles.advStatValue}>{advancedStats.passes.toFixed(1)}</Text>
                    <Text style={styles.advStatLabel}>Passes</Text>
                  </View>
                  <View style={styles.advStatBox}>
                    <Text style={styles.advStatValue}>{advancedStats.tackles.toFixed(1)}</Text>
                    <Text style={styles.advStatLabel}>Tackles</Text>
                  </View>
                  <View style={styles.advStatBox}>
                    <Text style={styles.advStatValue}>{advancedStats.minutesPerGame}</Text>
                    <Text style={styles.advStatLabel}>Min/Gm</Text>
                  </View>
                </View>
              ) : (
                <Text style={styles.emptyStateText}>No advanced stats available.</Text>
              )}
            </View>

            {/* Past Picks */}
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Past Picks</Text>
              {pastPicks.length > 0 ? (
                pastPicks.map((p, idx) => <PickRow pick={p} key={p.id || p._id || p.pickId || idx.toString()} index={idx} />)
              ) : (
                <View style={styles.emptyState}>
                  <Text style={styles.emptyStateText}>
                    No past picks found for this player.
                  </Text>
                </View>
              )}
            </View>
          </ScrollView>
        </View>
      </View>

    </Modal>
  );
}

const styles = StyleSheet.create({
  modalOverlay: {
    flex: 1,
    backgroundColor: Colors.overlay,
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: Colors.cardSecondary,
    borderTopLeftRadius: Colors.radiusLg,
    borderTopRightRadius: Colors.radiusLg,
    maxHeight: '90%',
    paddingBottom: 40,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
  },
  iconBtn: {
    padding: 6,
    backgroundColor: Colors.card,
    borderRadius: 20,
  },
  playerName: {
    fontSize: 22,
    fontWeight: '700',
    color: Colors.text,
    flex: 1,
    marginRight: 12,
  },
  closeBtn: {
    padding: 4,
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusFull,
  },
  scrollArea: {
    flexShrink: 1,
  },
  scrollContent: {
    padding: 20,
    gap: 20,
  },
  statsRow: {
    flexDirection: 'row',
    gap: 12,
  },
  statBox: {
    flex: 1,
    backgroundColor: Colors.card,
    padding: 16,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  statLabel: {
    fontSize: 12,
    color: Colors.textSecondary,
    marginBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    fontWeight: '600',
  },
  statValue: {
    fontSize: 20,
    fontWeight: '700',
    color: Colors.text,
  },
  statSubtext: {
    fontSize: 13,
    color: Colors.textTertiary,
    marginTop: 4,
  },
  ouCard: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    padding: 16,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  ouRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  ouBlock: {
    flex: 1,
    alignItems: 'center',
  },
  ouValue: {
    fontSize: 22,
    fontWeight: '800',
    color: Colors.text,
  },
  ouLabel: {
    fontSize: 12,
    color: Colors.textSecondary,
    marginTop: 4,
  },
  ouDivider: {
    width: 1,
    height: 40,
    backgroundColor: Colors.borderSubtle,
  },
  section: {
    marginTop: 8,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: Colors.text,
    marginBottom: 12,
  },
  propBreakdownRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    padding: 12,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  propBreakdownName: {
    fontSize: 14,
    color: Colors.text,
    fontWeight: '600',
    flex: 1,
    marginRight: 8,
  },
  propBreakdownBars: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  propBreakdownBadge: {
    backgroundColor: Colors.cardSecondary,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  propBreakdownBadgeText: {
    fontSize: 12,
    fontWeight: '700',
    color: Colors.text,
  },
  propBreakdownTotal: {
    fontSize: 12,
    color: Colors.textTertiary,
  },
  formContainer: {
    flexDirection: 'row',
    gap: 8,
  },
  formBadge: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  formW: {
    backgroundColor: Colors.successDim,
    borderWidth: 1,
    borderColor: Colors.success,
  },
  formL: {
    backgroundColor: Colors.errorDim,
    borderWidth: 1,
    borderColor: Colors.error,
  },
  formP: {
    backgroundColor: Colors.pushDim,
    borderWidth: 1,
    borderColor: Colors.push,
  },
  formD: {
    backgroundColor: Colors.dnpDim,
    borderWidth: 1,
    borderColor: Colors.dnp,
  },
  formBadgeText: {
    fontSize: 14,
    fontWeight: '700',
    color: Colors.text,
  },
  pickRow: {
    backgroundColor: Colors.card,
    padding: 16,
    borderRadius: Colors.radius,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    gap: 10,
  },
  pickRowHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
  },
  pickRowHeaderLeft: {
    flex: 1,
  },
  pickDate: {
    fontSize: 12,
    color: Colors.textTertiary,
  },
  pickLeague: {
    fontSize: 12,
    color: Colors.primary,
    fontWeight: '600',
    marginTop: 2,
  },
  resultBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    borderWidth: 1,
    minWidth: 40,
    alignItems: 'center',
  },
  resultText: {
    fontSize: 12,
    fontWeight: '800',
  },
  matchContext: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap',
  },
  venueBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 4,
  },
  venueHome: {
    backgroundColor: Colors.primaryDim,
  },
  venueAway: {
    backgroundColor: Colors.errorDim,
  },
  venueNeutral: {
    backgroundColor: Colors.cardSecondary,
  },
  venueText: {
    fontSize: 11,
    fontWeight: '800',
    color: Colors.text,
  },
  opponentText: {
    fontSize: 14,
    color: Colors.text,
    fontWeight: '600',
  },
  teamText: {
    fontSize: 13,
    color: Colors.textSecondary,
  },
  scoreText: {
    fontSize: 13,
    color: Colors.textSecondary,
    fontWeight: '600',
  },
  propLine: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  pickProp: {
    fontSize: 15,
    fontWeight: '600',
    color: Colors.text,
  },
  pickRec: {
    fontSize: 14,
    color: Colors.textSecondary,
    fontWeight: '500',
  },
  statsRowCompact: {
    flexDirection: 'row',
    gap: 8,
    flexWrap: 'wrap',
  },
  statPill: {
    backgroundColor: Colors.cardSecondary,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  statPillLabel: {
    fontSize: 11,
    color: Colors.textSecondary,
    fontWeight: '600',
  },
  statPillValue: {
    fontSize: 13,
    color: Colors.text,
    fontWeight: '700',
  },
  possRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  possText: {
    fontSize: 12,
    color: Colors.textSecondary,
  },
  alertRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: Colors.warningDim,
    borderRadius: 8,
    padding: 8,
  },
  alertText: {
    fontSize: 12,
    color: Colors.warning,
    fontWeight: '600',
  },
  sharpSummary: {
    fontSize: 12,
    color: Colors.textSecondary,
    lineHeight: 18,
  },
  emptyState: {
    padding: 20,
    alignItems: 'center',
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  emptyStateText: {
    color: Colors.textSecondary,
    fontSize: 14,
  },
  advStatsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  advStatBox: {
    width: '22%',
    minWidth: 70,
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    padding: 12,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  advStatValue: {
    fontSize: 16,
    fontWeight: '800',
    color: Colors.text,
  },
  advStatLabel: {
    fontSize: 11,
    color: Colors.textSecondary,
    marginTop: 4,
    fontWeight: '600',
  },
});
