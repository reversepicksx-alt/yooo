/**
 * AnalysisCards.tsx — Shared render helpers for AI analysis data cards.
 *
 * Used by both picks.tsx (analysis modal) and scan.tsx (immediate result view)
 * so the two surfaces always show identical cards from the same code.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Svg, { Circle, Line, Rect } from 'react-native-svg';
import Colors from '@/constants/colors';
import { AnalysisFactor, TheStatsApiEnrichment } from '@/lib/api';

export const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Attempts', shots: 'Shots', shots_on_target: 'SOT',
  goals: 'Goals', assists: 'Assists', key_passes: 'Key Passes',
  tackles: 'Tackles', saves: 'Saves', dribbles: 'Dribbles', crosses: 'Crosses',
  interceptions: 'Interceptions', blocks: 'Blocks', fouls_drawn: 'Fouls Drawn',
  fouls_committed: 'Fouls', clearances: 'Clearances', duels_won: 'Duels Won',
  yellow_cards: 'Yellow Cards', shots_assisted: 'Shot Assists', passes: 'Passes',
};

/** Evidence-only TheStatsAPI spatial/tactical context. */
export function renderTheStatsApiEnrichment(
  data: Record<string, unknown> | null,
  options?: { legacy?: boolean },
) {
  if (!data) return null;
  const enrichment = (
    (data as any)?.thestatsapiEnrichment
    ?? (data as any)?.modelInputSnapshot?.thestatsapi
  ) as TheStatsApiEnrichment | undefined;
  // Older saved picks predate this integration. Keep that state explicit
  // instead of implying that a missing payload means zero provider evidence.
  if (!enrichment && !options?.legacy) return null;

  const heatmap = enrichment?.heatmap;
  const shotmap = enrichment?.shotmap;
  const tactics = enrichment?.opponentTactics;
  const verified = enrichment?.fixtureVerification?.status === 'verified';
  const currentMatch = enrichment?.currentMatch;
  const liveEvent = currentMatch?.timeline?.[currentMatch.timeline.length - 1];
  const points = heatmap?.points ?? [];
  const shots = shotmap?.shots ?? [];
  const measured = heatmap?.status === 'measured' || shotmap?.status === 'measured';
  const dotColor = '#67E8F9';
  const shotColor = '#FBBF24';
  const pitchW = 320;
  const pitchH = 178;

  return (
    <View style={[aStyles.proCard, { borderColor: '#67E8F944' }]}>
      <View style={aStyles.proCardHeader}>
        <View style={[aStyles.proCardPill, { backgroundColor: '#67E8F918' }]}>
          <Text style={[aStyles.proCardPillText, { color: dotColor }]}>SPATIAL INTEL</Text>
        </View>
        <Text style={aStyles.proCardTitle} numberOfLines={1}>
          TheStatsAPI · analysis only
        </Text>
        <Ionicons name={verified ? 'shield-checkmark-outline' : 'information-circle-outline'} size={13} color={verified ? Colors.success : Colors.textTertiary} />
      </View>

      {!verified ? (
        <Text style={aStyles.proCardNote}>
          {options?.legacy
            ? 'TheStatsAPI data was unavailable when this pick was created. Predictions and settlement use API-Football.'
            : 'Unavailable for this verified fixture. Predictions and settlement use API-Football.'}
        </Text>
      ) : (
        <>
          <View style={aStyles.tsaMetaRow}>
            <Text style={aStyles.tsaMetaText}>
              {heatmap?.status === 'measured'
                ? `${heatmap.sampleSize ?? points.length} observed touch locations`
                : `Touch heatmap unavailable${heatmap?.status ? ` · ${heatmap.status}` : ''}`}
            </Text>
            {shotmap?.status === 'measured' ? (
              <Text style={aStyles.tsaMetaText}>{shotmap.sampleSize ?? shots.length} shots</Text>
            ) : (
              <Text style={aStyles.tsaMetaText}>Shotmap unavailable</Text>
            )}
          </View>
          {measured ? (
            <View style={aStyles.tsaPitchWrap}>
              <Svg width={pitchW} height={pitchH} viewBox={`0 0 ${pitchW} ${pitchH}`}>
                <Rect x={0} y={0} width={pitchW} height={pitchH} rx={8} fill="#071A18" stroke="#1F4A45" strokeWidth={1} />
                <Line x1={pitchW / 2} y1={0} x2={pitchW / 2} y2={pitchH} stroke="#1F4A45" strokeWidth={1} />
                <Circle cx={pitchW / 2} cy={pitchH / 2} r={22} fill="none" stroke="#1F4A45" strokeWidth={1} />
                <Rect x={0} y={pitchH / 2 - 34} width={45} height={68} fill="none" stroke="#1F4A45" strokeWidth={1} />
                <Rect x={pitchW - 45} y={pitchH / 2 - 34} width={45} height={68} fill="none" stroke="#1F4A45" strokeWidth={1} />
                {points.map((point, index) => (
                  <Circle
                    key={`tsa-touch-${index}`}
                    cx={(Number(point.x) / 100) * pitchW}
                    cy={(Number(point.y) / 100) * pitchH}
                    r={Math.max(2, Math.min(6, 2 + Number(point.count ?? 0) * 0.15))}
                    fill={dotColor}
                    opacity={0.18 + Math.min(0.62, (Number(point.count ?? 1) / 10))}
                  />
                ))}
                {shots.map((shot, index) => (
                  shot.x != null && shot.y != null ? (
                    <Circle
                      key={`tsa-shot-${index}`}
                      cx={(Number(shot.x) / 100) * pitchW}
                      cy={(Number(shot.y) / 100) * pitchH}
                      r={shot.isGoal ? 4.5 : 3}
                      fill={shot.isGoal ? Colors.success : shotColor}
                      stroke="#071A18"
                      strokeWidth={1}
                    />
                  ) : null
                ))}
              </Svg>
            </View>
          ) : null}
          <Text style={aStyles.proCardNote}>
            {heatmap?.status === 'measured'
              ? 'Finite touch locations observed in the provider sample — not continuous player tracking.'
              : 'No usable spatial sample was returned for this player.'}
          </Text>
          {tactics?.formation ? (
            <View style={aStyles.tsaFormationRow}>
              <Ionicons name="git-network-outline" size={12} color={Colors.textTertiary} />
              <Text style={aStyles.proCardNote}>
                Opponent shape: {tactics.formation}{tactics.confirmed ? ' · confirmed XI' : ''}
              </Text>
            </View>
          ) : (
            <Text style={aStyles.proCardNote}>Opponent formation unavailable.</Text>
          )}
          {currentMatch?.status === 'measured' || (currentMatch?.timeline?.length ?? 0) > 0 ? (
            <View style={aStyles.tsaLiveBox}>
              <View style={aStyles.tsaFormationRow}>
                <Ionicons name="radio-outline" size={12} color="#FB7185" />
                <Text style={[aStyles.proCardNote, { color: '#FB7185', fontWeight: '800' }]}>
                  CURRENT MATCH · LIVE PROVIDER CONTEXT
                </Text>
              </View>
              {liveEvent ? (
                <Text style={aStyles.proCardNote}>
                  Latest event{liveEvent.minute != null ? ` · ${liveEvent.minute}'` : ''}
                  {liveEvent.type ? ` · ${String(liveEvent.type).replace(/_/g, ' ')}` : ''}
                  {liveEvent.player ? ` · ${liveEvent.player}` : ''}
                  {liveEvent.team ? ` (${liveEvent.team})` : ''}
                </Text>
              ) : (
                <Text style={aStyles.proCardNote}>Live aggregate match statistics measured; no timeline event returned.</Text>
              )}
            </View>
          ) : currentMatch?.status === 'not_live' ? null : (
            <Text style={aStyles.proCardNote}>Current-match live context unavailable.</Text>
          )}
        </>
      )}
    </View>
  );
}

