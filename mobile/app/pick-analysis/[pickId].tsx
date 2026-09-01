import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Stack, router, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { fetchSportPickAnalysis, Pick } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

const MLB_PROPS: Record<string, string> = {
  hits: 'HITS',
  total_bases: 'TOTAL BASES',
  home_runs: 'HOME RUNS',
  rbis: 'RBIs',
  runs: 'RUNS',
  stolen_bases: 'STOLEN BASES',
  strikeouts: 'STRIKEOUTS',
  walks: 'WALKS',
  hits_runs_rbis: 'HITS + RUNS + RBIs',
  pitcher_strikeouts: 'PITCHER STRIKEOUTS',
  pitching_outs: 'PITCHING OUTS',
  innings_pitched: 'INNINGS PITCHED',
  earned_runs: 'EARNED RUNS',
  pitcher_hits_allowed: 'HITS ALLOWED',
  pitcher_walks: 'PITCHER WALKS',
  pitcher_fantasy_score: 'PITCHER FANTASY',
  hitter_fantasy_points: 'HITTER FANTASY',
};

const NFL_PROPS: Record<string, string> = {
  passing_yards: 'PASSING YARDS',
  passing_tds: 'PASSING TDs',
  interceptions: 'INTERCEPTIONS',
  completions: 'COMPLETIONS',
  attempts: 'PASS ATTEMPTS',
  rushing_yards: 'RUSHING YARDS',
  rushing_attempts: 'RUSH ATTEMPTS',
  rushing_tds: 'RUSHING TDs',
  receiving_yards: 'RECEIVING YARDS',
  receptions: 'RECEPTIONS',
  receiving_tds: 'RECEIVING TDs',
  targets: 'TARGETS',
  touchdowns: 'TOUCHDOWNS',
  fantasy_points: 'FANTASY POINTS',
};

type Analysis = Record<string, any>;

function num(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function fmt(value: unknown, digits = 1): string {
  const n = num(value);
  if (n == null) return '—';
  return Number.isInteger(n) ? String(n) : n.toFixed(digits);
}

function labelFor(sport: string, prop: string): string {
  const map = sport === 'mlb' ? MLB_PROPS : NFL_PROPS;
  return map[prop] || prop.replace(/_/g, ' ').toUpperCase();
}

function logValue(log: Record<string, any>, prop: string, sport: string): number | null {
  if (num(log.value) != null) return num(log.value);
  const keys = sport === 'mlb'
    ? prop.includes('strikeout') ? ['strikeouts', 'so'] : prop.includes('hit') ? ['hits'] : [prop]
    : [prop, prop.replace(/s$/, '')];
  for (const key of keys) {
    const value = num(log[key]);
    if (value != null) return value;
  }
  return null;
}

function Section({
  title,
  eyebrow,
  children,
}: {
  title: string;
  eyebrow?: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.section}>
      <View style={styles.sectionHeading}>
        <View style={styles.headingRule} />
        <View style={{ flex: 1 }}>
          {eyebrow ? <Text style={styles.eyebrow}>{eyebrow}</Text> : null}
          <Text style={styles.sectionTitle}>{title}</Text>
        </View>
      </View>
      {children}
    </View>
  );
}

