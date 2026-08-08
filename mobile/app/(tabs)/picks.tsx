import React, { useState, useCallback, useRef, useMemo } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity,
  ActivityIndicator, Alert, Platform, RefreshControl,
  Modal, ScrollView, Pressable,
  TextInput,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import Reanimated, {
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
  FadeInDown,
} from 'react-native-reanimated';
import SwipeablePickRow from '@/components/SwipeablePickRow';
import OwnerPickCard from '@/components/OwnerPickCard';
import { router } from 'expo-router';
import Colors from '@/constants/colors';
import NotificationBell from '@/components/NotificationBell';
import AnalyticsDashboard from '@/components/AnalyticsDashboard';
import LiveMatchTracker from '@/components/LiveMatchTracker';
import StreaksAchievements from '@/components/StreaksAchievements';
import PicksCalendar from '@/components/PicksCalendar';
import SocialFeed from '@/components/SocialFeed';
import CustomAlerts from '@/components/CustomAlerts';
import { listPicks, deletePick, sharePickToCommunity, autoPostPickToCommunity, fetchPickAnalysis, Pick, AnalysisFactor } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { renderTacticalIntelligence } from '@/components/AnalysisCards';

type Tab = 'live' | 'history';

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Attempts', shots: 'Shots', shots_on_target: 'SOT',
  goals: 'Goals', assists: 'Assists', key_passes: 'Key Passes',
  tackles: 'Tackles', saves: 'Saves', dribbles: 'Dribbles', crosses: 'Crosses',
  interceptions: 'Interceptions', blocks: 'Blocks', fouls_drawn: 'Fouls Drawn',
  fouls_committed: 'Fouls', clearances: 'Clearances', duels_won: 'Duels Won',
  yellow_cards: 'Yellow Cards', shots_assisted: 'Shot Assists', passes: 'Passes',
};

const LEAGUE_LABELS: Record<number, string> = {
  39: 'Premier League', 40: 'Championship', 140: 'La Liga', 141: 'La Liga 2',
  135: 'Serie A', 136: 'Serie B', 78: 'Bundesliga', 79: 'Bundesliga 2',
  61: 'Ligue 1', 62: 'Ligue 2', 71: 'Brasileirão', 128: 'Liga Profesional',
  253: 'MLS', 262: 'Liga MX', 254: 'NWSL', 2: 'Champions League', 3: 'Europa League',
  848: 'NWSL', 1: 'World Cup', 5: 'Nations League', 307: 'Saudi Pro',
  88: 'Eredivisie', 94: 'Primeira Liga', 144: 'Belgian Pro', 203: 'Süper Lig',
};

function getLeagueLabel(id?: number | null) {
  if (!id) return null;
  return LEAGUE_LABELS[id] || `League ${id}`;
}

