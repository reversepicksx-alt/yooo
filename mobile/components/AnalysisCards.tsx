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
import { reversePicksPressureLabel } from '@/lib/pressure';

export const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Attempts', shots: 'Shots', shots_on_target: 'SOT',
  goals: 'Goals', assists: 'Assists', key_passes: 'Key Passes',
  tackles: 'Tackles', saves: 'Saves', dribbles: 'Dribbles', crosses: 'Crosses',
  interceptions: 'Interceptions', blocks: 'Blocks', fouls_drawn: 'Fouls Drawn',
  fouls_committed: 'Fouls', clearances: 'Clearances', duels_won: 'Duels Won',
  yellow_cards: 'Yellow Cards', shots_assisted: 'Shot Assists', passes: 'Passes',
};

function cohortPositionLabel(value: unknown) {
  const labels: Record<string, string> = {
    GK: 'goalkeepers',
    G: 'goalkeepers',
    GOALKEEPER: 'goalkeepers',
    CB: 'centre-backs',
    LB: 'left-backs',
    RB: 'right-backs',
    LWB: 'left wing-backs',
    RWB: 'right wing-backs',
    DEF: 'defenders',
    D: 'defenders',
    CDM: 'defensive midfielders',
    DM: 'defensive midfielders',
    CM: 'central midfielders',
    MID: 'midfielders',
    M: 'midfielders',
    CAM: 'attacking midfielders',
    AM: 'attacking midfielders',
    LM: 'left midfielders',
    RM: 'right midfielders',
    LW: 'left wingers',
    RW: 'right wingers',
    CF: 'forwards',
    ST: 'strikers',
    SS: 'second strikers',
    F: 'forwards',
    FWD: 'forwards',
  };
  const raw = String(value || '').trim();
  return labels[raw.toUpperCase().replace(/\s+/g, '')] || `${raw.toLowerCase() || 'same-position'} players`;
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

/** Opponent profile card — descriptive matchup context for the selected prop. */
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
          <Text style={aStyles.proCardMetricLabel}>{prop.toUpperCase()} MATCHUP AVG</Text>
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
           ? `Favourable — comparable ${prop.toLowerCase()} observations are above this player's season baseline.`
           : `Unfavourable — comparable ${prop.toLowerCase()} observations are below this player's season baseline.`}
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
  const possessionStatus = String(
    (data as any)?.possessionStatus
      ?? mo?.possessionStatus
      ?? ((data as any)?.possessionSource || mo?.possessionSource
        ? 'estimated'
        : 'unavailable'),
  ).toLowerCase();
  const possessionMeta = (
    (data as any)?.matchDominance
      ?? mo
      ?? (data as any)?.matchFactors
      ?? {}
  ) as Record<string, unknown>;
  const possessionVerificationStatus = String(
    (data as any)?.possessionVerificationStatus
      ?? possessionMeta.possessionVerificationStatus
      ?? possessionStatus,
  ).toLowerCase();
  const possessionRequired = Number(
    (data as any)?.possessionSampleRequired
      ?? possessionMeta.possessionSampleRequired
      ?? 10,
  );
  const teamPossessionSample = Number(
    (data as any)?.teamPossessionSampleSize
      ?? possessionMeta.teamPossessionSampleSize
      ?? 0,
  );
  const opponentPossessionSample = Number(
    (data as any)?.opponentPossessionSampleSize
      ?? possessionMeta.opponentPossessionSampleSize
      ?? 0,
  );
  const teamPossessionVenue = String(
    possessionMeta.teamPossessionVenue ?? 'team',
  ).toUpperCase();
  const opponentPossessionVenue = String(
    possessionMeta.opponentPossessionVenue ?? 'opponent',
  ).toUpperCase();
  const observedTeamPossession = typeof possessionMeta.teamPossessionObservedAvg === 'number'
    ? possessionMeta.teamPossessionObservedAvg
    : Number.NaN;
  const observedOpponentPossession = typeof possessionMeta.opponentPossessionObservedAvg === 'number'
    ? possessionMeta.opponentPossessionObservedAvg
    : Number.NaN;
  const moneylineWeight = Number(possessionMeta.moneylineWeight ?? 0);
  const teamPossessionRows = Array.isArray(possessionMeta.teamPossessionRows)
    ? possessionMeta.teamPossessionRows as Array<Record<string, unknown>>
    : [];
  const opponentPossessionRows = Array.isArray(possessionMeta.opponentPossessionRows)
    ? possessionMeta.opponentPossessionRows as Array<Record<string, unknown>>
    : [];
  const possessionCalculationStatus =
    possessionVerificationStatus === 'verified' && possessionStatus === 'verified'
      ? 'VERIFIED'
      : possessionVerificationStatus === 'insufficient_sample'
        ? 'LIMITED'
        : possessionStatus === 'unavailable'
          ? 'UNAVAILABLE'
          : 'ESTIMATE';
  const possessionSampleLabel = (
    `${possessionCalculationStatus} · ${teamPossessionVenue} N=${Number.isFinite(teamPossessionSample) ? teamPossessionSample : 0}/${possessionRequired} · `
    + `${opponentPossessionVenue} N=${Number.isFinite(opponentPossessionSample) ? opponentPossessionSample : 0}/${possessionRequired}`
  );
  const possessionEvidenceLabel = (
    Number.isFinite(observedTeamPossession) && Number.isFinite(observedOpponentPossession)
      ? `SCHEDULE AVG ${observedTeamPossession.toFixed(1)}% / ${observedOpponentPossession.toFixed(1)}%`
      : 'VERIFIED SCHEDULE AVG UNAVAILABLE'
  ) + ` · ML BLEND ${Math.round(Math.max(0, moneylineWeight) * 100)}%`;
  const gt = (data as any)?.expectedGameType ?? mo?.expectedGameType;
  const kmf = (data as any)?.keyMatchupFactor ?? mo?.keyMatchupFactor;
  const ss = (data as any)?.sharpSummary;
  const isHome = pick?.venue !== 'away';
  const teamPoss: number | null = ep ? (isHome ? ep.home : ep.away) : null;
  const oppPoss: number | null = ep ? (isHome ? ep.away : ep.home) : null;
  const hasPoss = teamPoss != null && oppPoss != null && possessionStatus !== 'unavailable';
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
          <Text style={aStyles.proCardTitle} numberOfLines={1}>
            {possessionCalculationStatus}
          </Text>
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
      {hasPoss && (
        <>
          <Text style={aStyles.proCardNote} numberOfLines={2}>
            {possessionSampleLabel}
          </Text>
          <Text style={aStyles.proCardNote} numberOfLines={2}>
            {possessionEvidenceLabel}
          </Text>
          {teamPossessionRows.length > 0 && opponentPossessionRows.length > 0 && (
            <View style={{ marginTop: 4 }}>
              <Text style={aStyles.proCardNote} numberOfLines={1}>
                HOME POSSESSION MATCHES USED · {teamPossessionRows.length} latest verified
              </Text>
              {teamPossessionRows.slice(0, 10).map((row, index) => (
                <Text key={`team-possession-${String(row.fixtureId ?? index)}`} style={aStyles.proCardNote} numberOfLines={1}>
                  {String(row.date ?? 'Unknown date')} · vs {String(row.opponent ?? 'Unknown')} · {Number(row.value).toFixed(1)}%
                </Text>
              ))}
              <Text style={[aStyles.proCardNote, { marginTop: 3 }]} numberOfLines={1}>
                AWAY POSSESSION MATCHES USED · {opponentPossessionRows.length} latest verified
              </Text>
              {opponentPossessionRows.slice(0, 10).map((row, index) => (
                <Text key={`opponent-possession-${String(row.fixtureId ?? index)}`} style={aStyles.proCardNote} numberOfLines={1}>
                  {String(row.date ?? 'Unknown date')} · vs {String(row.opponent ?? 'Unknown')} · {Number(row.value).toFixed(1)}%
                </Text>
              ))}
            </View>
          )}
        </>
      )}
      {possessionStatus !== 'verified' && (
        <Text style={[aStyles.proCardNote, { color: Colors.textTertiary }]}>
          {possessionStatus === 'estimated'
            ? 'Possession shown as an estimate from available matchup signals, not verified match statistics.'
            : 'Verified possession unavailable for this fixture.'}
        </Text>
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

/**
 * Subscriber-facing model/causal decision card. It deliberately displays the
 * deterministic projection and causal workload as separate evidence layers.
 */
export function renderModelCausalDecision(
  data: Record<string, unknown> | null,
  pick: Record<string, unknown> | null,
) {
  const source = { ...(pick ?? {}), ...(data ?? {}) } as any;
  const causal = source.causalScript ?? {};
  const summary = source.causalSummary ?? {};
  const cohort = causal.opponentRoleCohort ?? {};
  const gate = causal.recommendationGate ?? {};
  const modelProjection = Number(
    source.deterministicProjection ?? summary.modelProjection ?? causal.modelProjection
      ?? source.projectedValue ?? source.projection,
  );
  const line = Number(source.line);
  const modelDirection = String(
    source.modelDirection ?? summary.modelDirection ?? causal.modelDirection ?? gate.rpRecommendation ?? '',
  ).toUpperCase();
  const causalDirection = String(
    source.causalDirection ?? summary.causalDirection ?? causal.causalDirection ?? '',
  ).toUpperCase();
  const verdict = String(summary.verdict ?? causal.causalVerdict ?? '');
  const gateDecision = String(summary.gateDecision ?? gate.decision ?? '');
  const workload = summary.workloadAverage ?? cohort.workloadAverage;
  const baseline = summary.normalMatchingVenueAverage ?? cohort.normalMatchingVenueAverage;
  const effect = summary.opponentRoleEffect ?? cohort.opponentRoleEffect;
  const effectLabel = String(summary.effect ?? cohort.effect ?? '').toUpperCase();
  const sampleSize = summary.cleanSampleSize ?? cohort.cleanSampleSize ?? summary.sampleSize ?? cohort.sampleSize;
  const sampleStrength = String(summary.sampleStrength ?? causal.corroboration?.sampleStrength ?? '');
  const finalRecommendation = String(
    summary.finalRecommendation ?? source.recommendation ?? 'PASS',
  ).toUpperCase();
  const finalReason = String(
    summary.finalReason ?? source.passReason ?? summary.reason ?? gate.reason ?? '',
  );
  const safety = source.recentPropSafety ?? summary.gates?.rollingSafety;
  const lineBand = source.lineDeviationBand ?? summary.lineDeviationBand;
  const lineRate = source.lineDeviationHitRate ?? summary.lineDeviationHitRate;
  const lineSample = source.lineDeviationHitRateN ?? summary.lineDeviationHitRateN;
  const calibrationDirection = String(
    source.recommendation ?? source.preCausalRecommendation ?? summary.modelDirection ?? '',
  ).toUpperCase();
  const hasLineCalibration = typeof lineRate === 'number' && Number(lineSample ?? 0) > 0;
  const rollingRate = safety && typeof (safety as any).hitRate === 'number'
    ? (safety as any).hitRate
    : null;
  const rollingSample = safety ? Number((safety as any).sampleSize ?? (safety as any).n ?? 0) : 0;
  const calibrationMessage = hasLineCalibration
    ? `Within the ${String(lineBand ?? 'current').replace(/_/g, ' ').toUpperCase()} line band, ${calibrationDirection || 'SELECTED'} has hit ${(lineRate as number).toFixed(1)}% across ${lineSample} system-confirmed saved picks.`
    : rollingRate != null && rollingSample > 0
      ? `The ${calibrationDirection || 'selected'} direction has hit ${Number(rollingRate).toFixed(1)}% across ${rollingSample} system-confirmed saved picks in the rolling 45-day calibration window.`
      : 'Calibration is active, but there is not yet a verified system-confirmed saved-pick sample for this band.';
  const hasCausalData = Boolean(
    verdict || causalDirection || workload != null || gateDecision || summary.reason,
  );
  if (!hasCausalData) return null;

  const conflict = verdict === 'CAUSAL CONTRADICTION'
    || (modelDirection === 'UNDER' && causalDirection === 'MORE')
    || (modelDirection === 'OVER' && causalDirection === 'LESS');
  const incomplete = verdict === 'EVIDENCE INCOMPLETE' || causalDirection === 'EVIDENCE INCOMPLETE';
  const accent = conflict ? '#F59E0B' : incomplete ? '#94A3B8' : Colors.success;
  const finalLabel = conflict
    ? `PASS — MODEL ${modelDirection || 'EDGE'} / CAUSAL ${causalDirection || 'CONFLICT'}`
    : finalRecommendation === 'PASS'
      ? 'PASS — EVIDENCE GATE ACTIVE'
      : `${finalRecommendation} — MODEL & CAUSAL CHECK`;
  const metric = (label: string, value: string | null, key: string) => value ? (
    <View key={key} style={{ flex: 1, minWidth: 108, paddingVertical: 7, borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle }}>
      <Text style={{ color: Colors.textTertiary, fontSize: 8, fontWeight: '800', letterSpacing: 0.8 }}>{label}</Text>
      <Text style={{ color: Colors.text, fontSize: 13, fontWeight: '900', marginTop: 3 }}>{value}</Text>
    </View>
  ) : null;

  return (
    <View style={{ marginHorizontal: 14, marginTop: 14, paddingLeft: 12, paddingBottom: 3, borderLeftWidth: 2, borderLeftColor: accent }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7 }}>
        <Ionicons name={conflict ? 'git-compare-outline' : incomplete ? 'information-circle-outline' : 'shield-checkmark-outline'} size={16} color={accent} />
        <Text style={{ color: accent, fontSize: 9, fontWeight: '900', letterSpacing: 1 }}>MODEL VS CAUSAL EVIDENCE</Text>
        {sampleStrength ? <Text style={{ marginLeft: 'auto', color: Colors.textSecondary, fontSize: 9, fontWeight: '800' }}>{sampleStrength.toUpperCase()}</Text> : null}
      </View>
      <Text style={{ color: Colors.text, fontSize: 14, lineHeight: 20, fontWeight: '900', marginTop: 7 }}>{finalLabel}</Text>
      <Text style={{ color: Colors.textSecondary, fontSize: 11, lineHeight: 16, marginTop: 3 }}>
        Model projection and causal workload are separate inputs. Causal workload never replaces the model projection.
      </Text>
      <View style={{ flexDirection: 'row', gap: 18, marginTop: 10 }}>
        {metric('YOUR LINE', Number.isFinite(line) ? line.toFixed(1) : null, 'line')}
        {metric('MODEL PROJECTION', Number.isFinite(modelProjection) ? modelProjection.toFixed(1) : null, 'projection')}
      </View>
      <View style={{ flexDirection: 'row', gap: 18, marginTop: 2 }}>
        {metric('MODEL DIRECTION', modelDirection || null, 'model')}
        {metric('CAUSAL DIRECTION', causalDirection || (incomplete ? 'INCOMPLETE' : null), 'causal')}
      </View>
      {(workload != null || baseline != null || effect != null || sampleSize != null) && (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', columnGap: 18, marginTop: 2 }}>
          {metric('ROLE WORKLOAD', workload != null ? Number(workload).toFixed(2) : null, 'workload')}
          {metric('SAME-VENUE BASELINE', baseline != null ? Number(baseline).toFixed(2) : null, 'baseline')}
          {metric('OPPONENT EFFECT', effect != null ? `${Number(effect) >= 1 ? '+' : ''}${((Number(effect) - 1) * 100).toFixed(1)}%` : effectLabel || null, 'effect')}
          {metric('EXACT-ROLE SAMPLE', sampleSize != null ? String(sampleSize) : null, 'sample')}
        </View>
      )}
      <Text style={{ color: accent, fontSize: 11, fontWeight: '800', lineHeight: 16, marginTop: 10 }}>
        {incomplete
          ? 'CAUSAL EVIDENCE INCOMPLETE — no causal direction is inferred without a verified comparison baseline.'
          : finalReason || verdict.replace(/_/g, ' ')}
      </Text>
      {safety ? (
        <Text style={{ color: Colors.textSecondary, fontSize: 10, lineHeight: 15, marginTop: 5 }}>
          Independent rolling safety control: {String((safety as any).action ?? 'active').replace(/_/g, ' ')}.
        </Text>
      ) : null}
      <Text style={{ color: Colors.textSecondary, fontSize: 10, lineHeight: 15, marginTop: 6 }}>
        {calibrationMessage}
      </Text>
    </View>
  );
}

/** Customer-facing tactical verdict — one coherent player/prop read. */
export function renderTacticalVerdict(
  data: Record<string, unknown> | null,
  pick: {
    playerName?: string | null;
    propType?: string | null;
    recommendation?: string | null;
    projectedValue?: number | null;
    projection?: number | null;
    line?: number | null;
    teamName?: string | null;
    opponentName?: string | null;
  } | null,
) {
  if (!data || String((data as any)?.sport ?? 'soccer').toLowerCase() !== 'soccer') return null;

  const ti = ((data as any)?.tacticalIntelligence ?? {}) as any;
  const tc = ((data as any)?.tacticalContext ?? {}) as any;
  const player = ti.player ?? {};
  const lineup = ti.lineup ?? {};
  const possession = ti.possessionGameScript ?? {};
  const mechanism = ti.propMechanism ?? {};
  const comparison = ti.opponentRoleComparison ?? {};
  const matchScript = ti.matchScript ?? (data as any)?.matchScript ?? {};
  const positional = ti.positionalReality ?? (data as any)?.positionalReality ?? {};
  const propType = String(pick?.propType ?? (data as any)?.propType ?? '');
  const prop = PROP_LABELS[propType] ?? (propType.replace(/_/g, ' ') || 'prop');
  const playerName = pick?.playerName ?? (data as any)?.playerName ?? 'This player';
  const role = player.role ?? tc.role ?? player.position ?? tc.position;
  const position = player.position ?? tc.position;
  const recommendation = String(
    pick?.recommendation ?? (data as any)?.recommendation ?? '',
  ).toUpperCase();
  const projection = Number(pick?.projectedValue ?? pick?.projection ?? (data as any)?.projectedValue ?? (data as any)?.projection);
  const line = Number(pick?.line ?? (data as any)?.line);
  const playerPoss = Number(possession.expectedPlayerTeamPossession ?? tc.expectedPossession);
  const opponentPoss = Number(tc.opponentExpectedPossession ?? (100 - playerPoss));
  const possessionStatus = String(
    tc.possessionStatus
      ?? (data as any)?.possessionStatus
      ?? tc.possessionSource
      ?? 'unavailable',
  ).toLowerCase();
  const hasPossession =
    Number.isFinite(playerPoss) &&
    Number.isFinite(opponentPoss) &&
    possessionStatus !== 'unavailable';
  const roleLower = String(role ?? '').toLowerCase();
  const isPass = ['pass_attempts', 'passes', 'key_passes', 'crosses'].includes(propType);
  const isDefender = ['defender', 'cb', 'def', 'fullback', 'wingback', 'stopper'].some((x) => roleLower.includes(x));

  let roleMechanism = '';
  if (isPass && isDefender) {
    roleMechanism = `${playerName} is being used as a ${role || 'defensive'} player, so ${prop.toLowerCase()} come mainly from first-phase circulation: receiving the ball from the goalkeeper, recycling possession across the back line, and finding the next outlet. This is not a final-third volume role.`;
  } else if (isPass) {
    roleMechanism = `${playerName}'s ${role || 'resolved'} role creates ${prop.toLowerCase()} through ${roleLower.includes('mid') || roleLower.includes('deep') ? 'repeated buildup and recycle actions' : 'linking play when the team can establish possession'}.`;
  } else if (['shots', 'shots_on_target', 'goals', 'assists'].includes(propType)) {
    roleMechanism = `${playerName}'s ${role || 'resolved'} role creates this prop through attacking-third access and final actions; possession only helps if it reaches the player's zone.`;
  } else if (role) {
    roleMechanism = `${playerName}'s ${role} role is the primary tactical anchor for this ${prop.toLowerCase()} projection.`;
  }

  let possessionRead = '';
  if (hasPossession && isPass) {
    const direction = playerPoss < 50
      ? 'fewer settled possessions and recycle sequences'
      : 'more settled possessions and recycle sequences';
    const supports = (playerPoss < 50 && recommendation === 'UNDER')
      || (playerPoss >= 50 && recommendation === 'OVER');
    possessionRead = `The team is ${possessionStatus === 'verified' ? 'projected' : 'estimated'} at ${playerPoss.toFixed(0)}% possession against ${opponentPoss.toFixed(0)}% for ${pick?.opponentName ?? (data as any)?.opponentName ?? 'the opponent'}, which points to ${direction}. ${supports ? `That supports the ${recommendation} read.` : `That conflicts with the ${recommendation} read, so this is a key uncertainty.`}`;
  } else if (hasPossession) {
    possessionRead = `The team is ${possessionStatus === 'verified' ? 'projected' : 'estimated'} at ${playerPoss.toFixed(0)}% possession against ${opponentPoss.toFixed(0)}% for the opponent; the effect on ${prop.toLowerCase()} depends on whether the relevant actions are attacking or defensive.`;
  }

  const h2h = ti.playerOpponentHistory ?? (data as any)?.h2hPlayerStats?.opponentHitRate;
  const cohort = ti.positionCohort ?? (data as any)?.positionComparison;
  const h2hN = Number(h2h?.sampleSize ?? 0);
  const cohortN = Number(cohort?.sampleSize ?? 0);
  let opponentRead = '';
  if (h2hN > 0) {
    opponentRead = `Against ${h2h?.opponent ?? pick?.opponentName ?? 'this opponent'}, the player has ${h2hN} verified appearance${h2hN === 1 ? '' : 's'}${h2h?.average != null ? ` averaging ${Number(h2h.average).toFixed(1)} ${prop.toLowerCase()}` : ''}; that is ${h2hN < 5 ? 'useful but thin evidence' : 'relevant matchup evidence'}.`;
  } else if (cohortN > 0) {
    opponentRead = `No direct player history is available against this opponent. The same-position comparison has only ${cohortN} observation${cohortN === 1 ? '' : 's'}, so it is a limited context signal rather than a decisive edge.`;
  } else {
    opponentRead = 'No verified player-level or same-position opponent history is available, so the matchup read is driven by role and team context.';
  }

  const scriptLabel = String(matchScript.label ?? matchScript.classification ?? '').replace(/_/g, ' ');
  const scriptRead = scriptLabel && scriptLabel.toLowerCase() !== 'unavailable'
    ? `Pre-match environment: ${scriptLabel.toLowerCase()}. This is a scenario estimate, not a guaranteed game state.`
    : '';
  const limitations: string[] = [];
  const pressure = tc.pressureResponse ?? (data as any)?.pressureResponse;
  if (pressure?.status === 'insufficient_evidence') limitations.push('no player pressure-response classification');
  if (comparison.directMarkingVerified === false) limitations.push('no verified player-level pressure route');
  if (positional.mode === 'shadow' || mechanism.projectionAdjustmentStatus) limitations.push('tactical signal is context-only, not an extra projection adjustment');

  const conclusion = Number.isFinite(projection) && Number.isFinite(line)
    ? `Bottom line: the model lands at ${projection.toFixed(1)} against ${line.toFixed(1)}. The ${recommendation || 'model'} is based on the role mechanism plus the evidence above—not on a generic recent-form sentence.`
    : '';

  if (!roleMechanism && !possessionRead && !opponentRead) return null;
  const accent = recommendation === 'OVER' ? Colors.success : recommendation === 'UNDER' ? Colors.error : '#F59E0B';

  return (
    <View style={[aStyles.tacticalVerdictCard, { borderColor: accent + '66' }]}>
      <View style={aStyles.proCardHeader}>
        <View style={[aStyles.proCardPill, { backgroundColor: accent + '20' }]}>
          <Text style={[aStyles.proCardPillText, { color: accent }]}>PLAYER READ</Text>
        </View>
        <Text style={aStyles.proCardTitle}>TACTICAL VERDICT</Text>
      </View>
      {roleMechanism ? <Text style={aStyles.tacticalVerdictLead}>{roleMechanism}</Text> : null}
      {possessionRead ? <Text style={aStyles.proCardNote}>{possessionRead}</Text> : null}
      {scriptRead ? <Text style={aStyles.proCardNote}>{scriptRead}</Text> : null}
      {opponentRead ? <Text style={aStyles.proCardNote}>{opponentRead}</Text> : null}
      {conclusion ? <Text style={[aStyles.tacticalVerdictConclusion, { color: accent }]}>{conclusion}</Text> : null}
      {limitations.length > 0 ? (
        <Text style={[aStyles.proCardNote, { color: '#F59E0B' }]}>
          Limits: {limitations.join(' · ')}.
        </Text>
      ) : null}
    </View>
  );
}

/** Press Intensity card. It stays visible even when the provider sample is unavailable. */
export function renderTacticalContext(data: Record<string, unknown> | null) {
  const sport = String((data as any)?.sport ?? 'soccer').toLowerCase();
  if (sport && sport !== 'soccer') return null;
  const pressIntensity = (
    (data as any)?.tacticalContext?.pressIntensity
    ?? (data as any)?.bayesianMetrics?.pressIntensity
    ?? (data as any)?.pressIntensity
    ?? {}
  ) as any;
  const available = pressIntensity?.status === 'available';
  const label = available
    ? reversePicksPressureLabel(pressIntensity)
    : 'NO VERIFIED SAMPLE';
  const sampleSize = Number(pressIntensity?.sampleSize || 0);
  return (
    <View style={[aStyles.tacticalContextFlat, { borderLeftColor: available ? '#60A5FA' : '#64748B' }]}>
      <View style={aStyles.flatContextHeader}>
        <Text style={[aStyles.flatContextKicker, { color: available ? '#60A5FA' : '#94A3B8' }]}>PRESS INTENSITY</Text>
        <Text style={aStyles.flatContextTitle}>NEXT OPPONENT PRESSURE</Text>
      </View>
      <Text style={aStyles.flatContextValue}>
        {label}
      </Text>
      <Text style={aStyles.flatContextNote}>
        {available
          ? `${sampleSize} verified next-opponent pressure input${sampleSize === 1 ? '' : 's'}`
          : 'No verified next-opponent pressure input was returned; no 0/100 is implied.'}
      </Text>
      {available && pressIntensity?.avg_poss != null ? (
        <Text style={aStyles.flatContextNote}>
          Possession basis: opponent averaged {Number(pressIntensity.avg_poss).toFixed(0)}% possession
        </Text>
      ) : null}
      {available && pressIntensity?.projectionApplied ? (
        <Text style={aStyles.flatContextNote}>
          Passing projection factor: ×{Number(pressIntensity.projectionMultiplier || 1).toFixed(3)}
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
  if (!h2h || (!(h2h.sampleSize > 0) && !(h2h.teamMeetings > 0))) return null;
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
  const meetingsByVenue = h2h.teamMeetingsByVenue ?? {};
  const venueMeetings = [
    ...(Array.isArray(meetingsByVenue.home) ? meetingsByVenue.home.map((m: any) => ({ ...m, venueLabel: 'HOME' })) : []),
    ...(Array.isArray(meetingsByVenue.away) ? meetingsByVenue.away.map((m: any) => ({ ...m, venueLabel: 'AWAY' })) : []),
  ];
  const prop =
    PROP_LABELS[h2h.targetProp ?? ''] ?? (h2h.targetProp ?? '').replace(/_/g, ' ');
  const venueSplits = h2h.venueSplits ?? {};
  const venuePossessionAverages = (['home', 'away'] as const).reduce((result, venue) => {
    const rows = Array.isArray(meetingsByVenue[venue]) ? meetingsByVenue[venue] : [];
    const values = rows
      .map((meeting: any) => ({
        team: Number(venue === 'home' ? meeting.homePossession : meeting.awayPossession),
        opponent: Number(venue === 'home' ? meeting.awayPossession : meeting.homePossession),
      }))
      .filter((value: { team: number; opponent: number }) => (
        Number.isFinite(value.team) && Number.isFinite(value.opponent)
      ));
    if (values.length > 0) {
      result[venue] = {
        team: values.reduce((sum, value) => sum + value.team, 0) / values.length,
        opponent: values.reduce((sum, value) => sum + value.opponent, 0) / values.length,
        sampleSize: values.length,
      };
    }
    return result;
  }, {} as Record<'home' | 'away', { team: number; opponent: number; sampleSize: number } | undefined>);
  return (
    <View style={aStyles.proCard}>
      <View style={aStyles.proCardHeader}>
        <View style={[aStyles.proCardPill, { backgroundColor: Colors.primary + '18' }]}>
          <Text style={[aStyles.proCardPillText, { color: Colors.primary }]}>H2H</Text>
        </View>
        <Text style={aStyles.proCardTitle} numberOfLines={1}>
          {h2h.sampleSize
            ? `${h2h.sampleSize} app${h2h.sampleSize !== 1 ? 's' : ''} vs ${pick?.opponentName}`
            : `${h2h.teamMeetings ?? 0} team meeting${h2h.teamMeetings !== 1 ? 's' : ''} vs ${pick?.opponentName}`}
          {h2h.seasonsCovered ? ` · ${h2h.seasonsCovered.range}` : ''}
        </Text>
      </View>
      {(venueSplits.home || venueSplits.away) && (
        <View style={{ flexDirection: 'row', gap: 7, marginBottom: 9 }}>
          {(['home', 'away'] as const).map((venue) => {
            const split = venueSplits[venue];
            return (
              <View
                key={venue}
                style={{
                  flex: 1,
                  backgroundColor: Colors.cardSecondary,
                  borderRadius: 6,
                  paddingHorizontal: 7,
                  paddingVertical: 5,
                }}
              >
                <Text
                  style={[
                    aStyles.proCardMetricLabel,
                    { color: venue === 'home' ? Colors.success : '#60A5FA' },
                  ]}
                >
                  {venue.toUpperCase()}
                </Text>
                <Text style={{ color: Colors.text, fontSize: 10, fontWeight: '800', marginTop: 2 }}>
                  {split ? `${Number(split.average).toFixed(1)} AVG · ${Number(split.overPct).toFixed(1)}% O` : '—'}
                </Text>
                <Text style={{ color: Colors.textTertiary, fontSize: 8, marginTop: 1 }}>
                  {split ? `N=${split.sampleSize} · ${Math.round(Number(split.minutesAverage))}' avg` : 'No verified apps'}
                </Text>
              </View>
            );
          })}
        </View>
      )}
      {(venuePossessionAverages.home || venuePossessionAverages.away) && (
        <View style={{
          marginBottom: 9,
          padding: 7,
          borderRadius: 6,
          backgroundColor: Colors.cardSecondary,
          borderWidth: 1,
          borderColor: Colors.borderSubtle,
        }}>
          <Text style={[aStyles.proCardMetricLabel, { marginBottom: 4 }]}>
            VENUE AVG POSSESSION · VS OPPONENT
          </Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            {(['home', 'away'] as const).map((venue) => {
              const average = venuePossessionAverages[venue];
              return (
                <View key={`h2h-pos-${venue}`} style={{ flex: 1 }}>
                  <Text style={[aStyles.proCardMetricLabel, { color: venue === 'home' ? Colors.success : '#60A5FA' }]}>
                    TEAM {venue.toUpperCase()}
                  </Text>
                  <Text style={{ color: Colors.text, fontSize: 10, fontWeight: '800', marginTop: 2 }}>
                    {average ? `${average.team.toFixed(1)}% · OPP ${average.opponent.toFixed(1)}%` : '—'}
                  </Text>
                  <Text style={{ color: Colors.textTertiary, fontSize: 8, marginTop: 1 }}>
                    {average ? `N=${average.sampleSize} team meetings` : 'No verified meetings'}
                  </Text>
                </View>
              );
            })}
          </View>
        </View>
      )}
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
                <Text style={aStyles.h2hVenue}>
                  {String(m.venue ?? '').toLowerCase() === 'home'
                    ? 'HOME'
                    : String(m.venue ?? '').toLowerCase() === 'away'
                    ? 'AWAY'
                    : 'VENUE —'}
                </Text>
                <Text style={aStyles.h2hOpp} numberOfLines={1}>
                  {m.opponent ?? '?'}
                </Text>
                <Text style={aStyles.h2hPoss}>
                  {m.minutesPlayed != null || m.minutes != null
                    ? `${m.minutesPlayed ?? m.minutes}'`
                    : '—'}
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
      {venueMeetings.length > 0 && (
        <View style={{ marginTop: 10, borderTopWidth: 1, borderTopColor: Colors.borderSubtle, paddingTop: 8 }}>
          <Text style={[aStyles.proCardMetricLabel, { marginBottom: 5 }]}>
            TEAM MEETINGS BY VENUE · POSSESSION
          </Text>
          {venueMeetings.slice(0, 8).map((m: any, i: number) => (
            <View key={`team-meeting-${i}`} style={aStyles.h2hRow}>
              <Text style={aStyles.h2hDate}>{String(m.date ?? '').slice(0, 10) || '—'}</Text>
              <Text style={aStyles.h2hVenue}>{m.venueLabel}</Text>
              <Text style={aStyles.h2hOpp} numberOfLines={1}>
                {m.homeTeam || '?'}–{m.awayTeam || '?'}
              </Text>
              <Text style={aStyles.h2hPoss}>
                {m.homePossession != null && m.awayPossession != null
                  ? `${m.homePossession}%–${m.awayPossession}%`
                  : 'Poss. —'}
              </Text>
            </View>
          ))}
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

/** Minimal role and positional-reality card. */
export function renderTacticalIntelligence(data: Record<string, unknown> | null) {
  if (!data) return null;
  const ti = (data as any)?.tacticalIntelligence as TacticalIntelligence | undefined;
  const player = ti?.player ?? {};
  const positional = (
    ti?.positionalReality
      ?? (data as any)?.positionalReality
      ?? {}
  ) as PositionalReality;
  const roleLabel = [player.position, player.role].filter(Boolean).join(' · ');
  const signal = positional.propSignal ?? {};

  // Strip backend sentinel strings that carry no real information. The backend
  // sets zone="Zone Unavailable" and roleMechanism="No bounded zone-to-prop…"
  // when the positional packet is incomplete. Those strings should never
  // surface in the UI — they look like errors to subscribers.
  const ZONE_SENTINELS = new Set([
    '', 'zone unavailable', 'unavailable', 'unknown', 'n/a', 'none',
    'zone unknown', 'zone unverified', 'unverified',
  ]);
  const rawZone = String(positional.zone ?? '').trim();
  // Normalize underscores to spaces before checking sentinels so that
  // "zone_unavailable" and "zone unavailable" both match the sentinel set.
  const normalizedZone = rawZone.toLowerCase().replace(/_/g, ' ');
  const cleanZone = !ZONE_SENTINELS.has(normalizedZone)
    ? rawZone.replace(/_/g, ' ')
    : null;
  const rawMechanism = String(positional.roleMechanism ?? '').trim();
  const cleanMechanism = rawMechanism &&
    !rawMechanism.toLowerCase().includes('no bounded') &&
    !rawMechanism.toLowerCase().includes('unavailable') &&
    !rawMechanism.toLowerCase().includes('not supported')
    ? rawMechanism : null;
  // Only meaningful (non-neutral) shadow directions contribute to reality display.
  const meaningfulSignal = signal.shadowDirection &&
    signal.shadowDirection !== 'neutral'
    ? signal.shadowDirection : null;

  const hasReality = Boolean(
    cleanZone
      || cleanMechanism
      || meaningfulSignal
      || positional.robustEvidence?.sampleSize,
  );
  if (!roleLabel && !hasReality) return null;

  const roleSource = String(
    player.roleSource ?? (data as any)?.tacticalContext?.roleSource ?? '',
  );
  // Suppress the entire card when the only info is a generic position category
  // with no verified role, no zone, and no prop signal. The position string is
  // already visible in the pick header and repeating it as a "POSITIONAL REALITY"
  // card with no supporting evidence looks like an error to the subscriber.
  const genericFallback = roleSource === 'unavailable' || roleSource === 'category_fallback';
  if (genericFallback && !hasReality) return null;

  // "unavailable" and "category_fallback" are internal resolver states — show a
  // neutral label rather than the raw identifier.
  const roleSourceLabel = roleSource === 'fixture_lineup_observation'
    ? 'confirmed fixture lineup'
    : roleSource === 'manual_override'
    ? 'manual player profile'
    : roleSource === 'unavailable' || roleSource === 'category_fallback'
    ? 'listed position category'
    : roleSource.replace(/_/g, ' ') || 'role resolver';
  // Only show role confidence when the role itself is verified.
  const showRoleDetail = roleSource !== 'unavailable' && roleSource !== 'category_fallback';

  const signalColor = meaningfulSignal === 'higher_volume'
    ? Colors.success
    : meaningfulSignal === 'lower_volume'
    ? Colors.error
    : Colors.textSecondary;
  const signalLabel = String(meaningfulSignal ?? '').replace(/_/g, ' ');

  return (
    <View style={[aStyles.tacticalIntelligenceFlat, { borderLeftColor: Colors.primary }]}>
      <View style={aStyles.flatContextHeader}>
        <Text style={[aStyles.flatContextKicker, { color: Colors.primary }]}>ROLE + POSITION</Text>
        <Text style={aStyles.flatContextTitle}>POSITIONAL REALITY</Text>
      </View>

      {roleLabel ? (
        <>
          <View style={aStyles.flatRoleBlock}>
            <Text style={aStyles.flatRoleValue}>{roleLabel}</Text>
            <Text style={aStyles.proCardMetricLabel}>CURRENT PLAYER PROFILE</Text>
          </View>
          {showRoleDetail ? (
            <Text style={aStyles.proCardNote}>
              Role evidence: <Text style={{ fontWeight: '800' }}>{roleSourceLabel}</Text>
              {player.roleConfidence ? ` · ${player.roleConfidence} confidence` : ''}
            </Text>
          ) : null}
        </>
      ) : null}

      {hasReality ? (
        <View style={aStyles.flatIntelSection}>
          <View style={aStyles.flatSectionHeader}>
            <Text style={aStyles.intelSectionTitle}>POSITIONAL REALITY</Text>
            <Text style={aStyles.intelBadge}>
              {positional.zoneConfidence != null
                ? `${Math.round(Number(positional.zoneConfidence) * 100)}% ZONE`
                : 'ROLE ZONE'}
            </Text>
          </View>
          <View style={aStyles.flatMetricRow}>
            {cleanZone ? (
              <View style={aStyles.flatMetricCell}>
                <Text style={aStyles.flatMetricValue}>{cleanZone}</Text>
                <Text style={aStyles.proCardMetricLabel}>ROLE ZONE</Text>
              </View>
            ) : null}
            {meaningfulSignal ? (
              <View style={aStyles.flatMetricCell}>
                <Text style={[aStyles.flatMetricValue, { color: signalColor }]}>
                  {signalLabel}
                </Text>
                <Text style={aStyles.proCardMetricLabel}>PROP SIGNAL</Text>
              </View>
            ) : null}
          </View>
          {cleanMechanism ? (
            <Text style={aStyles.proCardNote}>{cleanMechanism}</Text>
          ) : null}
          {signal.shadowMultiplier != null && meaningfulSignal ? (
            <Text style={[aStyles.proCardNote, { color: signalColor }]}>
              Positional read: {signalLabel} · ×{Number(signal.shadowMultiplier).toFixed(3)}.
            </Text>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

/** Legacy tactical packet renderer retained for saved-data compatibility only. */
function renderLegacyTacticalIntelligence(data: Record<string, unknown> | null) {
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
  const signal = positional.propSignal ?? {};
  const robust = positional.robustEvidence ?? {};
  const signalColor = signal.shadowDirection === 'higher_volume'
    ? Colors.success
    : signal.shadowDirection === 'lower_volume'
    ? Colors.error
    : Colors.textSecondary;
  const signalLabel = String(signal.shadowDirection ?? 'neutral').replace(/_/g, ' ');
  const confidencePct = matchScript.confidence != null ? `${Math.round(Number(matchScript.confidence) * 100)}%` : '—';
  const opponentHistory = ti?.playerOpponentHistory ?? (data as any)?.h2hPlayerStats?.opponentHitRate;
  const positionCohort = ti?.positionCohort ?? (data as any)?.positionComparison;
  const roleSource = String(player.roleSource ?? (data as any)?.tacticalContext?.roleSource ?? '');
  const roleSourceLabel = roleSource === 'fixture_lineup_observation'
    ? 'confirmed fixture lineup'
    : roleSource === 'h2h_fixture_position_history'
    ? 'observed H2H lineup history'
    : roleSource === 'cached_role_resolver'
    ? 'cached role resolver'
    : roleSource.replace(/_/g, ' ') || 'role resolver';
  const cohortAverage = positionCohort?.average ?? positionCohort?.avgStatValue;
  const cohortSample = Number(positionCohort?.sampleSize ?? 0);
  const cohortMinimum = Number(positionCohort?.minimumRecommendedSample ?? 10);
  const hasOpponentHistory = Number(opponentHistory?.sampleSize ?? 0) > 0;
  const hasCohort = cohortSample > 0;

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
      </View>
      <Text style={aStyles.proCardNote}>
        Role evidence: <Text style={{ fontWeight: '800' }}>{roleSourceLabel}</Text>
        {player.roleSampleSize ? ` · n=${player.roleSampleSize}` : ''}
        {player.roleConfidence ? ` · ${player.roleConfidence} confidence` : ''}
      </Text>

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
      {(hasOpponentHistory || hasCohort || ti?.tacticalConclusion) && (
        <View style={aStyles.intelSection}>
          <View style={aStyles.intelSectionHeader}>
            <Text style={aStyles.intelSectionTitle}>PLAYER · OPPONENT EVIDENCE</Text>
          </View>
          {hasOpponentHistory ? (
            <Text style={aStyles.proCardNote}>
              {opponentHistory?.opponent || 'This opponent'}: {opponentHistory?.overPct ?? '—'}% OVER
              {opponentHistory?.underPct != null ? ` · ${opponentHistory.underPct}% UNDER` : ''}
              {' '}from n={opponentHistory?.sampleSize} verified player appearances
              {opponentHistory?.evidenceStatus === 'thin' ? ' · thin sample' : ''}.
            </Text>
          ) : (
            <Text style={aStyles.proCardNote}>
              No verified player-level appearance history against this opponent was found.
            </Text>
          )}
          {hasCohort ? (
            <>
            <Text style={aStyles.proCardNote}>
             {positionCohort?.opponent || 'Opponent'} matchup sample: comparable{' '}
             {cohortPositionLabel(positionCohort?.targetPosition || positionCohort?.positionShort)}{' '}
             players averaged {cohortAverage ?? '—'} {PROP_LABELS[positionCohort?.propType ?? '']?.toLowerCase() ?? String(positionCohort?.propType ?? 'prop').replace(/_/g, ' ')}
             {positionCohort?.venue ? ` in matching ${positionCohort.venue} fixtures` : ''}
             {' '}· n={cohortSample}
              {positionCohort?.overHitRate != null ? ` · ${positionCohort.overHitRate}% OVER` : ''}
              {cohortSample < cohortMinimum ? ` · limited (target n≥${cohortMinimum})` : ' · sufficient sample'}.
            </Text>
            {Array.isArray(positionCohort?.players) && positionCohort.players.length > 0 ? (
              <Text style={aStyles.proCardNote}>
                Similar players: {positionCohort.players.slice(0, 6).map((p: any) => p.playerName || p.name).filter(Boolean).join(', ')}
                {positionCohort.players.length > 6 ? ` +${positionCohort.players.length - 6} more` : ''}.
              </Text>
            ) : null}
            </>
          ) : (
            <Text style={aStyles.proCardNote}>
              No valid same-position opponent cohort was available.
            </Text>
          )}
          {ti?.tacticalConclusion ? (
            <Text style={[aStyles.proCardNote, { color: Colors.text }]}>
              {ti.tacticalConclusion}
            </Text>
          ) : null}
        </View>
      )}
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
  tacticalVerdictCard: {
    backgroundColor: 'rgba(20,20,20,0.98)', borderRadius: 11,
    borderWidth: 1, padding: 12, marginBottom: 8, gap: 8,
  },
  tacticalVerdictLead: {
    fontSize: 12.5, color: Colors.text, lineHeight: 19, fontWeight: '600',
  },
  tacticalVerdictConclusion: {
    fontSize: 11.5, lineHeight: 17, fontWeight: '800',
    borderTopWidth: 1, borderTopColor: Colors.borderSubtle, paddingTop: 8,
  },
  tacticalContextFlat: {
    borderLeftWidth: 2,
    paddingLeft: 11,
    marginBottom: 14,
    gap: 4,
  },
  tacticalIntelligenceFlat: {
    borderLeftWidth: 2,
    paddingLeft: 11,
    marginBottom: 14,
    gap: 7,
  },
  flatContextHeader: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 8,
    marginBottom: 1,
  },
  flatContextKicker: {
    fontSize: 8,
    fontWeight: '900',
    letterSpacing: 1.05,
  },
  flatContextTitle: {
    flex: 1,
    color: Colors.text,
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.15,
  },
  flatContextValue: {
    color: Colors.text,
    fontSize: 13,
    fontWeight: '800',
    marginTop: 2,
    textTransform: 'capitalize',
  },
  flatContextNote: {
    color: Colors.textSecondary,
    fontSize: 10,
    lineHeight: 14,
  },
  flatRoleBlock: {
    paddingVertical: 4,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
  },
  flatRoleValue: {
    color: Colors.text,
    fontSize: 13,
    fontWeight: '800',
    textTransform: 'capitalize',
  },
  flatIntelSection: {
    gap: 6,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: Colors.borderSubtle,
  },
  flatSectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  flatMetricRow: {
    flexDirection: 'row',
    gap: 24,
  },
  flatMetricCell: {
    flex: 1,
    minWidth: 0,
  },
  flatMetricValue: {
    color: Colors.text,
    fontSize: 12,
    fontWeight: '800',
    textTransform: 'capitalize',
  },
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