/** Evidence Summary ribbon — compact data-quality scorecard. */
export function renderEvidenceSummary(data: Record<string, unknown> | null) {
  if (!data) return null;
  const snapshot = (data as any)?.modelInputSnapshot?.sampleCounts ?? {};
  const factors: AnalysisFactor[] = (data as any)?.analysisFactors ?? [];
  const h2h = (data as any)?.h2hPlayerStats ?? {};
  const evFactor = factors.find((f) => f.id === 'evidence_quality');
  const level = (evFactor?.value as any)?.level ?? 'unknown';
  const score = (evFactor?.value as any)?.score;
  const applied = (evFactor?.value as any)?.appliedGroups ?? 0;
  const levelColor =
    level === 'high' ? Colors.success : level === 'medium' ? '#F59E0B' : Colors.textSecondary;
  const playerLogs = snapshot.playerLogs ?? 0;
  const opp = (snapshot.h2hPlayerGames ?? 0) + (snapshot.comparableGames ?? 0);
  const possObs = snapshot.possessionObservations ?? 0;
  const h2hApps = h2h.sampleSize ?? 0;
  if (playerLogs === 0 && opp === 0 && possObs === 0 && score == null) return null;
  return (
    <View style={aStyles.evidenceRow}>
      {score != null && (
        <View style={[aStyles.evidenceCell, { borderColor: levelColor + '66' }]}>
          <Text style={[aStyles.evidenceCellValue, { color: levelColor }]}>{score}</Text>
          <Text style={aStyles.evidenceCellLabel}>EV SCORE</Text>
        </View>
      )}
      {playerLogs > 0 && (
        <View style={aStyles.evidenceCell}>
          <Text style={aStyles.evidenceCellValue}>{playerLogs}</Text>
          <Text style={aStyles.evidenceCellLabel}>GAME LOGS</Text>
        </View>
      )}
      {h2hApps > 0 && (
        <View style={aStyles.evidenceCell}>
          <Text style={aStyles.evidenceCellValue}>{h2hApps}</Text>
          <Text style={aStyles.evidenceCellLabel}>H2H APPS</Text>
        </View>
      )}
      {opp > 0 && (
        <View style={aStyles.evidenceCell}>
          <Text style={aStyles.evidenceCellValue}>{opp}</Text>
          <Text style={aStyles.evidenceCellLabel}>COMP GAMES</Text>
        </View>
      )}
      {possObs > 0 && (
        <View style={aStyles.evidenceCell}>
          <Text style={aStyles.evidenceCellValue}>{possObs}</Text>
          <Text style={aStyles.evidenceCellLabel}>POSS OBS</Text>
        </View>
      )}
      {applied > 0 && (
        <View style={aStyles.evidenceCell}>
          <Text style={[aStyles.evidenceCellValue, { color: Colors.primary }]}>{applied}/9</Text>
          <Text style={aStyles.evidenceCellLabel}>APPLIED</Text>
        </View>
      )}
    </View>
  );
}