function normalizedResult(p: Pick) {
  return String(p.result || '').toLowerCase();
}
function derivedOutcome(p: Pick): 'hit' | 'miss' | 'push' | 'dnp' | null {
  const result = normalizedResult(p);
  const hasVerifiedSource = p.settlementSource?.verified === true;
  const isExplicitFinal = p.status === 'settled' || p.matchStatus === 'final';
  const lineNumber = Number(p.line);
  const legacyVoid = result === 'push' && (
    Boolean(p.voidReason)
    || (p.actualValue == null && Number.isFinite(lineNumber) && !Number.isInteger(lineNumber))
  );
  if ((hasVerifiedSource || isExplicitFinal) && (result === 'dnp' || legacyVoid)) return 'dnp';
  if ((hasVerifiedSource || isExplicitFinal) && (result === 'hit' || result === 'won')) return 'hit';
  if ((hasVerifiedSource || isExplicitFinal) && (result === 'miss' || result === 'lost')) return 'miss';
  if ((hasVerifiedSource || isExplicitFinal) && result === 'push') return 'push';
  if ((hasVerifiedSource || isExplicitFinal) && result === 'dnp') return 'dnp';
  if ((hasVerifiedSource || isExplicitFinal) && result === 'pass') {
    // A verified final value is always an actionable settlement outcome.
    // Older rows may still carry result=pass; use their stored avoided
    // direction/outcome so history renders them like every other pick.
    if (p.passOutcome === 'hit') return 'hit';
    if (p.passOutcome === 'miss') return 'miss';
    if (p.passOutcome === 'push') return 'push';
  }
  if ((p.status === 'settled' || p.matchStatus === 'final') && p.actualValue != null) {
    const line = Number(p.line);
    const actual = Number(p.actualValue);
    const rec = String(p.recommendation || '').toLowerCase();
    if (Number.isFinite(line) && Number.isFinite(actual) && (rec === 'over' || rec === 'under')) {
      if (Number.isInteger(line) && actual === line) return 'push';
      return (rec === 'over' ? actual > line : actual < line) ? 'hit' : 'miss';
    }
  }
  return null;
}
function isLive(p: Pick) {
  return p.matchStatus === 'live' || p.status === 'live' || p.status === 'pending'
    || (!p.status && derivedOutcome(p) == null);
}
function isSettled(p: Pick) {
  return derivedOutcome(p) != null;
}
function isPendingReview(p: Pick) {
  const result = normalizedResult(p);
  const hasFinalOutcome = ['hit', 'miss', 'push', 'dnp'].includes(result)
    && p.actualValue != null
    && (
      p.settlementSource?.verified === true
      || p.settlementSource?.verificationMethod === 'legacy_numeric_reconciliation'
      || p.status === 'settled'
    );
  if (hasFinalOutcome) return false;
  return p.status === 'pending_review'
    || result === 'pending_review';
}
function isHistoryVisible(p: Pick) {
  return isSettled(p) || isPendingReview(p)
    || (p.status === 'settled' && p.actualValue != null);
}
function pickWon(p: Pick) {
  return derivedOutcome(p) === 'hit' || p.status === 'won';
}
function pickLost(p: Pick) {
  return derivedOutcome(p) === 'miss' || p.status === 'lost';
}
function pickPush(p: Pick) {
  return derivedOutcome(p) === 'push';
}
function pickDnp(p: Pick) {
  return derivedOutcome(p) === 'dnp';
}
function getRecDir(p: Pick): 'OVER' | 'UNDER' | null {
  const rec = p.recommendation;
  if (rec === 'OVER' || rec === 'UNDER') return rec;
  const passLean = String(p.passLeaning || '').toUpperCase();
  if (passLean === 'OVER' || passLean === 'UNDER') return passLean;
  const proj = p.projection ?? p.projectedValue;
  if (proj != null && p.line > 0) {
    return proj > p.line ? 'OVER' : 'UNDER';
  }
  return null;
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
  const created = p.createdAt || '';
  return `${base.join('|')}|${created.slice(0, 16)}`;
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


function PulsingDot() {
  const opacity = useSharedValue(1);
  React.useEffect(() => {
    opacity.value = withRepeat(
      withSequence(withTiming(0.2, { duration: 700 }), withTiming(1, { duration: 700 })),
      -1, false
    );
  }, []);
  const style = useAnimatedStyle(() => ({ opacity: opacity.value }));
  return <Reanimated.View style={[{ width: 5, height: 5, borderRadius: 2.5, backgroundColor: Colors.primary }, style]} />;
}


function RecordBar({ picks }: { picks: Pick[] }) {
  const [expanded, setExpanded] = useState(false);
  const uniquePicks = useMemo(() => uniquePickEvents(picks), [picks]);
  const hits = uniquePicks.filter(pickWon).length;
  const misses = uniquePicks.filter(pickLost).length;
  const dnps = uniquePicks.filter(pickDnp).length;
  const pending = uniquePicks.filter(isLive).length;
  const review = uniquePicks.filter(isPendingReview).length;
  const settled = hits + misses;
  const winPct = settled > 0 ? Math.round((hits / settled) * 100) : null;

  let streak = 0;
  const sorted = [...uniquePicks].sort((a, b) =>
    new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime()
  );
  for (const p of sorted) {
    if (pickWon(p)) streak++;
    else if (pickLost(p) || pickDnp(p)) break;
  }

  return (
    <TouchableOpacity
      activeOpacity={0.8}
      onPress={() => setExpanded((value) => !value)}
      style={[styles.recordBar, expanded && styles.recordBarExpanded]}
      accessibilityRole="button"
      accessibilityLabel={expanded ? 'Hide record details' : 'Show record details'}
    >
      <View style={styles.recordCompactRow}>
        <View style={styles.recordCompactTitle}>
          <Text style={styles.recordLabel}>YOUR RECORD</Text>
          <Text style={styles.recordCompactSummary}>
            <Text style={{ color: Colors.success }}>{hits}W</Text>
            <Text style={styles.recordSummaryMuted}> · </Text>
            <Text style={{ color: Colors.error }}>{misses}L</Text>
            <Text style={styles.recordSummaryMuted}> · </Text>
            <Text style={{ color: Colors.primary }}>{winPct != null ? `${winPct}%` : '—'}</Text>
          </Text>
        </View>
        <Ionicons
          name={expanded ? 'chevron-up' : 'chevron-down'}
          size={16}
          color={Colors.textTertiary}
        />
      </View>

      {expanded && (
        <View style={styles.recordDetails}>
          <View style={styles.recordStats}>
            <View style={styles.recordStat}>
              <Text style={[styles.recordVal, { color: Colors.success }]}>{hits}</Text>
              <Text style={styles.recordKey}>HITS</Text>
            </View>
            <View style={styles.recordStat}>
              <Text style={[styles.recordVal, { color: Colors.error }]}>{misses}</Text>
              <Text style={styles.recordKey}>MISS</Text>
            </View>
            <View style={styles.recordStat}>
              <Text style={[styles.recordVal, { color: Colors.textSecondary }]}>{pending}</Text>
              <Text style={styles.recordKey}>LIVE</Text>
            </View>
            <View style={styles.recordStat}>
              <Text style={[styles.recordVal, { color: '#FFD60A' }]}>{review}</Text>
              <Text style={styles.recordKey}>REVIEW</Text>
            </View>
            <View style={styles.recordStat}>
              <Text style={[styles.recordVal, { color: Colors.dnp }]}>{dnps}</Text>
              <Text style={styles.recordKey}>DNP</Text>
            </View>
            <View style={styles.recordStat}>
              <Text style={[styles.recordVal, { color: Colors.primary }]}>
                {winPct != null ? `${winPct}%` : '—'}
              </Text>
              <Text style={styles.recordKey}>WIN%</Text>
            </View>
            <View style={styles.recordStat}>
              <Text style={[styles.recordVal, { color: Colors.accent }]}>
                {streak > 0 ? `${streak}W` : '—'}
              </Text>
              <Text style={styles.recordKey}>STRK</Text>
            </View>
          </View>
          <View style={styles.progressWrap}>
            <View style={styles.progressTrack}>
              <View
                style={[
                  styles.progressFill,
                  { width: `${picks.length > 0 ? Math.max(8, Math.min(100, (settled / picks.length) * 100)) : 0}%` },
                ]}
              />
            </View>
            <Text style={styles.progressText}>
              {settled}/{uniquePicks.length} unique events settled · {picks.length} saved rows
            </Text>
          </View>
        </View>
      )}
    </TouchableOpacity>
  );
}

function renderAnalysisBlocks(text: string, rec: string) {
  const isOver = rec === 'OVER';
  const isUnder = rec === 'UNDER';
  const recColor = isOver ? Colors.success : isUnder ? Colors.error : Colors.textSecondary;
  const paragraphs = text.split(/\n\n+/).filter(p => p.trim());
  const blocks: React.ReactElement[] = [];
  for (let i = 0; i < paragraphs.length; i++) {
    const para = paragraphs[i];
    const m = para.match(/^\*\*([^*]+)\*\*\s*([\s\S]*)/);
    if (m) {
      const section = m[1].trim();
      const body = m[2].trim().replace(/\*\*/g, '');
      if (section === 'Analysis') continue;
      if (section === 'Verdict') {
        blocks.push(
          <View key={i} style={mStyles.aiVerdictBlock}>
            <View style={[mStyles.aiVerdictPill, { backgroundColor: isOver ? 'rgba(57,255,20,0.12)' : 'rgba(255,59,48,0.12)' }]}>
              <Text style={[mStyles.aiVerdictLabel, { color: recColor }]}>VERDICT</Text>
            </View>
            <Text style={mStyles.aiVerdictText}>{body}</Text>
          </View>
        );
        continue;
      }
      if (section === 'TL;DR') {
        blocks.push(
          <View key={i} style={mStyles.aiTldrBlock}>
            <Text style={mStyles.aiTldrText}>{body}</Text>
          </View>
        );
        continue;
      }
      blocks.push(
        <View key={i} style={mStyles.aiSection}>
          <Text style={mStyles.aiSectionTitle}>{section.toUpperCase()}</Text>
          {body ? <Text style={mStyles.aiSectionBody}>{body}</Text> : null}
        </View>
      );
    } else {
      const plain = para.replace(/\*\*/g, '').trim();
      if (plain) blocks.push(<Text key={i} style={mStyles.aiSectionBody}>{plain}</Text>);
    }
  }
  return blocks;
}

function renderModelFactors(factors: AnalysisFactor[]) {
  const statusColor: Record<string, string> = {
    applied: Colors.primary,
    measured: '#60A5FA',
    warning: '#F59E0B',
    unavailable: Colors.textTertiary,
  };
  const statusLabel: Record<string, string> = {
    applied: 'APPLIED',
    measured: 'MEASURED',
    warning: 'CAUTION',
    unavailable: 'UNAVAILABLE',
  };
  const formatValue = (factor: AnalysisFactor) => {
    const value = factor.value as any;
    if (!value || typeof value !== 'object') return null;
    if (factor.id === 'possession_range' && Array.isArray(value.range)) {
      return `${value.expected != null ? Number(value.expected).toFixed(1) : '—'}% expected · ${value.range[0]}–${value.range[1]}% range`;
    }
    if (factor.id === 'historical_depth') {
      return `${value.avg != null ? Number(value.avg).toFixed(1) : '—'} avg · ${value.seasonsSearched ?? '—'} seasons searched`;
    }
    if (factor.id === 'opponent_history') {
      return `${value.h2hAverage != null ? Number(value.h2hAverage).toFixed(1) : '—'} H2H avg · ${value.comparableGames ?? 0} comparable`;
    }
    if (factor.id === 'team_pass_volume') {
      return value.average != null ? `${Number(value.average).toFixed(1)} team passes/game` : null;
    }
    if (factor.id === 'player_share') {
      return value.averagePct != null ? `${Number(value.averagePct).toFixed(1)}% team-pass share` : null;
    }
    if (factor.id === 'evidence_quality') {
      return `${value.score ?? '—'}/100 · ${value.appliedGroups ?? 0} applied groups`;
    }
    if (factor.id === 'availability_role') {
      return [value.lineupStatus, value.position, value.role].filter(Boolean).join(' · ') || null;
    }
    if (factor.id === 'competition_context') {
      return [value.leagueId ? `League ${value.leagueId}` : null, value.venue, value.opponentTier ? `${value.opponentTier} opponent` : null]
        .filter(Boolean).join(' · ') || null;
    }
    if (factor.id === 'tactical_similarity') {
      return value.sampleSize != null ? `${value.sampleSize} comparable games${value.position ? ` · ${value.position}` : ''}` : null;
    }
    return null;
  };

  return (
    <View style={mStyles.factorSection}>
      <View style={mStyles.factorSectionHeader}>
        <View style={{ flex: 1 }}>
          <Text style={mStyles.factorSectionTitle}>MODEL FACTORS</Text>
          <Text style={mStyles.factorSectionSubtitle}>What the final projection had — and what it did not</Text>
        </View>
        <Ionicons name="flask-outline" size={16} color={Colors.primary} />
      </View>
      <View style={mStyles.factorGrid}>
        {factors.map((factor) => {
          const color = statusColor[factor.status] || Colors.textSecondary;
          const valueText = formatValue(factor);
          const impact = factor.impact === 'projection'
            ? 'PROJECTION'
            : factor.impact === 'confidence' ? 'CONFIDENCE' : 'CONTEXT';
          return (
            <View key={factor.id} style={[mStyles.factorCard, { borderLeftColor: color }]}>
              <View style={mStyles.factorCardTop}>
                <Text style={mStyles.factorTitle}>{factor.title}</Text>
                <Text style={[mStyles.factorStatus, { color }]}>{statusLabel[factor.status] || factor.status.toUpperCase()}</Text>
              </View>
              <Text style={mStyles.factorSummary}>{factor.summary}</Text>
              {valueText ? <Text style={mStyles.factorValue}>{valueText}</Text> : null}
              <View style={mStyles.factorMeta}>
                <Text style={mStyles.factorImpact}>{impact}</Text>
                {factor.sampleSize != null ? <Text style={mStyles.factorSample}>n={factor.sampleSize}</Text> : null}
                {factor.direction && factor.direction !== 'neutral' ? (
                  <Ionicons
                    name={factor.direction === 'up' ? 'arrow-up' : 'arrow-down'}
                    size={11}
                    color={factor.direction === 'up' ? Colors.success : Colors.error}
                  />
                ) : null}
              </View>
              {factor.detail ? <Text style={mStyles.factorDetail}>{factor.detail}</Text> : null}
            </View>
          );
        })}
      </View>
    </View>
  );
}

function renderEvidenceSummary(data: Record<string, unknown> | null) {
  if (!data) return null;
  const snapshot = (data as any)?.modelInputSnapshot?.sampleCounts ?? {};
  const factors: AnalysisFactor[] = (data as any)?.analysisFactors ?? [];
  const h2h = (data as any)?.h2hPlayerStats ?? {};
  const evFactor = factors.find((f) => f.id === 'evidence_quality');
  const level = (evFactor?.value as any)?.level ?? 'unknown';
  const score = (evFactor?.value as any)?.score;
  const applied = (evFactor?.value as any)?.appliedGroups ?? 0;
  const levelColor = level === 'high' ? Colors.success : level === 'medium' ? '#F59E0B' : Colors.textSecondary;
  const playerLogs = snapshot.playerLogs ?? 0;
  const opp = (snapshot.h2hPlayerGames ?? 0) + (snapshot.comparableGames ?? 0);
  const possObs = snapshot.possessionObservations ?? 0;
  const h2hApps = h2h.sampleSize ?? 0;
  if (playerLogs === 0 && opp === 0 && possObs === 0 && score == null) return null;
  return (
    <View style={mStyles.evidenceRow}>
      {score != null && (
        <View style={[mStyles.evidenceCell, { borderColor: levelColor + '66' }]}>
          <Text style={[mStyles.evidenceCellValue, { color: levelColor }]}>{score}</Text>
          <Text style={mStyles.evidenceCellLabel}>EV SCORE</Text>
        </View>
      )}
      {playerLogs > 0 && (
        <View style={mStyles.evidenceCell}>
          <Text style={mStyles.evidenceCellValue}>{playerLogs}</Text>
          <Text style={mStyles.evidenceCellLabel}>GAME LOGS</Text>
        </View>
      )}
      {h2hApps > 0 && (
        <View style={mStyles.evidenceCell}>
          <Text style={mStyles.evidenceCellValue}>{h2hApps}</Text>
          <Text style={mStyles.evidenceCellLabel}>H2H APPS</Text>
        </View>
      )}
      {opp > 0 && (
        <View style={mStyles.evidenceCell}>
          <Text style={mStyles.evidenceCellValue}>{opp}</Text>
          <Text style={mStyles.evidenceCellLabel}>COMP GAMES</Text>
        </View>
      )}
      {possObs > 0 && (
        <View style={mStyles.evidenceCell}>
          <Text style={mStyles.evidenceCellValue}>{possObs}</Text>
          <Text style={mStyles.evidenceCellLabel}>POSS OBS</Text>
        </View>
      )}
      {applied > 0 && (
        <View style={mStyles.evidenceCell}>
          <Text style={[mStyles.evidenceCellValue, { color: Colors.primary }]}>{applied}/9</Text>
          <Text style={mStyles.evidenceCellLabel}>APPLIED</Text>
        </View>
      )}
    </View>
  );
}

function renderRecentGameData(data: Record<string, unknown> | null, pick: Pick | null) {
  if (!data) return null;
  const playerLogs = (data as any)?.playerGameLogs;
  const games = Array.isArray(playerLogs?.games)
    ? playerLogs.games
    : Array.isArray((data as any)?.recentSamples)
      ? (data as any).recentSamples
      : [];
  const propLabel = PROP_LABELS[pick?.propType ?? ''] ?? (pick?.propType ?? 'STAT').replace(/_/g, ' ');
  const line = Number(pick?.line);
  const direction = String(pick?.recommendation ?? '').toUpperCase();
  const h2h = (data as any)?.h2hPlayerStats;
  const h2hMatches: any[] = Array.isArray(h2h?.matches) ? h2h.matches.slice(0, 5) : [];
  if (games.length === 0 && h2hMatches.length === 0) return null;
  const getValue = (game: any) => {
    const value = game?.targetStat ?? game?.value ?? game?.statValue ?? game?.stat;
    return value == null || value === '' ? null : Number(value);
  };
  const isHit = (value: number | null) => {
    if (value == null || !Number.isFinite(line) || !line) return null;
    return direction === 'UNDER' ? value < line : value > line;
  };

  return (
    <View style={mStyles.proCard}>
      <View style={mStyles.proCardHeader}>
        <View style={[mStyles.proCardPill, { backgroundColor: Colors.primary + '18' }]}>
          <Text style={[mStyles.proCardPillText, { color: Colors.primary }]}>RECENT GAMES</Text>
        </View>
        <Text style={mStyles.proCardTitle}>
          {games.length} {propLabel.toLowerCase()} observations
        </Text>
      </View>
      {games.length > 0 && <View style={{ marginTop: 8 }}>
        {games.slice(0, 8).map((game: any, index: number) => {
          const value = getValue(game);
          const hit = isHit(value);
          const valueColor = hit == null
            ? Colors.text
            : hit ? Colors.success : Colors.error;
          const date = String(game?.date ?? game?.gameDate ?? '').slice(0, 10) || '—';
          const opponent = String(game?.opponent ?? game?.opponentName ?? 'Unknown opponent');
          const venue = String(game?.venue ?? '').toUpperCase();
          const minutes = game?.minutes ?? game?.minutesPlayed;
          return (
            <View
              key={`${date}-${opponent}-${index}`}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                paddingVertical: 8,
                borderTopWidth: index === 0 ? 0 : 1,
                borderTopColor: Colors.borderSubtle,
              }}
            >
              <Text style={{ width: 76, fontSize: 10, color: Colors.textTertiary }}>{date}</Text>
              <Text style={{ flex: 1, fontSize: 11, color: Colors.textSecondary }} numberOfLines={1}>
                {venue ? `${venue} · ` : ''}{opponent}
              </Text>
              <Text style={{ width: 48, textAlign: 'right', fontSize: 10, color: Colors.textTertiary }}>
                {minutes != null ? `${minutes}m` : '—'}
              </Text>
              <Text style={{ width: 48, textAlign: 'right', fontSize: 14, fontWeight: '800', color: valueColor }}>
                {value != null && Number.isFinite(value) ? value : '—'}
              </Text>
            </View>
          );
        })}
      </View>}
      {h2hMatches.length > 0 && (
        <View style={mStyles.inlineH2H}>
          <View style={mStyles.inlineH2HHeader}>
            <View style={mStyles.inlineH2HTitleRow}>
              <Ionicons name="swap-horizontal-outline" size={11} color={Colors.primary} />
              <Text style={mStyles.inlineH2HTitle}>
                H2H · VS {(pick?.opponentName ?? h2h?.opponentName ?? 'OPPONENT').toUpperCase()}
              </Text>
            </View>
            <Text style={mStyles.inlineH2HMeta}>
              {h2h?.sampleSize ?? h2hMatches.length} APP{(h2h?.sampleSize ?? h2hMatches.length) !== 1 ? 'S' : ''}
              {h2h?.avgVsOpponent != null ? ` · AVG ${Number(h2h.avgVsOpponent).toFixed(1)}` : ''}
            </Text>
          </View>
          <View style={mStyles.inlineH2HRows}>
            {h2hMatches.map((match: any, index: number) => {
              const value = match?.targetStat ?? match?.value;
              const over = value != null && Number.isFinite(line) && value > line;
              return (
                <View key={`${match?.date ?? 'h2h'}-${index}`} style={mStyles.inlineH2HRow}>
                  <Text style={mStyles.inlineH2HDate}>{String(match?.date ?? '').slice(0, 10) || '—'}</Text>
                  <Text style={mStyles.inlineH2HVenue}>{String(match?.venue ?? '?').slice(0, 1).toUpperCase()}</Text>
                  <Text style={mStyles.inlineH2HOpp} numberOfLines={1}>{match?.opponent ?? pick?.opponentName ?? '?'}</Text>
                  {match?.teamPossession != null && (
                    <Text style={mStyles.inlineH2HPoss}>{match.teamPossession}%</Text>
                  )}
                  <Text style={[mStyles.inlineH2HStat, {
                    color: value == null ? Colors.textTertiary : over ? Colors.success : Colors.error,
                  }]}>
                    {value != null ? value : '—'}
                  </Text>
                </View>
              );
            })}
          </View>
          {(h2h?.matches?.length ?? 0) > h2hMatches.length && (
            <Text style={mStyles.inlineH2HMore}>
              Showing {h2hMatches.length} of {h2h.matches.length} meetings
            </Text>
          )}
        </View>
      )}
      <Text style={mStyles.proCardNote}>
        {propLabel} values from the player’s recent completed games. Green/red indicates the saved pick direction against the line.
      </Text>
    </View>
  );
}

