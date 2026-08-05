/**
 * AnalysisCards.tsx — Shared render helpers for structured analysis data cards.
 *
 * Used by both picks.tsx (analysis modal) and scan.tsx (immediate result view)
 * so the two surfaces always show identical cards from the same code.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { AnalysisFactor, MatchScript, PositionalReality, TacticalIntelligence } from '@/lib/api';

export const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Attempts', shots: 'Shots', shots_on_target: 'SOT',
  goals: 'Goals', assists: 'Assists', key_passes: 'Key Passes',
  tackles: 'Tackles', saves: 'Saves', dribbles: 'Dribbles', crosses: 'Crosses',
  interceptions: 'Interceptions', blocks: 'Blocks', fouls_drawn: 'Fouls Drawn',
  fouls_committed: 'Fouls', clearances: 'Clearances', duels_won: 'Duels Won',
  yellow_cards: 'Yellow Cards', shots_assisted: 'Shot Assists', passes: 'Passes',
};

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

/** Tactical Intelligence card — role, opponent shape, market script, and limits. */
export function renderTacticalIntelligence(data: Record<string, unknown> | null) {
  if (!data) return null;
  const ti = (data as any)?.tacticalIntelligence as TacticalIntelligence | undefined;
  const topMatchScript = (data as any)?.matchScript as MatchScript | undefined;
  const topPositionalReality = (data as any)?.positionalReality as PositionalReality | undefined;
  if (!ti && !topMatchScript && !topPositionalReality) return null;

  const player = ti?.player ?? {};
  const lineup = ti?.lineup ?? {};
  const market = ti?.marketGameScript ?? {};
  const possession = ti?.possessionGameScript ?? {};
  const mechanism = ti?.propMechanism ?? {};
  const comparison = ti?.opponentRoleComparison ?? {};
  const evidence = ti?.evidence ?? {};
  const matchScript = (ti?.matchScript ?? topMatchScript ?? {}) as MatchScript;
  const positional = (ti?.positionalReality ?? topPositionalReality ?? {}) as PositionalReality;
  const roleLabel = [player.position, player.role].filter(Boolean).join(' · ');
  const marketLabel = String(market.classification ?? '').replace(/_/g, ' ');
  const possessionLabel = String(possession.classification ?? '').replace(/_/g, ' ');
  const opponentCounts = comparison.opponentRoleCounts ?? {};
  const opponentShape = Object.entries(opponentCounts)
    .filter(([, count]) => Number(count) > 0)
    .map(([key, count]) => `${key.replace(/_/g, ' ')} ${count}`)
    .join(' · ');
  const limitations = [
    ...(ti?.limitations ?? []),
    ...(matchScript.limitations ?? []),
    ...(positional.limitations ?? []),
  ].filter(Boolean);
  const accent = ti?.status === 'strong' ? Colors.success : '#F59E0B';
  const shapeLabel = lineup.shapeStatus === 'confirmed' ? 'CONFIRMED SHAPE' : lineup.shapeStatus === 'projected' ? 'PROJECTED SHAPE' : 'SHAPE LIMITED';
  const signal = positional.propSignal ?? {};
  const robust = positional.robustEvidence ?? {};
  const signalColor = signal.shadowDirection === 'higher_volume'
    ? Colors.success
    : signal.shadowDirection === 'lower_volume'
    ? Colors.error
    : Colors.textSecondary;
  const signalLabel = String(signal.shadowDirection ?? 'neutral').replace(/_/g, ' ');
  const confidencePct = matchScript.confidence != null ? `${Math.round(Number(matchScript.confidence) * 100)}%` : '—';

  return (
    <View style={[aStyles.proCard, { borderColor: accent + '55' }]}>
      <View style={aStyles.proCardHeader}>
        <View style={[aStyles.proCardPill, { backgroundColor: accent + '20' }]}>
          <Text style={[aStyles.proCardPillText, { color: accent }]}>TACTICAL INTELLIGENCE</Text>
        </View>
        <Text style={aStyles.proCardTitle} numberOfLines={1}>
          {ti?.mode === 'shadow' || positional.mode === 'shadow' ? 'SHADOW · NOT LIVE' : 'MODEL CONTEXT'}
        </Text>
      </View>

      <View style={aStyles.tacticalGrid}>
        <View style={aStyles.tacticalCell}>
          <Text style={aStyles.tacticalValue}>{roleLabel || 'Role unavailable'}</Text>
          <Text style={aStyles.proCardMetricLabel}>PLAYER ROLE</Text>
        </View>
        <View style={aStyles.tacticalCell}>
          <Text style={aStyles.tacticalValue}>
            {lineup.formation && lineup.opponentFormation
              ? `${lineup.formation} vs ${lineup.opponentFormation}`
              : 'Formation unavailable'}
          </Text>
          <Text style={aStyles.proCardMetricLabel}>{shapeLabel}</Text>
        </View>
      </View>

      {(matchScript.label || matchScript.classification) && (
        <View style={aStyles.intelSection}>
          <View style={aStyles.intelSectionHeader}>
            <Text style={aStyles.intelSectionTitle}>MATCH SCRIPT</Text>
            <Text style={[aStyles.intelBadge, { color: accent }]}>
              {confidencePct} {String(matchScript.confidenceLabel ?? '').toUpperCase()}
            </Text>
          </View>
          <Text style={[aStyles.tacticalValue, { color: accent }]}>
            {matchScript.label ?? String(matchScript.classification).replace(/_/g, ' ')}
          </Text>
          <Text style={aStyles.proCardNote}>
            Classified from {(matchScript.sources ?? []).join(' + ') || 'available fixture evidence'}.
            {' '}This is a pre-match scenario, not a guaranteed game state.
          </Text>
        </View>
      )}

      {(positional.zone || positional.roleMechanism || signal.shadowDirection) && (
        <View style={aStyles.intelSection}>
          <View style={aStyles.intelSectionHeader}>
            <Text style={aStyles.intelSectionTitle}>POSITIONAL REALITY</Text>
            <Text style={aStyles.intelBadge}>
              {positional.zoneConfidence != null ? `${Math.round(Number(positional.zoneConfidence) * 100)}% ZONE` : 'ROLE ZONE'}
            </Text>
          </View>
          <View style={aStyles.tacticalGrid}>
            <View style={aStyles.tacticalCell}>
              <Text style={aStyles.tacticalValue}>{String(positional.zone ?? 'Zone unavailable').replace(/_/g, ' ')}</Text>
              <Text style={aStyles.proCardMetricLabel}>{positional.zoneSource === 'lineup_provider_coordinates' ? 'PROVIDER COORDINATES' : 'ROLE INFERENCE'}</Text>
            </View>
            <View style={aStyles.tacticalCell}>
              <Text style={[aStyles.tacticalValue, { color: signalColor }]}>{signalLabel}</Text>
              <Text style={aStyles.proCardMetricLabel}>PROP SHADOW SIGNAL</Text>
            </View>
          </View>
          {positional.roleMechanism ? <Text style={aStyles.proCardNote}>{positional.roleMechanism}</Text> : null}
          {signal.shadowMultiplier != null && signal.shadowDirection !== 'neutral' ? (
            <Text style={[aStyles.proCardNote, { color: signalColor }]}>
              Shadow read: {signalLabel} · ×{Number(signal.shadowMultiplier).toFixed(3)} potential movement.
              {' '}Not applied to the displayed projection.
            </Text>
          ) : null}
          {robust.sampleSize != null && robust.sampleSize > 0 ? (
            <Text style={[aStyles.proCardNote, { color: Colors.textTertiary }]}>
              Robust history: n={robust.sampleSize}
              {robust.weightedMean != null ? ` · weighted avg ${Number(robust.weightedMean).toFixed(1)}` : ''}
              {robust.outlierCount ? ` · ${robust.outlierCount} outlier${robust.outlierCount === 1 ? '' : 's'} down-weighted` : ''}
            </Text>
          ) : null}
        </View>
      )}

      {market.classification && (
        <Text style={aStyles.proCardNote}>
          Market script: <Text style={{ color: accent, fontWeight: '800' }}>{marketLabel}</Text>
          {market.playerTeamImpliedProbability != null
            ? ` · ${(market.playerTeamImpliedProbability * 100).toFixed(0)}% player-team implied win probability`
            : ''}
          {market.source ? ' · verified fixture odds' : ''}
        </Text>
      )}
      {possession.expectedPlayerTeamPossession != null && (
        <Text style={aStyles.proCardNote}>
          Possession script: <Text style={{ fontWeight: '800' }}>{possession.expectedPlayerTeamPossession.toFixed(0)}%</Text>
          {possessionLabel !== 'unavailable' ? ` · ${possessionLabel}` : ''}
          {possession.status === 'verified' ? ' · verified data' : ' · fallback estimate'}
        </Text>
      )}
      {mechanism.marketSupport?.length ? (
        <Text style={aStyles.proCardNote}>
          Prop mechanism: {mechanism.marketSupport.join('; ')}.
        </Text>
      ) : null}
      {mechanism.opponentNote ? (
        <Text style={aStyles.proCardNote}>{mechanism.opponentNote}</Text>
      ) : null}
      {opponentShape ? (
        <Text style={aStyles.proCardNote}>
          Opponent role mix: {opponentShape}. {comparison.comparison}
        </Text>
      ) : null}
      <Text style={[aStyles.proCardNote, { color: Colors.textTertiary }]}>
        {comparison.directMarkingVerified
          ? 'Direct marking assignment verified.'
          : 'No direct one-to-one marking assignment is claimed.'}
      </Text>
      {limitations.length > 0 && (
        <Text style={[aStyles.proCardNote, { color: '#F59E0B' }]}>
          Limits: {Array.from(new Set(limitations)).slice(0, 4).join(' · ')}.
        </Text>
      )}
      {evidence.positionComparableSamples != null && evidence.positionComparableSamples > 0 && (
        <Text style={aStyles.proCardNote}>
          Comparable role sample: {evidence.positionComparableSamples} observation{evidence.positionComparableSamples === 1 ? '' : 's'}.
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
  tacticalGrid: { flexDirection: 'row', gap: 8, marginBottom: 8 },
  tacticalCell: {
    flex: 1, minWidth: 0, backgroundColor: Colors.cardSecondary,
    borderRadius: 8, paddingHorizontal: 8, paddingVertical: 7,
    borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  tacticalValue: {
    color: Colors.text, fontSize: 11, fontWeight: '800',
    textTransform: 'capitalize',
  },
  intelSection: {
    backgroundColor: Colors.card,
    borderRadius: 8,
    padding: 8,
    gap: 5,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  intelSectionHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  intelSectionTitle: { fontSize: 8, fontWeight: '900', color: Colors.textTertiary, letterSpacing: 1.1 },
  intelBadge: { fontSize: 8, fontWeight: '800', color: Colors.textTertiary, letterSpacing: 0.4 },
});