function Metric({ label, value, accent = Colors.text }: { label: string; value: string; accent?: string }) {
  return (
    <View style={styles.metric}>
      <Text style={[styles.metricValue, { color: accent }]}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

function DetailRow({ label, value }: { label: string; value: unknown }) {
  if (value == null || value === '') return null;
  return (
    <View style={styles.detailRow}>
      <Text style={styles.detailLabel}>{label}</Text>
      <Text style={styles.detailValue} numberOfLines={2}>{String(value)}</Text>
    </View>
  );
}

export default function PickAnalysisPage() {
  const { session } = useAuth();
  const params = useLocalSearchParams<{ pickId?: string; sport?: string }>();
  const pickId = String(params.pickId || '');
  const sport = String(params.sport || '').toLowerCase();
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    if (!session || !pickId) {
      setLoading(false);
      setError('This saved pick could not be opened.');
      return () => { cancelled = true; };
    }
    setLoading(true);
    setError('');
    fetchSportPickAnalysis(session.email, session.token, { pickId, sport })
      .then((response) => {
        if (cancelled) return;
        if (!response.found || !response.analysis) {
          setError('No saved analysis is available for this pick.');
          setAnalysis(null);
        } else {
          setAnalysis(response.analysis as Analysis);
        }
      })
      .catch(() => {
        if (!cancelled) setError('Analysis is temporarily unavailable.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [session, pickId, sport]);

  const normalizedSport = String(analysis?.sport || sport || 'sports').toLowerCase();
  const isMlb = normalizedSport === 'mlb';
  const sportLabel = isMlb ? 'MLB' : normalizedSport === 'nfl' ? 'NFL' : normalizedSport.toUpperCase();
  const prop = String(analysis?.propType || '');
  const propLabel = labelFor(normalizedSport, prop);
  const recommendation = String(analysis?.recommendation || 'PASS').toUpperCase();
  const recommendationColor = recommendation === 'OVER' ? Colors.success
    : recommendation === 'UNDER' ? Colors.error : Colors.textSecondary;
  const matchup = analysis?.matchupOverview || {};
  const logs = useMemo(() => {
    const raw = Array.isArray(analysis?.gameLogs)
      ? analysis.gameLogs
      : Array.isArray(analysis?.playerGameLogs?.games)
        ? analysis.playerGameLogs.games
        : [];
    return raw.slice(0, 10) as Record<string, any>[];
  }, [analysis]);
  const factors = Array.isArray(analysis?.analysisFactors) ? analysis.analysisFactors : [];
  const rolePacket = analysis?.roleEvidencePacket || analysis?.roleEvidence || {};
  const tacticalText = analysis?.tacticalBreakdown || analysis?.reasoning || analysis?.sharpSummary;
  const modeledProbability = recommendation === 'UNDER' ? analysis?.pUnder : analysis?.pOver;
  const evidenceStatus = analysis?.analysisStatus === 'complete' ? 'SNAPSHOT COMPLETE' : 'LIMITED SNAPSHOT';
  const result = String(analysis?.result || '').toUpperCase();

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={styles.topBar}>
        <Pressable onPress={() => router.back()} style={styles.backButton} accessibilityLabel="Back to My Picks">
          <Ionicons name="arrow-back" size={21} color={Colors.text} />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.topEyebrow}>{sportLabel} · SAVED ANALYSIS</Text>
          <Text style={styles.topTitle}>Model read</Text>
        </View>
        <View style={[styles.sourcePill, { borderColor: analysis ? Colors.primary + '80' : Colors.borderSubtle }]}>
          <View style={[styles.sourceDot, { backgroundColor: analysis ? Colors.primary : Colors.textTertiary }]} />
          <Text style={styles.sourceText}>{analysis ? 'DURABLE' : 'LOADING'}</Text>
        </View>
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={Colors.primary} />
          <Text style={styles.centerText}>Loading saved analysis…</Text>
        </View>
      ) : error || !analysis ? (
        <View style={styles.center}>
          <Ionicons name="analytics-outline" size={36} color={Colors.textTertiary} />
          <Text style={styles.errorTitle}>Analysis unavailable</Text>
          <Text style={styles.centerText}>{error || 'No analysis found for this pick.'}</Text>
          <Pressable onPress={() => router.back()} style={styles.secondaryButton}>
            <Text style={styles.secondaryButtonText}>RETURN TO MY PICKS</Text>
          </Pressable>
        </View>
      ) : (
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          <View style={styles.identityCard}>
            <View style={styles.identityTop}>
              <View style={{ flex: 1 }}>
                <Text style={styles.identityName} numberOfLines={1}>{analysis.playerName || 'Player'}</Text>
                <Text style={styles.identitySub}>
                  {[analysis.teamName, analysis.position || analysis.playerRole, analysis.opponentName ? `vs ${analysis.opponentName}` : null]
                    .filter(Boolean).join(' · ')}
                </Text>
              </View>
              <View style={[styles.recBadge, { borderColor: recommendationColor + '90', backgroundColor: recommendationColor + '15' }]}>
                <Text style={[styles.recText, { color: recommendationColor }]}>{recommendation}</Text>
              </View>
            </View>
            <Text style={styles.market}>{propLabel} · LINE {fmt(analysis.line)}</Text>
            <View style={styles.metricsRow}>
              <Metric label="PROJECTION" value={fmt(analysis.projection ?? analysis.projectedValue)} accent={Colors.primary} />
              <Metric label="MODEL PROB" value={modeledProbability != null ? `${fmt(modeledProbability, 0)}%` : '—'} accent={recommendationColor} />
              <Metric label="CONFIDENCE" value={analysis.confidenceScore != null ? `${fmt(analysis.confidenceScore, 0)}%` : '—'} />
            </View>
            {result ? (
              <View style={styles.resultRow}>
                <Text style={styles.resultLabel}>SETTLEMENT</Text>
                <Text style={[styles.resultValue, { color: result === 'HIT' ? Colors.success : result === 'MISS' ? Colors.error : Colors.textSecondary }]}>
                  {result}{analysis.actualValue != null ? ` · ${fmt(analysis.actualValue)}` : ''}
                </Text>
              </View>
            ) : null}
          </View>

          <Section title="The read" eyebrow="DETERMINISTIC DECISION">
            <View style={styles.readCard}>
              <Text style={styles.readText}>
                {tacticalText || `${recommendation} ${propLabel.toLowerCase()} at ${fmt(analysis.line)}. The saved model projection is ${fmt(analysis.projection ?? analysis.projectedValue)}.`}
              </Text>
              <Text style={styles.provenance}>Source: {analysis.explanationSource || 'saved sport model snapshot'} · {evidenceStatus}</Text>
            </View>
          </Section>

          <Section title="Matchup context" eyebrow={isMlb ? 'BASEBALL ENVIRONMENT' : 'FOOTBALL ENVIRONMENT'}>
            <View style={styles.detailCard}>
              <DetailRow label="MATCHUP" value={`${matchup.homeTeam || analysis.homeTeam || analysis.teamName || 'Home'}  vs  ${matchup.awayTeam || analysis.awayTeam || analysis.opponentName || 'Away'}`} />
              <DetailRow label="VENUE" value={analysis.venue ? String(analysis.venue).toUpperCase() : matchup.playerIsHome != null ? (matchup.playerIsHome ? 'HOME' : 'AWAY') : null} />
              <DetailRow label={isMlb ? 'GAME TOTAL' : 'GAME TOTAL'} value={analysis.gameTotalUsed ?? analysis.gameTotal} />
              {isMlb ? (
                <>
                  <DetailRow label="PITCHER" value={analysis.pitcherName || null} />
                  <DetailRow label="PLATOON" value={analysis.batterHandedness && analysis.pitcherHandedness ? `${analysis.batterHandedness} batter vs ${analysis.pitcherHandedness} pitcher` : null} />
                  <DetailRow label="LINEUP SPOT" value={analysis.lineupSpot} />
                  <DetailRow label="PITCHER ERA" value={analysis.pitcherEra != null ? fmt(analysis.pitcherEra, 2) : null} />
                </>
              ) : (
                <>
                  <DetailRow label="OPPONENT DEFENSE" value={analysis.oppRankPercentile != null ? `${fmt(Number(analysis.oppRankPercentile) * 100, 0)}th percentile difficulty` : null} />
                  <DetailRow label="REST" value={analysis.restDays != null ? `${analysis.restDays} days` : null} />
                </>
              )}
              <DetailRow label="KEY FACTOR" value={matchup.keyMatchupFactor || null} />
            </View>
          </Section>

          <Section title="Recent game evidence" eyebrow={`${logs.length} SAVED ${isMlb ? 'GAMES' : 'GAMES'}`}>
            {logs.length > 0 ? (
              <View style={styles.logsCard}>
                <View style={styles.logHeader}>
                  <Text style={styles.logHeaderText}>DATE</Text>
                  <Text style={styles.logHeaderText}>OPPONENT</Text>
                  <Text style={[styles.logHeaderText, { textAlign: 'right' }]}>{propLabel}</Text>
                </View>
                {logs.map((log, index) => {
                  const value = logValue(log, prop, normalizedSport);
                  const opponent = log.opponent || log.opponentName || log.opponent_team || 'Opponent';
                  const date = log.date || log.gameDate || log.game_date || '—';
                  const hit = num(analysis.line) != null && value != null
                    ? recommendation === 'OVER' ? value > Number(analysis.line) : recommendation === 'UNDER' ? value < Number(analysis.line) : false
                    : null;
                  return (
                    <View key={`${date}-${index}`} style={styles.logRow}>
                      <Text style={styles.logDate}>{String(date).slice(0, 10)}</Text>
                      <Text style={styles.logOpponent} numberOfLines={1}>{String(opponent)}</Text>
                      <Text style={[styles.logValue, { color: hit === true ? Colors.success : hit === false ? Colors.error : Colors.text }]}>
                        {fmt(value)}
                      </Text>
                    </View>
                  );
                })}
              </View>
            ) : (
              <View style={styles.emptyCard}>
                <Text style={styles.emptyText}>Recent game logs were not captured for this saved pick.</Text>
              </View>
            )}
          </Section>

          <Section title="Model factors" eyebrow="WHAT THE PROJECTION USED">
            {factors.length > 0 ? (
              <View style={styles.factorList}>
                {factors.slice(0, 9).map((factor: any, index: number) => (
                  <View key={String(factor.id || index)} style={styles.factorRow}>
                    <Ionicons name={factor.status === 'unavailable' ? 'remove-circle-outline' : 'checkmark-circle-outline'} size={17} color={factor.status === 'unavailable' ? Colors.textTertiary : Colors.primary} />
                    <View style={{ flex: 1 }}>
                      <Text style={styles.factorTitle}>{factor.title || factor.id || 'Model factor'}</Text>
                      <Text style={styles.factorDetail}>{factor.detail || factor.summary || factor.impact || 'Captured in the saved model snapshot.'}</Text>
                    </View>
                  </View>
                ))}
              </View>
            ) : (
              <View style={styles.detailCard}>
                <DetailRow label="PRIOR MEAN" value={fmt(analysis.priorMean)} />
                <DetailRow label="MOMENTUM" value={fmt(analysis.momentum ?? analysis.momentumMean)} />
                <DetailRow label="SAMPLE" value={analysis.sampleSize ?? analysis.historyGameCount} />
                <DetailRow label="SAFETY" value={analysis.safetyRating || 'Not captured'} />
              </View>
            )}
          </Section>

          <Section title="Role and evidence quality" eyebrow="SPORT-SPECIFIC CONTEXT">
            <View style={styles.detailCard}>
              <DetailRow label="ROLE" value={analysis.playerRole || analysis.role || analysis.position} />
              <DetailRow label="POSITION" value={analysis.position || analysis.playerPosition} />
              <DetailRow label="ROLE STATUS" value={rolePacket.status || rolePacket.context || null} />
              <DetailRow label="ROLE SAMPLE" value={rolePacket.sampleSize || rolePacket.sample || null} />
              <DetailRow label="EVIDENCE" value={analysis.evidenceQuality?.level || evidenceStatus} />
              <DetailRow label="SAFETY" value={analysis.safetyRating || null} />
              {analysis.qualityConfidenceCapped ? <Text style={styles.limitedNote}>Confidence was capped because the saved evidence was limited.</Text> : null}
            </View>
          </Section>

          <View style={styles.footerNote}>
            <Ionicons name="lock-closed-outline" size={14} color={Colors.textTertiary} />
            <Text style={styles.footerText}>This page reads the saved prediction snapshot. Missing evidence is shown as unavailable rather than estimated.</Text>
          </View>
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  topBar: {
    paddingTop: Platform.OS === 'web' ? 24 : 52, paddingHorizontal: 18, paddingBottom: 14,
    flexDirection: 'row', alignItems: 'center', gap: 12, borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle,
  },
  backButton: { width: 36, height: 36, borderRadius: 10, justifyContent: 'center', alignItems: 'center', backgroundColor: Colors.cardSecondary },
  topEyebrow: { color: Colors.primary, fontSize: 9, fontWeight: '900', letterSpacing: 1.2 },
  topTitle: { color: Colors.text, fontSize: 21, fontWeight: '900', marginTop: 2 },
  sourcePill: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 8, paddingVertical: 5, borderWidth: 1, borderRadius: 20 },
  sourceDot: { width: 6, height: 6, borderRadius: 3 },
  sourceText: { color: Colors.textSecondary, fontSize: 8, fontWeight: '900', letterSpacing: 0.6 },
  content: { padding: 16, paddingBottom: 38, maxWidth: 720, width: '100%', alignSelf: 'center' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 34, gap: 10 },
  centerText: { color: Colors.textSecondary, fontSize: 13, textAlign: 'center', lineHeight: 19 },
  errorTitle: { color: Colors.text, fontSize: 18, fontWeight: '900', marginTop: 5 },
  secondaryButton: { marginTop: 10, paddingHorizontal: 15, paddingVertical: 11, borderWidth: 1, borderColor: Colors.border, borderRadius: 9 },
  secondaryButtonText: { color: Colors.primary, fontSize: 10, fontWeight: '900', letterSpacing: 0.7 },
  identityCard: { backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border, borderRadius: 16, padding: 15 },
  identityTop: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  identityName: { color: Colors.text, fontSize: 22, fontWeight: '900', letterSpacing: -0.4 },
  identitySub: { color: Colors.textSecondary, fontSize: 11, marginTop: 4 },
  recBadge: { paddingHorizontal: 11, paddingVertical: 7, borderRadius: 8, borderWidth: 1 },
  recText: { fontSize: 12, fontWeight: '900', letterSpacing: 0.8 },
  market: { color: Colors.textTertiary, fontSize: 10, fontWeight: '800', letterSpacing: 0.5, marginTop: 14 },
  metricsRow: { flexDirection: 'row', marginTop: 15, borderTopWidth: 1, borderTopColor: Colors.borderSubtle, paddingTop: 12 },
  metric: { flex: 1, alignItems: 'center' },
  metricValue: { fontSize: 18, fontWeight: '900' },
  metricLabel: { color: Colors.textTertiary, fontSize: 8, fontWeight: '900', letterSpacing: 0.5, marginTop: 3 },
  resultRow: { marginTop: 11, paddingTop: 10, borderTopWidth: 1, borderTopColor: Colors.borderSubtle, flexDirection: 'row', justifyContent: 'space-between' },
  resultLabel: { color: Colors.textTertiary, fontSize: 9, fontWeight: '900', letterSpacing: 0.6 },
  resultValue: { fontSize: 10, fontWeight: '900' },
  section: { marginTop: 22 },
  sectionHeading: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 9 },
  headingRule: { width: 3, height: 28, backgroundColor: Colors.primary, borderRadius: 2 },
  eyebrow: { color: Colors.primary, fontSize: 8, fontWeight: '900', letterSpacing: 1 },
  sectionTitle: { color: Colors.text, fontSize: 17, fontWeight: '900', marginTop: 2 },
  readCard: { backgroundColor: Colors.cardSecondary, borderWidth: 1, borderColor: Colors.borderSubtle, borderRadius: 11, padding: 13 },
  readText: { color: Colors.text, fontSize: 13, lineHeight: 20 },
  provenance: { color: Colors.textTertiary, fontSize: 9, lineHeight: 14, marginTop: 10 },
  detailCard: { backgroundColor: Colors.cardSecondary, borderWidth: 1, borderColor: Colors.borderSubtle, borderRadius: 11, paddingHorizontal: 13 },
  detailRow: { minHeight: 37, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 14, borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle },
  detailLabel: { color: Colors.textTertiary, fontSize: 9, fontWeight: '900', letterSpacing: 0.5, flexShrink: 0 },
  detailValue: { color: Colors.text, fontSize: 11, fontWeight: '700', textAlign: 'right', flex: 1 },
  logsCard: { backgroundColor: Colors.cardSecondary, borderWidth: 1, borderColor: Colors.borderSubtle, borderRadius: 11, paddingHorizontal: 12, paddingBottom: 3 },
  logHeader: { flexDirection: 'row', paddingVertical: 9, borderBottomWidth: 1, borderBottomColor: Colors.border },
  logHeaderText: { color: Colors.textTertiary, fontSize: 8, fontWeight: '900', flex: 1, letterSpacing: 0.4 },
  logRow: { flexDirection: 'row', alignItems: 'center', minHeight: 34, borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle },
  logDate: { color: Colors.textSecondary, fontSize: 10, flex: 1 },
  logOpponent: { color: Colors.text, fontSize: 10, fontWeight: '700', flex: 1 },
  logValue: { fontSize: 12, fontWeight: '900', flex: 1, textAlign: 'right' },
  emptyCard: { backgroundColor: Colors.cardSecondary, borderWidth: 1, borderColor: Colors.borderSubtle, borderRadius: 11, padding: 14 },
  emptyText: { color: Colors.textTertiary, fontSize: 11, lineHeight: 17 },
  factorList: { backgroundColor: Colors.cardSecondary, borderWidth: 1, borderColor: Colors.borderSubtle, borderRadius: 11, paddingHorizontal: 13 },
  factorRow: { flexDirection: 'row', gap: 9, paddingVertical: 11, borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle },
  factorTitle: { color: Colors.text, fontSize: 11, fontWeight: '800' },
  factorDetail: { color: Colors.textSecondary, fontSize: 10, lineHeight: 15, marginTop: 2 },
  limitedNote: { color: '#F59E0B', fontSize: 10, lineHeight: 15, paddingVertical: 10 },
  footerNote: { flexDirection: 'row', gap: 7, alignItems: 'flex-start', marginTop: 25, paddingHorizontal: 3 },
  footerText: { color: Colors.textTertiary, fontSize: 10, lineHeight: 15, flex: 1 },
});