function renderOpponentDefProfile(data: Record<string, unknown> | null, pick: any) {
  if (!data) return null;
  const prof = (data as any)?.opponentDefensiveProfile;
  if (!prof || (prof.sampleSize ?? 0) < 2) return null;
  const delta = prof.vsPlayerSeasonAvg as number | null;
  const favorable = prof.isFavorable as boolean | null;
  const accentColor = favorable == null ? Colors.textSecondary : favorable ? Colors.success : Colors.error;
  const arrow = delta == null ? '–' : delta > 0 ? `+${delta.toFixed(1)}%` : `${delta.toFixed(1)}%`;
  const prop = PROP_LABELS[prof.propType ?? ''] ?? (prof.propType ?? '').replace(/_/g, ' ');
  return (
    <View style={mStyles.proCard}>
      <View style={mStyles.proCardHeader}>
        <View style={[mStyles.proCardPill, { backgroundColor: accentColor + '18' }]}>
          <Text style={[mStyles.proCardPillText, { color: accentColor }]}>OPP PROFILE</Text>
        </View>
        <Text style={mStyles.proCardTitle} numberOfLines={1}>{prof.opponent}</Text>
      </View>
      <View style={mStyles.proCardMetrics}>
        <View style={mStyles.proCardMetric}>
          <Text style={[mStyles.proCardMetricValue, { color: accentColor }]}>{Number(prof.avgAllowed).toFixed(1)}</Text>
          <Text style={mStyles.proCardMetricLabel}>{prop.toUpperCase()} ALLOWED</Text>
        </View>
        {prof.playerSeasonAvg != null && (
          <View style={mStyles.proCardMetric}>
            <Text style={mStyles.proCardMetricValue}>{Number(prof.playerSeasonAvg).toFixed(1)}</Text>
            <Text style={mStyles.proCardMetricLabel}>PLAYER AVG</Text>
          </View>
        )}
        <View style={mStyles.proCardMetric}>
          <Text style={[mStyles.proCardMetricValue, { color: accentColor }]}>{arrow}</Text>
          <Text style={mStyles.proCardMetricLabel}>VS SEASON</Text>
        </View>
        <View style={mStyles.proCardMetric}>
          <Text style={mStyles.proCardMetricValue}>{prof.sampleSize}</Text>
          <Text style={mStyles.proCardMetricLabel}>FIXTURES</Text>
        </View>
      </View>
      <Text style={mStyles.proCardNote}>
        {prof.position ? `${prof.position} · ` : ''}
        {favorable == null
          ? 'Insufficient data to classify.'
          : favorable
          ? `Favourable — ${prof.opponent} concedes above-average ${prop} to this position.`
          : `Unfavourable — ${prof.opponent} allows below-average ${prop} here.`}
      </Text>
    </View>
  );
}

function renderMatchupPossession(data: Record<string, unknown> | null, pick: any) {
  if (!data) return null;
  const mo = (data as any)?.matchupOverview;
  const ep = (data as any)?.expectedPossession;
  const gt = (data as any)?.expectedGameType ?? mo?.expectedGameType;
  const kmf = (data as any)?.keyMatchupFactor ?? mo?.keyMatchupFactor;
  const ss = (data as any)?.sharpSummary;
  const isHome = pick?.venue !== 'away';
  const teamPoss: number | null = ep ? (isHome ? ep.home : ep.away) : null;
  const oppPoss: number | null = ep ? (isHome ? ep.away : ep.home) : null;
  const hasPoss = teamPoss != null;
  if (!hasPoss && !gt && !kmf && !ss) return null;
  const possColor = teamPoss != null && oppPoss != null
    ? teamPoss > oppPoss + 5 ? Colors.success : teamPoss < oppPoss - 5 ? '#F59E0B' : Colors.textSecondary
    : Colors.textSecondary;
  return (
    <View style={mStyles.proCard}>
      <View style={mStyles.proCardHeader}>
        <View style={[mStyles.proCardPill, { backgroundColor: '#60A5FA18' }]}>
          <Text style={[mStyles.proCardPillText, { color: '#60A5FA' }]}>MATCHUP</Text>
        </View>
        {gt ? <Text style={mStyles.proCardTitle} numberOfLines={1}>{(gt as string).replace(/_/g, ' ').toUpperCase()}</Text> : null}
      </View>
      {hasPoss && teamPoss != null && oppPoss != null && (
        <View style={mStyles.possRow}>
          <View style={mStyles.possTeam}>
            <Text style={[mStyles.possValue, { color: possColor }]}>{Number(teamPoss).toFixed(0)}%</Text>
            <Text style={mStyles.possLabel} numberOfLines={1}>{(pick?.teamName ?? 'HOME').split(' ').pop()?.toUpperCase()}</Text>
          </View>
          <View style={mStyles.possBar}>
            <View style={[mStyles.possBarFill, { width: `${teamPoss}%` as any, backgroundColor: possColor }]} />
          </View>
          <View style={mStyles.possTeam}>
            <Text style={mStyles.possValue}>{Number(oppPoss).toFixed(0)}%</Text>
            <Text style={mStyles.possLabel} numberOfLines={1}>{(pick?.opponentName ?? 'AWAY').split(' ').pop()?.toUpperCase()}</Text>
          </View>
        </View>
      )}
      {kmf ? <Text style={mStyles.proCardNote}>{kmf}</Text> : null}
      {ss ? <Text style={[mStyles.proCardNote, { color: Colors.textSecondary, marginTop: 4 }]}>{ss}</Text> : null}
    </View>
  );
}

function renderManagerContext(data: Record<string, unknown> | null) {
  if (!data) return null;
  const mc = (data as any)?.managerContext;
  if (!mc || !mc.coachName) return null;

  const split = mc.logSplitInfo ?? {};
  const drift = mc.possessionDrift ?? {};
  const isRecent = mc.isRecent === true;
  const isThin   = split.thinSample === true;

  // Only show card when there's something meaningful to surface
  if (!isRecent && !drift.isShift) return null;

  const cardColor   = isRecent ? '#F59E0B' : '#60A5FA';
  const pillLabel   = isRecent ? 'MANAGER CHANGE' : 'TACTICAL SHIFT';
  const daysLabel   = mc.daysElapsed != null ? `${mc.daysElapsed}d ago` : '';

  return (
    <View style={[mStyles.proCard, { borderColor: cardColor + '44' }]}>
      <View style={mStyles.proCardHeader}>
        <View style={[mStyles.proCardPill, { backgroundColor: cardColor + '20' }]}>
          <Text style={[mStyles.proCardPillText, { color: cardColor }]}>{pillLabel}</Text>
        </View>
        <Text style={mStyles.proCardTitle} numberOfLines={1}>
          {mc.coachName}{daysLabel ? ` · ${daysLabel}` : ''}
        </Text>
        {isThin && (
          <View style={[mStyles.proCardPill, { backgroundColor: '#FF6B3520' }]}>
            <Text style={[mStyles.proCardPillText, { color: '#FF6B35' }]}>THIN SAMPLE</Text>
          </View>
        )}
      </View>

      {/* Pre vs post average */}
      {split.preAvg != null && split.postAvg != null && (
        <View style={mStyles.proCardMetrics}>
          <View style={mStyles.proCardMetric}>
            <Text style={[mStyles.proCardMetricValue, { color: Colors.textTertiary, textDecorationLine: 'line-through' }]}>
              {split.preAvg}
            </Text>
            <Text style={mStyles.proCardMetricLabel}>PRE-CHANGE{split.preCount ? ` (${split.preCount}G)` : ''}</Text>
          </View>
          <View style={{ justifyContent: 'center', paddingBottom: 12 }}>
            <Text style={{ color: Colors.textTertiary, fontSize: 16 }}>→</Text>
          </View>
          <View style={mStyles.proCardMetric}>
            <Text style={[mStyles.proCardMetricValue, { color: cardColor }]}>
              {split.postAvg}
            </Text>
            <Text style={mStyles.proCardMetricLabel}>NEW SYSTEM{split.postCount ? ` (${split.postCount}G)` : ''}</Text>
          </View>
          {split.preAvg > 0 && (
            <View style={mStyles.proCardMetric}>
              <Text style={[mStyles.proCardMetricValue, {
                color: split.postAvg > split.preAvg ? '#4ADE80' : '#F87171',
              }]}>
                {split.postAvg > split.preAvg ? '+' : ''}{((split.postAvg - split.preAvg) / split.preAvg * 100).toFixed(0)}%
              </Text>
              <Text style={mStyles.proCardMetricLabel}>ΔROLE</Text>
            </View>
          )}
        </View>
      )}

      {/* Possession drift */}
      {drift.isShift && (
        <View style={mStyles.mgr_driftRow}>
          <Text style={mStyles.mgr_driftLabel}>POSSESSION DRIFT</Text>
          <View style={mStyles.mgr_driftBars}>
            <View style={{ flex: 1, alignItems: 'center', gap: 2 }}>
              <Text style={[mStyles.mgr_driftVal, { color: Colors.textTertiary }]}>{drift.seasonAvg}%</Text>
              <Text style={mStyles.mgr_driftSub}>SEASON</Text>
            </View>
            <Text style={{ color: Colors.textTertiary, fontSize: 13 }}>→</Text>
            <View style={{ flex: 1, alignItems: 'center', gap: 2 }}>
              <Text style={[mStyles.mgr_driftVal, { color: drift.direction === 'up' ? '#4ADE80' : '#F87171' }]}>
                {drift.last5Avg}%
              </Text>
              <Text style={mStyles.mgr_driftSub}>LAST 5</Text>
            </View>
            <View style={{ flex: 1, alignItems: 'center', gap: 2 }}>
              <Text style={[mStyles.mgr_driftVal, { color: drift.direction === 'up' ? '#4ADE80' : '#F87171' }]}>
                {drift.drift > 0 ? '+' : ''}{drift.drift}pp
              </Text>
              <Text style={mStyles.mgr_driftSub}>SHIFT</Text>
            </View>
          </View>
        </View>
      )}

      {mc.prevCoachName && (
        <Text style={[mStyles.proCardNote, { color: Colors.textTertiary }]}>
          Replaced: {mc.prevCoachName}
          {isThin ? '  ·  ⚠ High uncertainty — thin post-change sample' : '  ·  ✓ Model used post-change logs only'}
        </Text>
      )}
    </View>
  );
}

const LEGACY_MODEL_FACTORS: AnalysisFactor[] = [
  ['historical_depth', 'Multi-season player history'],
  ['opponent_history', 'Opponent and comparable-player history'],
  ['possession_range', 'Possession range and upside'],
  ['team_pass_volume', 'Team pass-volume environment'],
  ['player_share', 'Player share of team passes'],
  ['availability_role', 'Availability, lineup, and role'],
  ['game_state', 'Game-state and event scenarios'],
  ['competition_context', 'Competition, venue, and opponent strength'],
  ['tactical_similarity', 'Tactical and role similarity'],
  ['evidence_quality', 'Evidence quality and confidence controls'],
].map(([id, title]) => ({
  id,
  title,
  status: 'unavailable',
  summary: 'Not captured when this saved pick was created.',
  impact: id === 'evidence_quality' ? 'confidence' : 'context',
  direction: 'neutral',
  detail: 'This is a legacy pick. New predictions preserve the exact inputs and sample counts used by the model.',
}));