/** Opponent Defensive Profile card — how the opponent concedes to this position/prop. */
export function renderOpponentDefProfile(
  data: Record<string, unknown> | null,
  pick: { opponentName?: string | null; line?: number | null } | null,
) {
  if (!data) return null;
  const prof = (data as any)?.opponentDefensiveProfile;
  if (!prof || prof.sampleSize < 2) return null;
  const delta = prof.vsPlayerSeasonAvg as number | null;
  const favorable = prof.isFavorable as boolean | null;
  const accentColor =
    favorable == null ? Colors.textSecondary : favorable ? Colors.success : Colors.error;
  const arrow =
    delta == null ? '–' : delta > 0 ? `+${delta.toFixed(1)}%` : `${delta.toFixed(1)}%`;
  const prop =
    PROP_LABELS[prof.propType ?? ''] ?? (prof.propType ?? '').replace(/_/g, ' ');
  return (
    <View style={aStyles.proCard}>
      <View style={aStyles.proCardHeader}>
        <View style={[aStyles.proCardPill, { backgroundColor: accentColor + '18' }]}>
          <Text style={[aStyles.proCardPillText, { color: accentColor }]}>OPP PROFILE</Text>
        </View>
        <Text style={aStyles.proCardTitle} numberOfLines={1}>
          {prof.opponent}
        </Text>
      </View>
      <View style={aStyles.proCardMetrics}>
        <View style={aStyles.proCardMetric}>
          <Text style={[aStyles.proCardMetricValue, { color: accentColor }]}>
            {Number(prof.avgAllowed).toFixed(1)}
          </Text>
          <Text style={aStyles.proCardMetricLabel}>{prop.toUpperCase()} ALLOWED</Text>
        </View>
        {prof.playerSeasonAvg != null && (
          <View style={aStyles.proCardMetric}>
            <Text style={aStyles.proCardMetricValue}>
              {Number(prof.playerSeasonAvg).toFixed(1)}
            </Text>
            <Text style={aStyles.proCardMetricLabel}>PLAYER AVG</Text>
          </View>
        )}
        <View style={aStyles.proCardMetric}>
          <Text style={[aStyles.proCardMetricValue, { color: accentColor }]}>{arrow}</Text>
          <Text style={aStyles.proCardMetricLabel}>VS SEASON</Text>
        </View>
        <View style={aStyles.proCardMetric}>
          <Text style={aStyles.proCardMetricValue}>{prof.sampleSize}</Text>
          <Text style={aStyles.proCardMetricLabel}>FIXTURES</Text>
        </View>
      </View>
      <Text style={aStyles.proCardNote}>
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

/** Matchup & Possession card — expected possession bar + key matchup factor. */
export function renderMatchupPossession(
  data: Record<string, unknown> | null,
  pick: { venue?: string; teamName?: string; opponentName?: string | null } | null,
) {
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
  const possColor =
    teamPoss != null && oppPoss != null
      ? teamPoss > oppPoss + 5
        ? Colors.success
        : teamPoss < oppPoss - 5
        ? '#F59E0B'
        : Colors.textSecondary
      : Colors.textSecondary;
  return (
    <View style={aStyles.proCard}>
      <View style={aStyles.proCardHeader}>
        <View style={[aStyles.proCardPill, { backgroundColor: '#60A5FA18' }]}>
          <Text style={[aStyles.proCardPillText, { color: '#60A5FA' }]}>MATCHUP</Text>
        </View>
        {gt ? (
          <Text style={aStyles.proCardTitle} numberOfLines={1}>
            {(gt as string).replace(/_/g, ' ').toUpperCase()}
          </Text>
        ) : null}
      </View>
      {hasPoss && teamPoss != null && oppPoss != null && (
        <View style={aStyles.possRow}>
          <View style={aStyles.possTeam}>
            <Text style={[aStyles.possValue, { color: possColor }]}>
              {Number(teamPoss).toFixed(0)}%
            </Text>
            <Text style={aStyles.possLabel} numberOfLines={1}>
              {(pick?.teamName ?? 'HOME').split(' ').pop()?.toUpperCase()}
            </Text>
          </View>
          <View style={aStyles.possBar}>
            <View
              style={[
                aStyles.possBarFill,
                { width: `${teamPoss}%` as any, backgroundColor: possColor },
              ]}
            />
          </View>
          <View style={aStyles.possTeam}>
            <Text style={aStyles.possValue}>{Number(oppPoss).toFixed(0)}%</Text>
            <Text style={aStyles.possLabel} numberOfLines={1}>
              {(pick?.opponentName ?? 'AWAY').split(' ').pop()?.toUpperCase()}
            </Text>
          </View>
        </View>
      )}
      {kmf ? <Text style={aStyles.proCardNote}>{kmf}</Text> : null}
      {ss ? (
        <Text style={[aStyles.proCardNote, { color: Colors.textSecondary, marginTop: 4 }]}>
          {ss}
        </Text>
      ) : null}
    </View>
  );
}

/** H2H Intelligence card — historical head-to-head stats vs the same opponent. */
export function renderH2HIntelligence(
  data: Record<string, unknown> | null,
  pick: { opponentName?: string | null; line?: number | null } | null,
) {
  if (!data) return null;
  const h2h = (data as any)?.h2hPlayerStats;
  if (!h2h || !h2h.sampleSize) return null;
  const matches: any[] = h2h.matches ?? [];
  const avg: number | undefined = h2h.avgVsOpponent;
  const trend: string | undefined = h2h.trendDirection;
  const trendColor =
    trend === 'improving'
      ? Colors.success
      : trend === 'declining'
      ? Colors.error
      : Colors.textSecondary;
  const trendIcon: any =
    trend === 'improving' ? 'trending-up' : trend === 'declining' ? 'trending-down' : 'remove';
  const vhr = h2h.venueHitRate;
  const prop =
    PROP_LABELS[h2h.targetProp ?? ''] ?? (h2h.targetProp ?? '').replace(/_/g, ' ');
  return (
    <View style={aStyles.proCard}>
      <View style={aStyles.proCardHeader}>
        <View style={[aStyles.proCardPill, { backgroundColor: Colors.primary + '18' }]}>
          <Text style={[aStyles.proCardPillText, { color: Colors.primary }]}>H2H</Text>
        </View>
        <Text style={aStyles.proCardTitle} numberOfLines={1}>
          {h2h.sampleSize} app{h2h.sampleSize !== 1 ? 's' : ''} vs {pick?.opponentName}
          {h2h.seasonsCovered ? ` · ${h2h.seasonsCovered.range}` : ''}
        </Text>
      </View>
      <View style={aStyles.proCardMetrics}>
        {avg != null && (
          <View style={aStyles.proCardMetric}>
            <Text style={aStyles.proCardMetricValue}>{Number(avg).toFixed(1)}</Text>
            <Text style={aStyles.proCardMetricLabel}>{prop.toUpperCase()} AVG</Text>
          </View>
        )}
        {h2h.teamMeetings != null && h2h.teamMeetings > 0 && (
          <View style={aStyles.proCardMetric}>
            <Text style={aStyles.proCardMetricValue}>{h2h.teamMeetings}</Text>
            <Text style={aStyles.proCardMetricLabel}>TEAM MEETS</Text>
          </View>
        )}
        {trend && trend !== 'stable' && (
          <View style={aStyles.proCardMetric}>
            <View
              style={{ flexDirection: 'row', alignItems: 'center', gap: 3, justifyContent: 'center' }}
            >
              <Ionicons name={trendIcon} size={13} color={trendColor} />
              <Text style={[aStyles.proCardMetricValue, { color: trendColor, fontSize: 12 }]}>
                {trend.toUpperCase()}
              </Text>
            </View>
            <Text style={aStyles.proCardMetricLabel}>TREND</Text>
          </View>
        )}
        {vhr && vhr.total >= 2 && (
          <View style={aStyles.proCardMetric}>
            <Text
              style={[
                aStyles.proCardMetricValue,
                {
                  color:
                    vhr.pct >= 60
                      ? Colors.success
                      : vhr.pct <= 40
                      ? Colors.error
                      : Colors.textSecondary,
                },
              ]}
            >
              {vhr.pct}%
            </Text>
            <Text style={aStyles.proCardMetricLabel}>
              {(vhr.venue as string).toUpperCase()} HIT
            </Text>
          </View>
        )}
      </View>
      {matches.length > 0 && (
        <View style={aStyles.h2hTable}>
          {matches.slice(0, 5).map((m: any, i: number) => {
            const hitLine =
              m.targetStat != null && pick?.line != null && m.targetStat > pick.line;
            const statColor =
              hitLine
                ? Colors.success
                : m.targetStat != null
                ? Colors.error
                : Colors.textTertiary;
            return (
              <View key={i} style={aStyles.h2hRow}>
                <Text style={aStyles.h2hDate}>{(m.date ?? '').slice(0, 10)}</Text>
                <Text style={aStyles.h2hVenue}>{(m.venue ?? '?').slice(0, 1).toUpperCase()}</Text>
                <Text style={aStyles.h2hOpp} numberOfLines={1}>
                  {m.opponent ?? '?'}
                </Text>
                {m.teamPossession != null && (
                  <Text style={aStyles.h2hPoss}>{m.teamPossession}%</Text>
                )}
                <Text style={[aStyles.h2hStat, { color: statColor }]}>
                  {m.targetStat != null ? m.targetStat : '—'}
                </Text>
              </View>
            );
          })}
        </View>
      )}
    </View>
  );
}

/** Manager Context card — recent coaching change or possession drift signal. */
export function renderManagerContext(data: Record<string, unknown> | null) {
  if (!data) return null;
  const mc = (data as any)?.managerContext;
  if (!mc || !mc.coachName) return null;

  const split = mc.logSplitInfo ?? {};
  const drift = mc.possessionDrift ?? {};
  const isRecent = mc.isRecent === true;
  const isThin = split.thinSample === true;

  if (!isRecent && !drift.isShift) return null;

  const cardColor = isRecent ? '#F59E0B' : '#60A5FA';
  const pillLabel = isRecent ? 'MANAGER CHANGE' : 'TACTICAL SHIFT';
  const daysLabel = mc.daysElapsed != null ? `${mc.daysElapsed}d ago` : '';

  return (
    <View style={[aStyles.proCard, { borderColor: cardColor + '44' }]}>
      <View style={aStyles.proCardHeader}>
        <View style={[aStyles.proCardPill, { backgroundColor: cardColor + '20' }]}>
          <Text style={[aStyles.proCardPillText, { color: cardColor }]}>{pillLabel}</Text>
        </View>
        <Text style={aStyles.proCardTitle} numberOfLines={1}>
          {mc.coachName}
          {daysLabel ? ` · ${daysLabel}` : ''}
        </Text>
        {isThin && (
          <View style={[aStyles.proCardPill, { backgroundColor: '#FF6B3520' }]}>
            <Text style={[aStyles.proCardPillText, { color: '#FF6B35' }]}>THIN SAMPLE</Text>
          </View>
        )}
      </View>

      {split.preAvg != null && split.postAvg != null && (
        <View style={aStyles.proCardMetrics}>
          <View style={aStyles.proCardMetric}>
            <Text
              style={[
                aStyles.proCardMetricValue,
                { color: Colors.textTertiary, textDecorationLine: 'line-through' },
              ]}
            >
              {split.preAvg}
            </Text>
            <Text style={aStyles.proCardMetricLabel}>
              PRE-CHANGE{split.preCount ? ` (${split.preCount}G)` : ''}
            </Text>
          </View>
          <View style={{ justifyContent: 'center', paddingBottom: 12 }}>
            <Text style={{ color: Colors.textTertiary, fontSize: 16 }}>→</Text>
          </View>
          <View style={aStyles.proCardMetric}>
            <Text style={[aStyles.proCardMetricValue, { color: cardColor }]}>
              {split.postAvg}
            </Text>
            <Text style={aStyles.proCardMetricLabel}>
              NEW SYSTEM{split.postCount ? ` (${split.postCount}G)` : ''}
            </Text>
          </View>
          {split.preAvg > 0 && (
            <View style={aStyles.proCardMetric}>
              <Text
                style={[
                  aStyles.proCardMetricValue,
                  {
                    color:
                      split.postAvg > split.preAvg ? '#4ADE80' : '#F87171',
                  },
                ]}
              >
                {split.postAvg > split.preAvg ? '+' : ''}
                {(((split.postAvg - split.preAvg) / split.preAvg) * 100).toFixed(0)}%
              </Text>
              <Text style={aStyles.proCardMetricLabel}>ΔROLE</Text>
            </View>
          )}
        </View>
      )}

      {drift.isShift && (
        <View style={aStyles.mgr_driftRow}>
          <Text style={aStyles.mgr_driftLabel}>POSSESSION DRIFT</Text>
          <View style={aStyles.mgr_driftBars}>
            <View style={{ flex: 1, alignItems: 'center', gap: 2 }}>
              <Text style={[aStyles.mgr_driftVal, { color: Colors.textTertiary }]}>
                {drift.seasonAvg}%
              </Text>
              <Text style={aStyles.mgr_driftSub}>SEASON</Text>
            </View>
            <Text style={{ color: Colors.textTertiary, fontSize: 13 }}>→</Text>
            <View style={{ flex: 1, alignItems: 'center', gap: 2 }}>
              <Text
                style={[
                  aStyles.mgr_driftVal,
                  { color: drift.direction === 'up' ? '#4ADE80' : '#F87171' },
                ]}
              >
                {drift.last5Avg}%
              </Text>
              <Text style={aStyles.mgr_driftSub}>LAST 5</Text>
            </View>
            <View style={{ flex: 1, alignItems: 'center', gap: 2 }}>
              <Text
                style={[
                  aStyles.mgr_driftVal,
                  { color: drift.direction === 'up' ? '#4ADE80' : '#F87171' },
                ]}
              >
                {drift.drift > 0 ? '+' : ''}
                {drift.drift}pp
              </Text>
              <Text style={aStyles.mgr_driftSub}>SHIFT</Text>
            </View>
          </View>
        </View>
      )}

      {mc.prevCoachName && (
        <Text style={[aStyles.proCardNote, { color: Colors.textTertiary }]}>
          Replaced: {mc.prevCoachName}
          {isThin
            ? '  ·  ⚠ High uncertainty — thin post-change sample'
            : '  ·  ✓ Model used post-change logs only'}
        </Text>
      )}
    </View>
  );
}

export const aStyles = StyleSheet.create({
  // Evidence ribbon
  evidenceRow: {
    flexDirection: 'row', flexWrap: 'wrap', gap: 7,
    marginBottom: 8, backgroundColor: Colors.cardSecondary,
    borderRadius: 8, padding: 7,
    borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  evidenceCell: {
    flex: 1, minWidth: 52, alignItems: 'center', gap: 1,
    borderWidth: 1, borderColor: Colors.borderSubtle,
    borderRadius: 6, paddingVertical: 5, paddingHorizontal: 3,
    backgroundColor: Colors.card,
  },
  evidenceCellValue: { fontSize: 14, fontWeight: '800', color: Colors.text },
  evidenceCellLabel: {
    fontSize: 7, fontWeight: '700', color: Colors.textTertiary,
    letterSpacing: 0.7, textAlign: 'center',
  },
  // Pro data cards
  proCard: {
    backgroundColor: Colors.cardSecondary, borderRadius: 9,
    borderWidth: 1, borderColor: Colors.borderSubtle,
    padding: 10, marginBottom: 7, gap: 7,
  },
  proCardHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  proCardPill: { borderRadius: 4, paddingHorizontal: 6, paddingVertical: 1 },
  proCardPillText: { fontSize: 7, fontWeight: '900', letterSpacing: 1 },
  proCardTitle: { flex: 1, fontSize: 11, fontWeight: '700', color: Colors.text },
  proCardMetrics: { flexDirection: 'row', gap: 5 },
  proCardMetric: {
    flex: 1, alignItems: 'center', gap: 2,
    backgroundColor: Colors.card, borderRadius: 6,
    paddingVertical: 5, paddingHorizontal: 3,
  },
  proCardMetricValue: { fontSize: 13, fontWeight: '800', color: Colors.text },
  proCardMetricLabel: {
    fontSize: 7, fontWeight: '700', color: Colors.textTertiary,
    letterSpacing: 0.7, textAlign: 'center',
  },
  proCardNote: { fontSize: 10.5, color: Colors.textSecondary, lineHeight: 14 },
  tsaMetaRow: { flexDirection: 'row', justifyContent: 'space-between', gap: 8 },
  tsaMetaText: { flex: 1, fontSize: 8.5, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.3 },
  tsaPitchWrap: {
    alignItems: 'center', backgroundColor: Colors.card, borderRadius: 8,
    paddingVertical: 5, overflow: 'hidden',
  },
  tsaFormationRow: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  tsaLiveBox: {
    marginTop: 5, paddingTop: 7, borderTopWidth: 1,
    borderTopColor: '#FB718533', gap: 3,
  },
  // Possession bar
  possRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  possTeam: { alignItems: 'center', width: 42 },
  possValue: { fontSize: 15, fontWeight: '800', color: Colors.text },
  possLabel: { fontSize: 7, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 0.5 },
  possBar: {
    flex: 1, height: 5, backgroundColor: Colors.borderSubtle, borderRadius: 3, overflow: 'hidden',
  },
  possBarFill: { height: '100%' as any, borderRadius: 3 },
  // H2H table
  h2hTable: { gap: 2, marginTop: 1 },
  h2hRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 4, paddingHorizontal: 6,
    backgroundColor: Colors.card, borderRadius: 6,
  },
  h2hDate: { fontSize: 10, color: Colors.textTertiary, fontWeight: '600', width: 52 },
  h2hVenue: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', width: 12 },
  h2hOpp: { flex: 1, fontSize: 11, color: Colors.textSecondary, fontWeight: '500' },
  h2hPoss: { fontSize: 10, color: Colors.textTertiary, width: 32, textAlign: 'right' },
  h2hStat: { fontSize: 14, fontWeight: '800', width: 26, textAlign: 'right' },
  // Manager / Tactical Shift card
  mgr_driftRow: {
    gap: 6, backgroundColor: Colors.card, borderRadius: 8, padding: 10,
  },
  mgr_driftLabel: { fontSize: 8, fontWeight: '900', color: Colors.textTertiary, letterSpacing: 1.2 },
  mgr_driftBars: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  mgr_driftVal: { fontSize: 16, fontWeight: '800' },
  mgr_driftSub: { fontSize: 7, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 0.7 },
});