export default function PicksScreen() {
  const insets = useSafeAreaInsets();
  const { session, logout } = useAuth();
  const qc = useQueryClient();
  const topPad = Platform.OS === 'web' ? 67 : insets.top;
  const [activeTab, setActiveTab] = useState<Tab>('live');
  const [analysisModal, setAnalysisModal] = useState<{ pick: Pick; data: Record<string, unknown> | null; loading: boolean } | null>(null);
  const [analyticsOpen, setAnalyticsOpen] = useState(false);
  const [liveTrackerPick, setLiveTrackerPick] = useState<Pick | null>(null);
  const [streaksOpen, setStreaksOpen] = useState(false);
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [socialOpen, setSocialOpen] = useState(false);
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [imageDisclaimerVisible, setImageDisclaimerVisible] = useState(false);
  const sessionErrCount = useRef(0);
  const autoPostedImagesRef = useRef<Set<string>>(new Set());
  const modalScrollRef = useRef<ScrollView>(null);
  const managerLayoutY = useRef<number>(0);
  const pendingScrollToManager = useRef<boolean>(false);

  // Scroll to manager context card once analysis finishes loading
  React.useEffect(() => {
    if (!analysisModal || analysisModal.loading) return;
    if (!pendingScrollToManager.current) return;
    pendingScrollToManager.current = false;
    const y = managerLayoutY.current;
    if (y > 0) {
      setTimeout(() => {
        modalScrollRef.current?.scrollTo({ y, animated: true });
      }, 350);
    }
  }, [analysisModal?.loading]);

  // One-time owner-only image-use disclaimer (shown once per device install)
  const isOwner = session?.accessType === 'Owner';
  React.useEffect(() => {
    if (!isOwner) return;
    const key = 'rp_image_disclaimer_v1';
    if (Platform.OS === 'web') {
      if (!localStorage.getItem(key)) setImageDisclaimerVisible(true);
    } else {
      import('expo-secure-store').then(m => m.getItemAsync(key)).then(v => {
        if (!v) setImageDisclaimerVisible(true);
      });
    }
  }, [isOwner]);

  const dismissImageDisclaimer = React.useCallback(() => {
    const key = 'rp_image_disclaimer_v1';
    if (Platform.OS === 'web') localStorage.setItem(key, '1');
    else import('expo-secure-store').then(m => m.setItemAsync(key, '1'));
    setImageDisclaimerVisible(false);
  }, []);

  const { data: picks = [], isLoading, refetch, isRefetching, error } = useQuery({
    queryKey: ['picks', session?.email],
    queryFn: async () => {
      if (!session) return [];
      try {
        const result = await listPicks(session.email, session.token);
        sessionErrCount.current = 0; // reset on success
        return result;
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        // Only treat as SESSION_INVALID when the backend explicitly rejects the token
        // (actual HTTP 401 → api.ts throws 'Your session expired. Please sign in again.')
        const isAuthFailure = msg.includes('Your session expired') || msg.includes('Invalid session');
        if (isAuthFailure) {
          sessionErrCount.current += 1;
          // Need 3 consecutive auth failures before showing session-expired UI
          // (avoids false positives from a single network blip)
          if (sessionErrCount.current >= 3) throw new Error('SESSION_INVALID');
          // For first 2 auth failures: throw so React Query keeps stale cache
          // visible instead of replacing it with an empty array
          throw e;
        }
        // Server/network errors: throw so React Query preserves the last
        // successfully-fetched picks while it silently retries in the background.
        // Previously returning [] here would overwrite the cache with an empty
        // array, causing the "No picks" empty state to flash intermittently.
        throw e;
      }
    },
    enabled: !!session,
    staleTime: 10000,
    refetchInterval: 15000,
    refetchIntervalInBackground: false,
    retry: 1,
    retryDelay: 3000,
  });

  useFocusEffect(
    useCallback(() => {
      refetch();
      // React Query's refetchInterval handles ongoing polling.
      // A duplicate setInterval here caused near-simultaneous requests
      // on every 15s boundary → list flicker and navigation glitches.
    }, [refetch])
  );

  const deleteMutation = useMutation({
    mutationFn: (pickId: string) => {
      if (!session) throw new Error('Not authenticated');
      return deletePick(session.email, session.token, pickId);
    },
    onMutate: async (pickId: string) => {
      // Cancel any outgoing refetches so they don't overwrite our optimistic update
      await qc.cancelQueries({ queryKey: ['picks', session?.email] });
      // Snapshot the previous picks in case we need to rollback
      const previousPicks = qc.getQueryData<Pick[]>(['picks', session?.email]);
      // Optimistically remove the pick — disappears immediately
      qc.setQueryData<Pick[]>(['picks', session?.email], (old = []) =>
        old.filter(p => p.pickId !== pickId)
      );
      return { previousPicks };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['picks'] });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    },
    onError: (e: Error, _pickId: string, context: any) => {
      // Rollback to the snapshot if the server call fails
      if (context?.previousPicks) {
        qc.setQueryData(['picks', session?.email], context.previousPicks);
      }
      Alert.alert('Delete failed', e.message);
    },
  });

  const handleDelete = useCallback((pick: Pick) => {
    // The backend owns the canonical identifier. Do not fall back to _id/id:
    // those are client normalization/key fields and can target the wrong
    // record or make a failed delete look like a duplicated pick.
    const id = pick.pickId;
    if (!id) {
      Alert.alert('Delete failed', 'This saved pick has no valid ID. Refresh your picks and try again.');
      return;
    }
    if (Platform.OS === 'web') {
      if (window.confirm(`Remove ${pick.playerName}?`)) {
        deleteMutation.mutate(id);
      }
    } else {
      Alert.alert('Delete Pick', `Remove ${pick.playerName}?`, [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Delete', style: 'destructive', onPress: () => deleteMutation.mutate(id) },
      ]);
    }
  }, [deleteMutation]);

  const handleShareCommunity = useCallback(async (pick: Pick, imageData: string) => {
    if (!session) return;
    try {
      await sharePickToCommunity(session.email, pick, imageData);
      Alert.alert('Shared', 'Your pick is now in Reverse Chat.');
    } catch (e: any) {
      Alert.alert('Share failed', e?.message || 'Please try again.');
    }
  }, [session]);

  const highestConfidenceActivePick = useMemo(() => {
    const active = picks.filter(p =>
      (p.status === 'live' || p.status === 'pending') &&
      !['hit', 'miss', 'push', 'won', 'lost', 'dnp'].includes(p.result ?? '')
    );
    return active
      .filter(p => Number(p.confidence ?? 0) >= 60)
      .sort((a, b) => {
        const confidence = Number(b.confidence ?? 0) - Number(a.confidence ?? 0);
        if (confidence) return confidence;
        const direction = (p: Pick) => {
          const rec = String(p.recommendation || '').toLowerCase();
          const bayes = p.bayesianMetrics as { pOver?: number; pUnder?: number } | undefined;
          const value = Number(rec === 'over' ? bayes?.pOver : bayes?.pUnder);
          return value <= 1 ? value * 100 : value;
        };
        const bayes = direction(b) - direction(a);
        if (bayes) return bayes;
        return Math.abs(Number(b.projectedValue ?? b.projection ?? 0) - Number(b.line ?? 0))
          - Math.abs(Number(a.projectedValue ?? a.projection ?? 0) - Number(a.line ?? 0));
      })[0] ?? null;
  }, [picks]);

  // Manager badge press — open analysis modal for this pick and scroll to manager context
  const handleManagerBadgePress = useCallback(async (pick: Pick) => {
    const id = pick.pickId || pick._id || pick.id;
    if (!id || !session) return;
    pendingScrollToManager.current = true;
    setAnalysisModal({ pick, data: null, loading: true });
    try {
      const result = await fetchPickAnalysis(session.email, session.token, id);
      if (result.found && result.analysis) {
        setAnalysisModal({ pick, data: result.analysis, loading: false });
      } else {
        setAnalysisModal({ pick, data: pick as unknown as Record<string, unknown>, loading: false });
      }
    } catch {
      setAnalysisModal({ pick, data: pick as unknown as Record<string, unknown>, loading: false });
    }
  }, [session]);

  const handleAutoPostImage = useCallback(async (pick: Pick, imageData: string) => {
    if (!session || !pick.pickId || autoPostedImagesRef.current.has(pick.pickId)) return;
    autoPostedImagesRef.current.add(pick.pickId);
    try {
      await autoPostPickToCommunity(session.email, session.token, pick.pickId, imageData);
    } catch (err) {
      autoPostedImagesRef.current.delete(pick.pickId);
      console.warn('[COMMUNITY AUTO POST] failed', err);
    }
  }, [session]);

  const filteredPicks = useMemo(() => picks.filter((p) => {
    const q = searchQuery.trim().toLowerCase();
    if (q) {
      const hay = [
        p.playerName, p.opponentName, p.propType, p.leagueName, p.teamName,
        p.position, p.role, p.venue, p.result, getRecDir(p), getLeagueLabel(p.leagueId) ?? '',
      ].filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }), [picks, searchQuery]);
  const live = filteredPicks.filter(isLive);
  const history = filteredPicks.filter(isHistoryVisible);

  const modalRec = ((analysisModal?.data?.recommendation ?? analysisModal?.pick?.recommendation) as string | undefined)?.toUpperCase() ?? '';
  const modalIsOver = modalRec === 'OVER';
  const modalIsUnder = modalRec === 'UNDER';
  const modalRecColor = modalIsOver ? Colors.success : modalIsUnder ? Colors.error : Colors.textSecondary;
  const _rawModalText = (analysisModal?.data?.tacticalBreakdown ?? analysisModal?.pick?.tacticalBreakdown ?? analysisModal?.data?.reasoning ?? analysisModal?.pick?.reasoning ?? analysisModal?.data?.explanation ?? analysisModal?.data?.sharpSummary ?? analysisModal?.pick?.sharpSummary) as string | undefined;
  // Filter out stale placeholder text that was stored while analysis was still pending
  const modalText = (_rawModalText && !_rawModalText.startsWith('Structured analysis loading')) ? _rawModalText : undefined;
  const modalAlerts = (analysisModal?.data?.tacticalAlerts ?? analysisModal?.pick?.tacticalAlerts ?? []) as string[];
  const capturedModalFactors = (
    (analysisModal?.data?.analysisFactors ?? (analysisModal?.pick as any)?.analysisFactors ?? []) as AnalysisFactor[]
  ).filter((factor) => factor && factor.id && factor.title);
  const modalFactors = capturedModalFactors.length > 0
    ? capturedModalFactors
    : (!analysisModal?.loading ? LEGACY_MODEL_FACTORS : []);

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>

      {/* Owner-only image-use disclaimer (shown once per device) */}
      <Modal visible={imageDisclaimerVisible} transparent animationType="fade" onRequestClose={dismissImageDisclaimer}>
        <Pressable style={disclaimerStyles.overlay} onPress={dismissImageDisclaimer}>
          <Pressable style={disclaimerStyles.sheet} onPress={e => e.stopPropagation()}>
            <View style={disclaimerStyles.iconRow}>
              <Ionicons name="shield-checkmark" size={28} color={Colors.primary} />
            </View>
            <Text style={disclaimerStyles.title}>Image Use Notice</Text>
            <Text style={disclaimerStyles.body}>
              Player photos and team crests displayed on your account are sourced from API-Football and are the property of their respective rights holders.{'\n\n'}
              These images are shown <Text style={disclaimerStyles.bold}>exclusively on your owner account</Text> for personal, non-commercial, informational purposes — they are never displayed to subscribers or third parties.{'\n\n'}
              This use is consistent with fair use under 17 U.S.C. § 107 (informational, non-commercial, personal). By continuing, you acknowledge that you will not use these images in any commercial context.{'\n\n'}
              <Text style={disclaimerStyles.small}>API-Football · api-football.com · Data licensed per your API subscription agreement.</Text>
            </Text>
            <TouchableOpacity style={disclaimerStyles.btn} onPress={dismissImageDisclaimer} activeOpacity={0.85}>
              <Text style={disclaimerStyles.btnText}>I UNDERSTAND — CONTINUE</Text>
            </TouchableOpacity>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Header */}
      <View style={styles.header}>
        {/* Top row: title + actions */}
        <View style={styles.headerTopRow}>
          <Text style={styles.headerTitle}>My Picks</Text>
          <View style={styles.headerActions}>
            <TouchableOpacity onPress={() => setAnalyticsOpen(true)} style={styles.iconBtn} activeOpacity={0.75}>
              <Ionicons name="stats-chart" size={18} color={Colors.primary} />
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setMenuOpen(true)} style={styles.iconBtn} activeOpacity={0.75}>
              <Ionicons name="grid" size={18} color={Colors.text} />
            </TouchableOpacity>
            <NotificationBell />
          </View>
        </View>

        {/* Search */}
        <View style={styles.searchRow}>
          <View style={styles.searchBar}>
            <Ionicons name="search" size={16} color={Colors.textTertiary} />
            <TextInput
              value={searchQuery}
              onChangeText={setSearchQuery}
              placeholder="Search by player, opponent, prop..."
              placeholderTextColor={Colors.textTertiary}
              style={styles.searchInput}
              returnKeyType="search"
              clearButtonMode="while-editing"
            />
            {searchQuery.length > 0 && (
              <TouchableOpacity onPress={() => setSearchQuery('')}>
                <Ionicons name="close-circle" size={16} color={Colors.textTertiary} />
              </TouchableOpacity>
            )}
          </View>
        </View>

        {/* Live / History tabs */}
        <View style={styles.tabToggle}>
          {(['live', 'history'] as Tab[]).map(t => (
            <TouchableOpacity
              key={t}
              style={[styles.toggle, activeTab === t && styles.toggleActive]}
              onPress={() => { setActiveTab(t); Haptics.selectionAsync(); }}
            >
              {t === 'live' && live.length > 0 && activeTab !== 'live' && (
                <View style={styles.tabDot} />
              )}
              <Text style={[styles.toggleText, activeTab === t && styles.toggleTextActive]}>
                {t === 'live' ? `Live${live.length > 0 ? ` (${live.length})` : ''}` : `History${history.length > 0 ? ` (${history.length})` : ''}`}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      {/* Record bar — always show if any picks exist */}
      {filteredPicks.length > 0 && <RecordBar picks={filteredPicks} />}

      {error && (error as Error).message === 'SESSION_INVALID' ? (
        <View style={styles.center}>
          <Ionicons name="lock-closed-outline" size={44} color={Colors.textTertiary} />
          <Text style={[styles.emptyTitle, { marginTop: 12 }]}>Session expired</Text>
          <Text style={[styles.emptySub, { textAlign: 'center', marginTop: 6 }]}>
            Your session timed out. Sign out and back in to restore your picks.
          </Text>
          <TouchableOpacity onPress={async () => { await logout(); router.replace('/auth'); }} style={{ marginTop: 18, backgroundColor: Colors.primary, paddingHorizontal: 24, paddingVertical: 10, borderRadius: 8 }}>
            <Text style={{ color: '#000', fontWeight: '800', fontSize: 14 }}>Sign Out & Re-login</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => refetch()} style={{ marginTop: 12 }}>
            <Text style={{ color: Colors.textTertiary, fontWeight: '600', fontSize: 13 }}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.primary} size="large" />
        </View>
      ) : activeTab === 'live' && live.length === 0 ? (
        <Reanimated.View entering={Platform.OS !== 'web' ? FadeInDown.duration(400).springify() : undefined} style={styles.empty}>
          <View style={styles.emptyIconWrap}>
            <Ionicons name="radio-outline" size={36} color={Colors.primary} />
          </View>
          <Text style={styles.emptyTitle}>No live picks</Text>
          <Text style={styles.emptySub}>Run a prediction on the Predict tab and save it — it'll appear here and update live as your game plays.</Text>
          <TouchableOpacity style={styles.emptyAction} onPress={() => router.replace('/(tabs)/scan')}>
            <Ionicons name="scan-outline" size={14} color="#000" />
            <Text style={styles.emptyActionText}>Make a Prediction</Text>
          </TouchableOpacity>
        </Reanimated.View>
      ) : activeTab === 'history' && history.length === 0 ? (
        <Reanimated.View entering={Platform.OS !== 'web' ? FadeInDown.duration(400).springify() : undefined} style={styles.empty}>
          <View style={styles.emptyIconWrap}>
            <Ionicons name="checkmark-circle-outline" size={36} color={Colors.textTertiary} />
          </View>
          <Text style={styles.emptyTitle}>No settled picks yet</Text>
          <Text style={styles.emptySub}>Your record will appear here once your saved picks have games that finish. Check back after kickoff.</Text>
        </Reanimated.View>
      ) : activeTab === 'live' ? (
        <FlatList
          data={live}
          keyExtractor={(item, i) => item.pickId || item._id || item.id || String(i)}
          initialNumToRender={8}
          maxToRenderPerBatch={8}
          renderItem={({ item }) => {
            const onDeleteForItem = () => handleDelete(item);
            const card = (
              <OwnerPickCard
                pick={item}
                compact
                onTrack={() => setLiveTrackerPick(item)}
                onDelete={onDeleteForItem}
                onShareCommunity={(imageData) => handleShareCommunity(item, imageData)}
                onAutoPostImage={highestConfidenceActivePick?.pickId === item.pickId
                  ? (imageData) => handleAutoPostImage(item, imageData)
                  : undefined}
                onManagerBadgePress={item.managerContext?.isRecent ? () => handleManagerBadgePress(item) : undefined}
              />
            );
            return <SwipeablePickRow onDelete={onDeleteForItem}>{card}</SwipeablePickRow>;
          }}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={Colors.primary} />}
          showsVerticalScrollIndicator={false}
        />
      ) : (
        /* ── HISTORY: flat list of settled picks ── */
        <FlatList
          data={history}
          initialNumToRender={12}
          maxToRenderPerBatch={15}
          keyExtractor={(item, i) => `pick-${item.pickId || item._id || item.id || i}`}
          renderItem={({ item }) => {
            const onDeleteForItem = () => handleDelete(item);
            return (
              <SwipeablePickRow onDelete={onDeleteForItem}>
                <OwnerPickCard
                  pick={item}
                  compact
                  onTrack={() => setLiveTrackerPick(item)}
                  onDelete={onDeleteForItem}
                  onShareCommunity={(imageData) => handleShareCommunity(item, imageData)}
                  onManagerBadgePress={item.managerContext?.isRecent ? () => handleManagerBadgePress(item) : undefined}
                />
              </SwipeablePickRow>
            );
          }}
          contentContainerStyle={[styles.list, { paddingTop: 4 }]}
          refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={Colors.primary} />}
          showsVerticalScrollIndicator={false}
        />
      )}

      {/* ── Analytics Dashboard ── */}
      <AnalyticsDashboard
        visible={analyticsOpen}
        picks={picks}
        onClose={() => setAnalyticsOpen(false)}
      />

      {/* ── Analysis Modal ── */}
      <Modal
        visible={analysisModal !== null}
        animationType="slide"
        transparent
        onRequestClose={() => setAnalysisModal(null)}
      >
        <View style={mStyles.modalContainer}>
          <Pressable style={mStyles.modalBackdrop} onPress={() => setAnalysisModal(null)} />
          <View style={mStyles.modalSheet}>
          {/* Handle */}
          <View style={mStyles.modalHandle} />

          {/* Header */}
          <View style={mStyles.modalHeader}>
            <View style={mStyles.modalPlayerInfo}>
              <Text style={mStyles.modalPlayer} numberOfLines={1}>{analysisModal?.pick.playerName}</Text>
              <Text style={mStyles.modalMeta} numberOfLines={1}>
                {[analysisModal?.pick.teamName, analysisModal?.pick.opponentName
                  ? (analysisModal?.pick.venue === 'away' ? `@ ${analysisModal?.pick.opponentName}` : `vs ${analysisModal?.pick.opponentName}`)
                  : null].filter(Boolean).join(' · ')}
              </Text>
            </View>
            <View style={mStyles.modalRight}>
              {modalRec ? (
                <View style={[mStyles.modalRecBadge, { backgroundColor: modalIsOver ? Colors.successDim : modalIsUnder ? Colors.errorDim : Colors.cardSecondary }]}>
                  <Text style={[mStyles.modalRecText, { color: modalRecColor }]}>{modalRec}</Text>
                </View>
              ) : null}
              <TouchableOpacity onPress={() => setAnalysisModal(null)} style={mStyles.modalClose}>
                <Ionicons name="close" size={18} color={Colors.textSecondary} />
              </TouchableOpacity>
            </View>
          </View>

          {/* Prop row */}
          <View style={mStyles.modalPropRow}>
            <Text style={mStyles.modalPropText}>
              {PROP_LABELS[analysisModal?.pick.propType ?? ''] ?? analysisModal?.pick.propType} · Line {analysisModal?.pick.line}
            </Text>
            {analysisModal?.data?.projectedValue != null && (
              <Text style={[mStyles.modalProjText, { color: modalRecColor }]}>
                Proj {(analysisModal.data.projectedValue as number).toFixed(1)}
              </Text>
            )}
          </View>

          {/* Edge-gap pill row — surfaces how far projection sits from the line
              and whether league calibration / game-script informed the call. */}
          {(() => {
            const bm: any = (analysisModal?.data as any)?.bayesianMetrics ?? (analysisModal?.pick?.bayesianMetrics as any) ?? {};
            const gapPct = bm.edgeGapPct;
            const gapBand = bm.edgeGapBand;
            const lcal = bm.leagueCalibration;
            const gs = bm.gameScript;
            if (gapPct == null && !lcal?.applied && !gs?.applied) return null;
            const bandColor = gapBand === 'DEEP' ? Colors.success
              : gapBand === 'STRONG' ? Colors.success
              : gapBand === 'MODERATE' ? Colors.primary
              : Colors.textSecondary;
            return (
              <View style={mStyles.modalEdgeRow}>
                {gapPct != null && (
                  <View style={[mStyles.edgePill, { borderColor: bandColor }]}>
                    <Text style={[mStyles.edgePillText, { color: bandColor }]}>
                      {gapBand ?? 'EDGE'} · {gapPct > 0 ? '+' : ''}{Number(gapPct).toFixed(1)}%
                    </Text>
                  </View>
                )}
                {lcal?.applied && lcal?.n > 0 && (
                  <View style={[mStyles.edgePill, { borderColor: Colors.borderSubtle }]}>
                    <Text style={[mStyles.edgePillText, { color: Colors.textSecondary }]}>
                      League calib · n={lcal.n} · {Math.round((lcal.hit_rate ?? 0) * 100)}% hit
                    </Text>
                  </View>
                )}
                {gs?.applied && (
                  <View style={[mStyles.edgePill, { borderColor: Colors.borderSubtle }]}>
                    <Text style={[mStyles.edgePillText, { color: Colors.textSecondary }]}>
                      Game-script · ×{Number(gs.multiplier).toFixed(3)}
                    </Text>
                  </View>
                )}
              </View>
            );
          })()}

          {/* ── Game Script Banner (on analysis modal) ── */}
          {(() => {
            const gs = (analysisModal?.data as any)?.gameScript ?? analysisModal?.pick?.gameScript;
            if (!gs || !gs.dominant) return null;
            const color = gs.color || '#60A5FA';
            const iconMap: Record<string, string> = {
              'low_scoring': 'shield', 'high_scoring': 'flame',
              'open_close': 'analytics', 'home_blowout': 'trending-up',
              'away_blowout': 'trending-down',
            };
            const icon = (iconMap[gs.dominant] || 'analytics') as any;
            return (
              <View style={[mStyles.gsBanner, { borderColor: color + '44' }]}>
                <View style={[mStyles.gsBannerStripe, { backgroundColor: color }]} />
                <View style={mStyles.gsBannerBody}>
                  <View style={mStyles.gsBannerHeader}>
                    <Ionicons name={icon} size={14} color={color} />
                    <Text style={[mStyles.gsBannerLabel, { color }]}>GAME SCRIPT</Text>
                    <Text style={mStyles.gsBannerProb}>{Math.round((gs.dominant_probability || 0) * 100)}%</Text>
                  </View>
                  <Text style={[mStyles.gsBannerTitle, { color }]}>{gs.key_finding}</Text>
                  {gs.scenarios && gs.scenarios.length > 1 && (
                    <View style={mStyles.gsBannerScenarios}>
                      {gs.scenarios.slice(0, 3).map((s: any, i: number) => (
                        <View key={i} style={mStyles.gsBannerChip}>
                          <Text style={mStyles.gsBannerChipName}>{s.name}</Text>
                          <Text style={[mStyles.gsBannerChipPct, { color }]}>{Math.round(s.probability * 100)}%</Text>
                        </View>
                      ))}
                    </View>
                  )}
                  {gs.expected_total_goals != null && (
                    <Text style={mStyles.gsBannerSub}>
                      Expected {gs.expected_total_goals} total goals
                    </Text>
                  )}
                </View>
              </View>
            );
          })()}

          {/* Moneyline Odds — always shown; "Not available" when no data */}
          {(() => {
            const ml = (analysisModal?.data as any)?.moneyline ?? (analysisModal?.pick as any)?.moneyline;
            const formatOdds = (val: string) => {
              if (!val || val === 'N/A') return '';
              const n = parseFloat(val);
              if (isNaN(n)) return val;
              if (n > 1 && n < 50) {
                if (n >= 2) return `+${Math.round((n - 1) * 100)}`;
                return `${Math.round(-100 / (n - 1))}`;
              }
              return n > 0 ? `+${Math.round(n)}` : `${Math.round(n)}`;
            };
            if (ml) {
              const h = formatOdds(ml.home);
              const d = formatOdds(ml.draw);
              const a = formatOdds(ml.away);
              if (h || d || a) {
                // Saved odds remain fixture-oriented: home odds belong to the
                // verified homeTeam and away odds to the verified awayTeam.
                // Never infer this pairing from the original player venue.
                const homeTeamName = String(analysisModal?.data?.homeTeam || analysisModal?.pick?.homeTeam || analysisModal?.pick?.teamName || 'HOME');
                const awayTeamName = String(analysisModal?.data?.awayTeam || analysisModal?.pick?.awayTeam || analysisModal?.pick?.opponentName || 'AWAY');
                const homeTeamShort = homeTeamName.split(' ').pop()?.slice(0, 5).toUpperCase() || 'HOME';
                const awayTeamShort = awayTeamName.split(' ').pop()?.slice(0, 5).toUpperCase() || 'AWAY';
                return (
                  <View style={mStyles.oddsRow}>
                    <View style={mStyles.oddsHeader}>
                      <Ionicons name="cash-outline" size={11} color={Colors.textTertiary} />
                      <Text style={mStyles.oddsLabel}>MONEYLINE</Text>
                    </View>
                    <View style={mStyles.oddsPills}>
                      <View style={mStyles.oddsPill}>
                        <Text style={mStyles.oddsPillTeam}>{homeTeamShort}</Text>
                        <Text style={mStyles.oddsPillVal}>{h}</Text>
                      </View>
                      {d ? (
                        <View style={mStyles.oddsPill}>
                          <Text style={mStyles.oddsPillTeam}>DRAW</Text>
                          <Text style={mStyles.oddsPillVal}>{d}</Text>
                        </View>
                      ) : null}
                      <View style={mStyles.oddsPill}>
                        <Text style={mStyles.oddsPillTeam}>{awayTeamShort}</Text>
                        <Text style={mStyles.oddsPillVal}>{a}</Text>
                      </View>
                    </View>
                    <Text style={mStyles.oddsDisclaim}>Indicative · verify with your sportsbook</Text>
                  </View>
                );
              }
            }
            return (
              <View style={mStyles.oddsRow}>
                <View style={mStyles.oddsHeader}>
                  <Ionicons name="cash-outline" size={11} color={Colors.textTertiary} />
                  <Text style={mStyles.oddsLabel}>MONEYLINE</Text>
                </View>
                <Text style={mStyles.oddsUnavail}>Not available for this market</Text>
              </View>
            );
          })()}

          <View style={mStyles.modalDivider} />

          {/* Body */}
          <ScrollView ref={modalScrollRef} style={mStyles.modalScroll} contentContainerStyle={mStyles.modalScrollContent} showsVerticalScrollIndicator={false}>

            {/* ── Loading state ── */}
            {analysisModal?.loading && (
              <View style={mStyles.modalLoading}>
                <ActivityIndicator color={Colors.primary} />
                <Text style={mStyles.modalLoadingText}>Loading analysis…</Text>
              </View>
            )}

            {/* ── POST-MATCH BREAKDOWN (settled only, shown FIRST) ── */}
            {(() => {
              const pick = analysisModal?.pick as any;
              const isSettled = pick?.status === 'settled' && (pick?.result === 'hit' || pick?.result === 'miss');
              if (!isSettled) return null;
              const review = pick?.matchReview as string | undefined;
              const isHit = (pick?.result || '').toLowerCase() === 'hit';
              const accent = isHit ? Colors.primary : '#FF6B35';
              return (
                <View style={{
                  borderWidth: 1, borderColor: accent + '55', borderLeftWidth: 4, borderLeftColor: accent,
                  backgroundColor: isHit ? 'rgba(57,255,20,0.05)' : 'rgba(255,107,53,0.05)',
                  borderRadius: 10, paddingVertical: 14, paddingHorizontal: 14, marginBottom: 16,
                }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                    <Ionicons name={isHit ? 'checkmark-circle' : 'close-circle'} size={16} color={accent} />
                    <Text style={{ fontSize: 11, fontWeight: '800', color: accent, letterSpacing: 1.4 }}>
                      {isHit ? 'VERDICT: HIT' : 'VERDICT: MISS'}
                    </Text>
                    <View style={{ marginLeft: 'auto', backgroundColor: accent + '22', borderRadius: 4, paddingHorizontal: 7, paddingVertical: 2 }}>
                      <Text style={{ fontSize: 9, fontWeight: '700', color: accent, letterSpacing: 0.8 }}>POST-MATCH LEDGER</Text>
                    </View>
                  </View>
                  {review ? (
                    <Text style={{ fontSize: 14, color: Colors.text, lineHeight: 22 }}>{review}</Text>
                  ) : (
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                      <ActivityIndicator size="small" color={accent} />
                      <Text style={{ fontSize: 13, color: Colors.textSecondary }}>Generating match breakdown…</Text>
                    </View>
                  )}
                </View>
              );
            })()}

            {/* ── EVIDENCE QUALITY CALLOUT — confidence cap or PASS reason ── */}
            {!analysisModal?.loading && (() => {
              const pick = analysisModal?.pick as any;
              const data = analysisModal?.data as any;
              // qualityConfidenceCapped persisted on the pick at save time
              const capped = pick?.qualityConfidenceCapped === true;
              // passReason — shown for PASS picks
              const passReason = pick?.passReason || data?.passReason;
              const isPass = (pick?.recommendation || '').toUpperCase() === 'PASS';
              // evidence quality from the fetched analysis data
              const eq = data?.evidenceQuality as Record<string, unknown> | undefined;
              const logCount = (eq?.realPlayerLogCount as number | undefined) ?? null;
              const capReasons = (eq?.capReasons as string[] | undefined) ?? [];
              const firstReason = capReasons[0] ?? null;

              if (!capped && !isPass) return null;
              if (isPass && passReason) {
                return (
                  <View style={{
                    marginBottom: 12,
                    padding: 12, borderRadius: 10,
                    backgroundColor: 'rgba(255,165,0,0.08)',
                    borderWidth: 1, borderColor: 'rgba(255,165,0,0.35)',
                    flexDirection: 'row', gap: 8, alignItems: 'flex-start',
                  }}>
                    <Ionicons name="pause-circle-outline" size={14} color="#FFA500" style={{ marginTop: 1 }} />
                    <View style={{ flex: 1 }}>
                      <Text style={{ fontSize: 11, fontWeight: '800', color: '#FFA500', letterSpacing: 0.4, marginBottom: 3 }}>
                        PASS — SKIP THIS PROP
                      </Text>
                      <Text style={{ fontSize: 12, color: '#9CA3AF', lineHeight: 17 }}>{passReason}</Text>
                    </View>
                  </View>
                );
              }
              if (capped) {
                return (
                  <View style={{
                    marginBottom: 12,
                    padding: 12, borderRadius: 10,
                    backgroundColor: 'rgba(245,158,11,0.08)',
                    borderWidth: 1, borderColor: 'rgba(245,158,11,0.30)',
                    flexDirection: 'row', gap: 8, alignItems: 'flex-start',
                  }}>
                    <Ionicons name="warning-outline" size={14} color="#F59E0B" style={{ marginTop: 1 }} />
                    <View style={{ flex: 1 }}>
                      <Text style={{ fontSize: 11, fontWeight: '800', color: '#F59E0B', letterSpacing: 0.4, marginBottom: 3 }}>
                        EVIDENCE LIMITED{logCount != null ? ` · ${logCount} game log${logCount !== 1 ? 's' : ''}` : ''}
                      </Text>
                      <Text style={{ fontSize: 12, color: '#9CA3AF', lineHeight: 17 }}>
                        {firstReason ?? 'Confidence was capped because game-log data was limited at prediction time.'}
                      </Text>
                    </View>
                  </View>
                );
              }
              return null;
            })()}

            {/* ── EVIDENCE SUMMARY ── */}
            {!analysisModal?.loading && renderEvidenceSummary(analysisModal?.data as Record<string, unknown> | null)}

            {/* ── RECENT GAME DATA ── */}
            {!analysisModal?.loading && renderRecentGameData(
              analysisModal?.data as Record<string, unknown> | null,
              analysisModal?.pick ?? null,
            )}

            {/* ── OPPONENT DEFENSIVE PROFILE ── */}
            {!analysisModal?.loading && renderOpponentDefProfile(
              analysisModal?.data as Record<string, unknown> | null,
              analysisModal?.pick as any,
            )}

            {/* ── MATCHUP & POSSESSION ── */}
            {!analysisModal?.loading && renderMatchupPossession(
              analysisModal?.data as Record<string, unknown> | null,
              analysisModal?.pick as any,
            )}

            {/* ── TACTICAL INTELLIGENCE ── */}
            {!analysisModal?.loading && renderTacticalIntelligence(
              analysisModal?.data as Record<string, unknown> | null,
            )}

            {/* ── MANAGER / TACTICAL SHIFT CONTEXT ── */}
            <View onLayout={(e) => { managerLayoutY.current = e.nativeEvent.layout.y; }}>
              {!analysisModal?.loading && renderManagerContext(
                analysisModal?.data as Record<string, unknown> | null,
              )}
            </View>

            {/* ── STRUCTURED ANALYSIS BREAKDOWN ── */}
            {!analysisModal?.loading && modalText && (() => {
              const pick = analysisModal?.pick as any;
              const isSettled = pick?.status === 'settled' && (pick?.result === 'hit' || pick?.result === 'miss');
              return (
                <View>
                  {isSettled && (
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                      <View style={{ flex: 1, height: 1, backgroundColor: Colors.borderSubtle }} />
                      <Text style={{ fontSize: 9, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 1.2 }}>PRE-MATCH INTEL</Text>
                      <View style={{ flex: 1, height: 1, backgroundColor: Colors.borderSubtle }} />
                    </View>
                  )}
                  <View style={mStyles.aiBlocks}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                      <Ionicons
                        name="calculator-outline"
                        size={13}
                        color={Colors.textTertiary}
                      />
                      <Text style={{ fontSize: 9, fontWeight: '800', letterSpacing: 1.1, color: Colors.textTertiary }}>
                        DETERMINISTIC · FULL MODEL EXPLANATION
                      </Text>
                    </View>
                    {renderAnalysisBlocks(modalText, modalRec)}
                  </View>
                </View>
              );
            })()}

            {/* ── SIGNAL ALERTS ── */}
            {!analysisModal?.loading && modalAlerts.length > 0 && (
              <View style={{ gap: 8, marginTop: 4 }}>
                {modalAlerts.slice(0, 5).map((alert, i) => {
                  const lower = alert.toLowerCase();
                  const isLineDeviation = lower.includes('line deviation') || lower.includes('edgegap') || lower.includes('deviation');
                  const isRisk = lower.includes('risk') || lower.includes('dismissal') || lower.includes('invalid') || lower.includes('flip') || lower.includes('void') || lower.includes('red card');
                  const isBoost = lower.includes('boost') || lower.includes('infl') || lower.includes('rise') || lower.includes('high');
                  const alertColor = isRisk ? '#FF6B35' : isLineDeviation ? '#60A5FA' : isBoost ? Colors.primary : '#60A5FA';
                  const iconName: any = isRisk ? 'warning' : isLineDeviation ? 'stats-chart' : isBoost ? 'trending-up' : 'information-circle';
                  const label = isLineDeviation ? 'LINE INTEL' : isRisk ? 'RISK SIGNAL' : 'SIGNAL';
                  return (
                    <View key={i} style={{
                      backgroundColor: alertColor + '0D', borderRadius: 8, padding: 10,
                      borderWidth: 1, borderColor: alertColor + '33', borderLeftWidth: 3, borderLeftColor: alertColor,
                    }}>
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5, marginBottom: 4 }}>
                        <Ionicons name={iconName} size={11} color={alertColor} />
                        <Text style={{ fontSize: 9, fontWeight: '800', color: alertColor, letterSpacing: 1.1 }}>{label}</Text>
                      </View>
                      <Text style={{ fontSize: 12.5, color: Colors.text, lineHeight: 18 }}>{alert}</Text>
                    </View>
                  );
                })}
              </View>
            )}

            {/* ── MODEL FACTORS ── */}
            {!analysisModal?.loading && modalFactors.length > 0 && renderModelFactors(modalFactors)}

            {/* No analysis yet */}
            {!analysisModal?.loading && !modalText && modalAlerts.length === 0 && (() => {
              const pick = analysisModal?.pick as any;
              const isSettled = pick?.status === 'settled' && (pick?.result === 'hit' || pick?.result === 'miss');
              if (isSettled) return null;
              const hasData = !!(
                (analysisModal?.data as any)?.opponentDefensiveProfile ||
                (analysisModal?.data as any)?.h2hPlayerStats?.sampleSize ||
                (analysisModal?.data as any)?.expectedPossession
              );
              if (hasData) return null;
              return (
                <View style={mStyles.modalLoading}>
                  <Ionicons name="analytics-outline" size={32} color={Colors.textTertiary} />
                  <Text style={mStyles.modalLoadingText}>No analysis found for this pick yet.</Text>
                </View>
              );
            })()}
          </ScrollView>
          </View>
        </View>
      </Modal>

      {/* ── Feature Menu ── */}
      <Modal visible={menuOpen} animationType="fade" transparent onRequestClose={() => setMenuOpen(false)}>
        <View style={styles.menuOverlay}>
          <Pressable style={styles.menuBackdrop} onPress={() => setMenuOpen(false)} />
          <View style={styles.menuSheet}>
            <Text style={styles.menuTitle}>More</Text>
            <View style={styles.menuGrid}>
              <MenuItem icon="calendar" label="Calendar" onPress={() => { setMenuOpen(false); setCalendarOpen(true); }} />
              <MenuItem icon="people" label="Social Feed" onPress={() => { setMenuOpen(false); setSocialOpen(true); }} />
              <MenuItem icon="notifications" label="Alerts" onPress={() => { setMenuOpen(false); setAlertsOpen(true); }} />
              <MenuItem icon="trophy" label="Streaks" onPress={() => { setMenuOpen(false); setStreaksOpen(true); }} />
            </View>
          </View>
        </View>
      </Modal>

      {/* ── Live Match Tracker ── */}
      {liveTrackerPick && (
        <LiveMatchTracker pick={liveTrackerPick} visible={!!liveTrackerPick} onClose={() => setLiveTrackerPick(null)} />
      )}

      {/* ── Streaks & Achievements ── */}
      <StreaksAchievements
        visible={streaksOpen}
        onClose={() => setStreaksOpen(false)}
        picks={picks.map((p, i) => ({
          id: p.pickId || p._id || p.id || String(i),
          status: ((p.result || 'pending') as any),
          sport: 'soccer',
          type: getRecDir(p)?.toLowerCase() as any,
          date: p.createdAt || p.settledAt || new Date().toISOString(),
        }))}
      />

      {/* ── Calendar ── */}
      <PicksCalendar visible={calendarOpen} picks={picks} onClose={() => setCalendarOpen(false)} />

      {/* ── Social Feed ── */}
      <Modal visible={socialOpen} animationType="slide" transparent onRequestClose={() => setSocialOpen(false)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.socialSheet}>
            <View style={styles.socialHeader}>
              <Text style={styles.socialTitle}>Social Feed</Text>
              <TouchableOpacity onPress={() => setSocialOpen(false)} style={styles.closeBtn}>
                <Ionicons name="close" size={18} color={Colors.text} />
              </TouchableOpacity>
            </View>
            <SocialFeed picks={picks} />
          </View>
        </View>
      </Modal>

      {/* ── Custom Alerts ── */}
      <CustomAlerts visible={alertsOpen} onClose={() => setAlertsOpen(false)} />

    </View>
  );
}

function MenuItem({ icon, label, onPress }: { icon: any; label: string; onPress: () => void }) {
  return (
    <TouchableOpacity onPress={onPress} style={styles.menuItem} activeOpacity={0.7}>
      <View style={styles.menuIconWrap}>
        <Ionicons name={icon} size={22} color={Colors.primary} />
      </View>
      <Text style={styles.menuItemLabel}>{label}</Text>
    </TouchableOpacity>
  );
}

const secStyles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginHorizontal: 16,
    marginTop: 14,
    marginBottom: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: Colors.card,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  headerLeft:  { flexDirection: 'row', alignItems: 'center', gap: 7 },
  headerRight: { flexDirection: 'row', alignItems: 'center' },
  headerIcon:  { fontSize: 16 },
  headerLabel: { fontSize: 13, fontWeight: '800', color: Colors.text, letterSpacing: 0.3 },
  countPill: {
    backgroundColor: Colors.cardSecondary,
    borderRadius: 999, paddingHorizontal: 7, paddingVertical: 1,
  },
  countText:   { fontSize: 10, fontWeight: '700', color: Colors.textSecondary },
  headerStats: { flexDirection: 'row', alignItems: 'baseline' },
  statNum:     { fontSize: 13, fontWeight: '800' },
  statLbl:     { fontSize: 9, fontWeight: '600', color: Colors.textTertiary },
  statDiv:     { fontSize: 11, color: Colors.textTertiary },
  winPct:      { fontSize: 14, fontWeight: '800' },
});

const mStyles = StyleSheet.create({
  modalContainer: { flex: 1, justifyContent: 'flex-end' },
  modalBackdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(0,0,0,0.6)' },
  modalSheet: {
    backgroundColor: Colors.card,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    maxHeight: '82%',
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    borderBottomWidth: 0,
  },
  modalHandle: {
    width: 36, height: 4, borderRadius: 2,
    backgroundColor: Colors.border, alignSelf: 'center', marginTop: 10,
  },
  modalHeader: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    paddingHorizontal: 18, paddingTop: 14, paddingBottom: 10, gap: 10,
  },
  modalPlayerInfo: { flex: 1 },
  modalPlayer: { fontSize: 18, fontWeight: '800', color: Colors.text },
  modalMeta: { fontSize: 12, color: Colors.textSecondary, marginTop: 2 },
  modalRight: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  modalRecBadge: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 8 },
  modalRecText: { fontSize: 13, fontWeight: '800', letterSpacing: 0.5 },
  modalClose: { padding: 4 },
  modalPropRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 18, paddingBottom: 12,
  },
  modalPropText: { fontSize: 13, color: Colors.textSecondary },
  modalProjText: { fontSize: 14, fontWeight: '700' },
  modalEdgeRow: {
    flexDirection: 'row', flexWrap: 'wrap', gap: 6,
    paddingHorizontal: 18, paddingTop: 4, paddingBottom: 8,
  },
  edgePill: {
    paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: 6, borderWidth: 1,
    backgroundColor: Colors.cardSecondary,
  },
  edgePillText: { fontSize: 11, fontWeight: '700', letterSpacing: 0.3 },
  modalDivider: { height: 1, backgroundColor: Colors.borderSubtle },
  modalScroll: { flex: 0 },
  modalScrollContent: { padding: 18, paddingBottom: 40 },
  modalLoading: { alignItems: 'center', paddingVertical: 40, gap: 14 },
  modalLoadingText: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center' },
  aiBlocks: { gap: 16 },
  oddsRow: { marginHorizontal: 16, marginBottom: 10, gap: 5 },
  oddsHeader: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  oddsLabel: { fontSize: 9, fontWeight: '800', color: Colors.textTertiary, letterSpacing: 1.2 },
  oddsPills: { flexDirection: 'row', gap: 6 },
  oddsPill: {
    flex: 1, backgroundColor: Colors.cardSecondary, borderRadius: 8,
    paddingVertical: 7, paddingHorizontal: 8, alignItems: 'center', gap: 2,
  },
  oddsPillTeam: { fontSize: 9, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 0.8 },
  oddsPillVal: { fontSize: 15, fontWeight: '800', color: Colors.text },
  oddsDisclaim: { fontSize: 9, color: Colors.textTertiary, fontStyle: 'italic', marginTop: 1 },
  oddsUnavail: { fontSize: 12, color: Colors.textTertiary, fontStyle: 'italic' },
  aiVerdictBlock: {
    backgroundColor: 'rgba(57,255,20,0.06)',
    borderLeftWidth: 3, borderLeftColor: Colors.primary,
    borderRadius: 8, padding: 12, gap: 6,
  },
  aiVerdictPill: { alignSelf: 'flex-start', borderRadius: 4, paddingHorizontal: 7, paddingVertical: 2 },
  aiVerdictLabel: { fontSize: 9, fontWeight: '800', letterSpacing: 1.5 },
  aiVerdictText: { fontSize: 14, fontWeight: '600', lineHeight: 21, color: Colors.text },
  aiTldrBlock: { backgroundColor: Colors.cardSecondary, borderRadius: 8, padding: 12 },
  aiTldrText: { fontSize: 12, color: Colors.textSecondary, lineHeight: 18, fontStyle: 'italic' },
  aiSection: { gap: 5 },
  aiSectionTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.2 },
  aiSectionBody: { fontSize: 13, color: Colors.textSecondary, lineHeight: 20 },
  factorSection: {
    marginBottom: 18,
    gap: 10,
  },
  factorSectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingBottom: 2,
  },
  factorSectionTitle: {
    fontSize: 10,
    fontWeight: '900',
    color: Colors.primary,
    letterSpacing: 1.4,
  },
  factorSectionSubtitle: {
    fontSize: 11,
    color: Colors.textTertiary,
    marginTop: 3,
  },
  factorGrid: {
    gap: 8,
  },
  factorCard: {
    backgroundColor: Colors.cardSecondary,
    borderRadius: 9,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    borderLeftWidth: 3,
    padding: 10,
    gap: 5,
  },
  factorCardTop: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 8,
  },
  factorTitle: {
    flex: 1,
    fontSize: 12,
    fontWeight: '800',
    color: Colors.text,
  },
  factorStatus: {
    fontSize: 8,
    fontWeight: '900',
    letterSpacing: 0.8,
  },
  factorSummary: {
    fontSize: 11.5,
    color: Colors.textSecondary,
    lineHeight: 17,
  },
  factorValue: {
    fontSize: 11,
    fontWeight: '700',
    color: Colors.text,
    lineHeight: 16,
  },
  factorMeta: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginTop: 1,
  },
  factorImpact: {
    fontSize: 8,
    fontWeight: '900',
    color: Colors.textTertiary,
    letterSpacing: 0.8,
  },
  factorSample: {
    fontSize: 9,
    color: Colors.textTertiary,
    fontWeight: '600',
  },
  factorDetail: {
    fontSize: 10,
    color: Colors.textTertiary,
    lineHeight: 15,
  },
  // ── Game Script Banner (analysis modal)
  gsBanner: {
    flexDirection: 'row',
    marginHorizontal: 16,
    marginBottom: 10,
    borderRadius: 10,
    borderWidth: 1,
    overflow: 'hidden',
    backgroundColor: '#0a0a0a',
  },
  gsBannerStripe: { width: 4 },
  gsBannerBody: { flex: 1, padding: 12, gap: 6 },
  gsBannerHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  gsBannerLabel: { fontSize: 9, fontWeight: '800', letterSpacing: 1.2 },
  gsBannerProb: { fontSize: 11, fontWeight: '800', color: '#fff', marginLeft: 'auto' },
  gsBannerTitle: { fontSize: 15, fontWeight: '800', letterSpacing: 0.3 },
  gsBannerScenarios: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 2 },
  gsBannerChip: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: '#151515', borderRadius: 5,
    paddingHorizontal: 7, paddingVertical: 3,
  },
  gsBannerChipName: { fontSize: 9, color: '#9CA3AF', fontWeight: '600' },
  gsBannerChipPct: { fontSize: 9, fontWeight: '800' },
  gsBannerSub: { fontSize: 10, color: '#6B7280', fontWeight: '500', marginTop: 2 },
  // ── Evidence Summary ribbon
  evidenceRow: {
    flexDirection: 'row', flexWrap: 'wrap', gap: 7,
    marginBottom: 14, backgroundColor: Colors.cardSecondary,
    borderRadius: 10, padding: 10,
    borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  evidenceCell: {
    flex: 1, minWidth: 52, alignItems: 'center', gap: 1,
    borderWidth: 1, borderColor: Colors.borderSubtle,
    borderRadius: 8, paddingVertical: 8, paddingHorizontal: 4,
    backgroundColor: Colors.card,
  },
  evidenceCellValue: { fontSize: 17, fontWeight: '800', color: Colors.text },
  evidenceCellLabel: { fontSize: 7, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 0.7, textAlign: 'center' },
  // ── Pro data cards
  proCard: {
    backgroundColor: Colors.cardSecondary, borderRadius: 12,
    borderWidth: 1, borderColor: Colors.borderSubtle,
    padding: 14, marginBottom: 12, gap: 10,
  },
  proCardHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  proCardPill: { borderRadius: 5, paddingHorizontal: 7, paddingVertical: 2 },
  proCardPillText: { fontSize: 8, fontWeight: '900', letterSpacing: 1.2 },
  proCardTitle: { flex: 1, fontSize: 13, fontWeight: '700', color: Colors.text },
  proCardMetrics: { flexDirection: 'row', gap: 7 },
  proCardMetric: {
    flex: 1, alignItems: 'center', gap: 2,
    backgroundColor: Colors.card, borderRadius: 8,
    paddingVertical: 8, paddingHorizontal: 4,
  },
  proCardMetricValue: { fontSize: 15, fontWeight: '800', color: Colors.text },
  proCardMetricLabel: { fontSize: 7, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 0.7, textAlign: 'center' },
  proCardNote: { fontSize: 11.5, color: Colors.textSecondary, lineHeight: 17 },
  // ── Possession bar
  possRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  possTeam: { alignItems: 'center', width: 46 },
  possValue: { fontSize: 17, fontWeight: '800', color: Colors.text },
  possLabel: { fontSize: 7, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 0.5 },
  possBar: { flex: 1, height: 6, backgroundColor: Colors.borderSubtle, borderRadius: 3, overflow: 'hidden' },
  possBarFill: { height: '100%', borderRadius: 3 },
  // ── H2H table
  h2hTable: { gap: 3, marginTop: 2 },
  h2hRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 6, paddingHorizontal: 8,
    backgroundColor: Colors.card, borderRadius: 6,
  },
  h2hDate: { fontSize: 10, color: Colors.textTertiary, fontWeight: '600', width: 52 },
  h2hVenue: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', width: 12 },
  h2hOpp: { flex: 1, fontSize: 11, color: Colors.textSecondary, fontWeight: '500' },
  h2hPoss: { fontSize: 10, color: Colors.textTertiary, width: 32, textAlign: 'right' },
  h2hStat: { fontSize: 14, fontWeight: '800', width: 26, textAlign: 'right' },
  inlineH2H: {
    borderTopWidth: 1, borderTopColor: Colors.borderSubtle,
    marginTop: 7, paddingTop: 7, gap: 4,
  },
  inlineH2HHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  inlineH2HTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  inlineH2HTitle: { fontSize: 8, fontWeight: '800', color: Colors.primary, letterSpacing: 0.7 },
  inlineH2HMeta: { fontSize: 8, color: Colors.textTertiary, fontWeight: '600' },
  inlineH2HRows: { gap: 1 },
  inlineH2HRow: { flexDirection: 'row', alignItems: 'center', gap: 5, minHeight: 19 },
  inlineH2HDate: { fontSize: 8, color: Colors.textTertiary, width: 56 },
  inlineH2HVenue: { fontSize: 8, color: Colors.textTertiary, fontWeight: '800', width: 10 },
  inlineH2HOpp: { flex: 1, fontSize: 9, color: Colors.textSecondary },
  inlineH2HPoss: { fontSize: 8, color: Colors.textTertiary, width: 28, textAlign: 'right' },
  inlineH2HStat: { fontSize: 11, fontWeight: '800', width: 24, textAlign: 'right' },
  inlineH2HMore: { fontSize: 7, color: Colors.textTertiary, textAlign: 'right' },
  // ── Manager / Tactical Shift card
  mgr_driftRow: {
    gap: 6,
    backgroundColor: Colors.card, borderRadius: 8,
    padding: 10,
  },
  mgr_driftLabel: { fontSize: 8, fontWeight: '900', color: Colors.textTertiary, letterSpacing: 1.2 },
  mgr_driftBars: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  mgr_driftVal: { fontSize: 16, fontWeight: '800' },
  mgr_driftSub: { fontSize: 7, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 0.7 },
});

function StatTile({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <View style={styles.statTile}>
      <Text style={[styles.statTileValue, accent ? { color: accent } : null]}>{value}</Text>
      <Text style={styles.statTileLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: {
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: 14,
    gap: 12,
    backgroundColor: Colors.background,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
  },
  headerTopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerTitle: { fontSize: 28, fontWeight: '800', color: Colors.text },
  headerActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  iconBtn: {
    width: 38,
    height: 38,
    borderRadius: 10,
    backgroundColor: Colors.card,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    alignItems: 'center',
    justifyContent: 'center',
  },
  searchRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  searchBar: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: Colors.card,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  searchInput: { flex: 1, color: Colors.text, fontSize: 15, padding: 0, height: 20 },
  chipRow: { gap: 8, paddingVertical: 2 },
  clearChip: { backgroundColor: Colors.primaryDim, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 6 },
  clearChipText: { color: Colors.primary, fontWeight: '700', fontSize: 12 },
  reviewBtn: {
    marginTop: 8,
    backgroundColor: Colors.primary,
    borderRadius: 14,
    paddingVertical: 13,
    alignItems: 'center',
  },
  reviewBtnText: { color: '#000', fontSize: 15, fontWeight: '900', letterSpacing: 0.4 },
  statTile: {
    width: '31%',
    backgroundColor: Colors.card,
    borderRadius: 14,
    padding: 12,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    gap: 4,
  },
  statTileValue: { color: Colors.text, fontSize: 18, fontWeight: '900' },
  statTileLabel: { color: Colors.textSecondary, fontSize: 11, fontWeight: '700' },
  filterResultsList: { gap: 10 },
  playerGroupCard: {
    backgroundColor: Colors.card,
    borderRadius: 16,
    padding: 14,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    gap: 10,
  },
  playerGroupHeader: { gap: 3 },
  playerGroupTitle: { color: Colors.text, fontSize: 16, fontWeight: '800' },
  playerGroupSummary: { color: Colors.textSecondary, fontSize: 12, fontWeight: '600' },
  playerPickRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
    paddingVertical: 8,
    borderTopWidth: 1,
    borderTopColor: Colors.borderSubtle,
  },
  playerPickMain: { flex: 1, gap: 2 },
  playerPickMeta: { color: Colors.textTertiary, fontSize: 11, fontWeight: '600' },
  playerPickLine: { color: Colors.text, fontSize: 13, fontWeight: '700' },
  playerPickResultWrap: { alignItems: 'flex-end' },
  playerPickResult: { fontSize: 12, fontWeight: '800' },
  playerGroupFooter: { color: Colors.textSecondary, fontSize: 11, fontWeight: '600' },
  tabToggle: {
    flexDirection: 'row',
    backgroundColor: Colors.card,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    padding: 3,
    alignSelf: 'stretch',
  },
  toggle: {
    flex: 1,
    paddingVertical: 9,
    paddingHorizontal: 14,
    borderRadius: 10,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
  },
  toggleActive: { backgroundColor: Colors.primary },
  toggleText: { fontSize: 14, fontWeight: '600', color: Colors.textSecondary },
  toggleTextActive: { color: '#000' },
  tabDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: Colors.primary },
  progressWrap: { gap: 6, marginTop: 2 },
  progressTrack: {
    height: 6,
    borderRadius: 999,
    backgroundColor: Colors.cardSecondary,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%' as unknown as number,
    borderRadius: 999,
    backgroundColor: Colors.primary,
  },
  progressText: { fontSize: 10, color: Colors.textTertiary, fontWeight: '600', textAlign: 'right' },

  recordBar: {
    marginHorizontal: 20, marginBottom: 8, backgroundColor: Colors.card,
    borderRadius: 10, borderWidth: 1, borderColor: Colors.border,
    paddingHorizontal: 12, paddingVertical: 8,
  },
  recordBarExpanded: {
    paddingVertical: 12, gap: 8,
  },
  recordLabel: { fontSize: 10, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 1.5 },
  recordCompactRow: {
    minHeight: 24, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
  },
  recordCompactTitle: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  recordCompactSummary: { fontSize: 12, fontWeight: '800', letterSpacing: 0.2 },
  recordSummaryMuted: { color: Colors.textTertiary },
  recordDetails: { gap: 8 },
  recordStats: { flexDirection: 'row', justifyContent: 'space-between' },
  recordStat: { alignItems: 'center', flex: 1 },
  recordVal: { fontSize: 18, fontWeight: '800', color: Colors.text },
  recordKey: { fontSize: 9, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.5, marginTop: 2 },

  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12, paddingHorizontal: 40, paddingTop: 60, paddingBottom: 40 },
  emptyIconWrap: {
    width: 72, height: 72, borderRadius: 36,
    backgroundColor: 'rgba(57,255,20,0.06)',
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.12)',
    alignItems: 'center', justifyContent: 'center',
    marginBottom: 4,
  },
  emptyTitle: { fontSize: 18, fontWeight: '700', color: Colors.text },
  emptySub: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center', lineHeight: 21 },
  emptyAction: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: Colors.primary, borderRadius: 10,
    paddingHorizontal: 20, paddingVertical: 11, marginTop: 6,
  },
  emptyActionText: { color: '#000', fontWeight: '800', fontSize: 14 },
  list: { paddingHorizontal: 12, paddingBottom: 40, gap: 6 },

  card: {
    backgroundColor: Colors.card, borderRadius: 12,
    paddingHorizontal: 12, paddingVertical: 7,
    borderWidth: 1, borderColor: Colors.borderSubtle, gap: 3,
    shadowColor: '#000', shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.12, shadowRadius: 3, elevation: 2,
  },
  cardWon: { borderColor: 'rgba(57,255,20,0.35)', shadowColor: 'rgba(57,255,20,0.15)' },
  cardLost: { borderColor: 'rgba(255,59,48,0.3)' },

  cardTopRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  cardRight: { flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 0 },
  cardPlayer: { fontSize: 13, fontWeight: '800', color: Colors.text, flex: 1, letterSpacing: 0.1 },
  cardMeta: { fontSize: 10, color: Colors.textTertiary, letterSpacing: 0.1, marginBottom: 1 },

  cardRow2: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 6 },
  cardRow2Left: { flex: 1, gap: 2 },
  inlineStats: { flexDirection: 'row', alignItems: 'center', gap: 10, flexShrink: 0 },
  inlineStat: { alignItems: 'center', gap: 1, minWidth: 28 },
  inlineVal: { fontSize: 15, fontWeight: '800', color: Colors.text },
  inlineLbl: { fontSize: 8, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.8 },

  liveBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: 'rgba(57,255,20,0.1)', borderRadius: 7,
    paddingHorizontal: 8, paddingVertical: 4,
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.25)',
  },
  liveDot: { width: 5, height: 5, borderRadius: 2.5, backgroundColor: Colors.primary },
  liveText: { fontSize: 10, color: Colors.primary, fontWeight: '800', letterSpacing: 0.5 },
  pendingBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: 'rgba(255,255,255,0.05)', borderRadius: 7,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.1)',
    paddingHorizontal: 8, paddingVertical: 4,
  },
  pendingText: { fontSize: 10, color: Colors.textSecondary, fontWeight: '600', letterSpacing: 0.5 },
  wonBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: Colors.primary, borderRadius: 7,
    paddingHorizontal: 8, paddingVertical: 4,
  },
  wonText: { fontSize: 10, color: '#000', fontWeight: '900', letterSpacing: 0.5 },
  lostBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: Colors.errorDim, borderRadius: 7,
    paddingHorizontal: 8, paddingVertical: 4,
    borderWidth: 1, borderColor: 'rgba(255,59,48,0.35)',
  },
  lostText: { fontSize: 10, color: Colors.error, fontWeight: '800', letterSpacing: 0.5 },
  pushBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: Colors.pushDim, borderRadius: 7,
    borderWidth: 1, borderColor: 'rgba(10,132,255,0.35)',
    paddingHorizontal: 8, paddingVertical: 4,
  },
  pushText: { fontSize: 10, color: Colors.push, fontWeight: '800', letterSpacing: 0.5 },
  dnpBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: Colors.dnpDim, borderRadius: 7,
    borderWidth: 1, borderColor: 'rgba(255,149,0,0.35)',
    paddingHorizontal: 8, paddingVertical: 4,
  },
  dnpText: { fontSize: 10, color: Colors.dnp, fontWeight: '800', letterSpacing: 0.5 },
  pickRow: { flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
  recPill: { paddingHorizontal: 7, paddingVertical: 2, borderRadius: 6 },
  recPillText: { fontSize: 10, fontWeight: '900', letterSpacing: 0.8 },
  pickDetail: { fontSize: 11, color: Colors.textSecondary, fontWeight: '500' },
  coinFlipBadge: { backgroundColor: Colors.cardSecondary, paddingHorizontal: 5, paddingVertical: 2, borderRadius: 5 },
  coinFlipText: { fontSize: 9, fontWeight: '800', color: Colors.textTertiary },
  confBadge: {
    paddingHorizontal: 5,
    paddingVertical: 2,
    borderRadius: 5,
    borderWidth: 1,
    backgroundColor: 'transparent',
  },
  confBadgeText: { fontSize: 9, fontWeight: '800', letterSpacing: 0.3 },

  trackBarOuter: {
    height: 6.5,
    backgroundColor: Colors.cardSecondary,
    borderRadius: 3.5,
    overflow: 'hidden',
    position: 'relative',
    marginTop: 3,
  },
  trackBarFill: {
    position: 'absolute',
    left: 0,
    top: 0,
    bottom: 0,
    borderRadius: 3.5,
  },
  trackBarMarker: {
    position: 'absolute',
    left: '50%',
    top: 0,
    bottom: 0,
    width: 1.5,
    backgroundColor: 'rgba(255,255,255,0.55)',
  },

  matchCtxBlock: {
    marginTop: 1,
    paddingTop: 3,
    borderTopWidth: 1,
    borderTopColor: Colors.borderSubtle,
  },
  paceWarningBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 6,
    paddingHorizontal: 8,
    paddingVertical: 6,
    borderRadius: 8,
    backgroundColor: Colors.dnpDim,
    borderWidth: 1,
    borderColor: 'rgba(255,149,0,0.35)',
  },
  paceWarningText: {
    flex: 1,
    fontSize: 11,
    fontWeight: '700',
    color: Colors.dnp,
    letterSpacing: 0.1,
  },
  matchCtxLine: {
    fontSize: 10,
    color: Colors.textTertiary,
    fontWeight: '600',
    letterSpacing: 0.1,
  },

  tapHint: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    paddingTop: 4, borderTopWidth: 1, borderTopColor: Colors.borderSubtle,
  },
  cardFooterWeb: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    alignItems: 'center',
    marginTop: 6,
  },
  tapHintText: { fontSize: 9, color: Colors.primary, fontWeight: '600', letterSpacing: 0.3 },

  // Settled story styles
  storyBlock: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 8,
    paddingVertical: 6,
    paddingHorizontal: 8,
    backgroundColor: 'rgba(255,255,255,0.03)',
    borderRadius: 6,
  },
  storyDot: {
    width: 5,
    height: 5,
    borderRadius: 2.5,
    marginRight: 8,
  },
  storyText: {
    flex: 1,
    fontSize: 11,
    color: Colors.textSecondary,
    fontWeight: '500',
    lineHeight: 15,
  },


  menuOverlay: { flex: 1, justifyContent: 'flex-start', alignItems: 'flex-end' },
  menuBackdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(0,0,0,0.6)' },
  menuSheet: {
    width: 220,
    marginTop: 90,
    marginRight: 16,
    backgroundColor: Colors.card,
    borderRadius: 16,
    padding: 14,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    shadowColor: '#000', shadowOffset: { width: 0, height: 8 }, shadowOpacity: 0.35, shadowRadius: 16, elevation: 10,
  },
  menuTitle: { fontSize: 13, fontWeight: '800', color: Colors.text, marginBottom: 12, marginLeft: 4 },
  menuGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  menuItem: { width: 84, alignItems: 'center', gap: 6, paddingVertical: 8 },
  menuIconWrap: {
    width: 42, height: 42, borderRadius: 12,
    backgroundColor: 'rgba(57,255,20,0.08)',
    alignItems: 'center', justifyContent: 'center',
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.15)',
  },
  menuItemLabel: { fontSize: 10, fontWeight: '700', color: Colors.text },
  modalBackdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.75)', justifyContent: 'flex-end' },
  socialSheet: {
    flex: 1,
    backgroundColor: Colors.background,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingTop: 16,
  },
  socialHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    marginBottom: 10,
  },
  socialTitle: { fontSize: 18, fontWeight: '800', color: Colors.text },
  closeBtn: {
    padding: 4,
  },

  // Live possession bar
  possBarBlock: {
    marginTop: 8,
  },
  possBarTrack: {
    height: 8,
    borderRadius: 4,
    flexDirection: 'row',
    overflow: 'hidden',
    backgroundColor: Colors.cardSecondary,
  },
  possBarHome: {
    height: 8,
  },
  possBarAway: {
    height: 8,
  },
  possBarLabels: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: 4,
  },
  possBarLabel: {
    fontSize: 10,
    color: Colors.textTertiary,
    fontWeight: '500',
  },
  trackBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    gap: 5,
    marginTop: 8,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 6,
    backgroundColor: 'rgba(57,255,20,0.10)',
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.25)',
  },
  trackBtnText: {
    fontSize: 10,
    color: Colors.primary,
    fontWeight: '800',
    letterSpacing: 0.3,
  },
});

const disclaimerStyles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.75)',
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 24,
  },
  sheet: {
    backgroundColor: '#111',
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.25)',
    padding: 24,
    width: '100%',
    maxWidth: 400,
  },
  iconRow: { alignItems: 'center', marginBottom: 12 },
  title: {
    color: Colors.primary,
    fontSize: 16,
    fontWeight: '800',
    textAlign: 'center',
    letterSpacing: 0.5,
    marginBottom: 14,
  },
  body: {
    color: 'rgba(255,255,255,0.75)',
    fontSize: 13,
    lineHeight: 20,
    marginBottom: 20,
  },
  bold: { fontWeight: '800', color: Colors.text },
  small: { color: 'rgba(255,255,255,0.35)', fontSize: 11 },
  btn: {
    backgroundColor: Colors.primary,
    borderRadius: 10,
    paddingVertical: 13,
    alignItems: 'center',
  },
  btnText: {
    color: '#000',
    fontWeight: '900',
    fontSize: 13,
    letterSpacing: 0.5,
  },
});
