import React from 'react';
import { Text, View } from 'react-native';
import Colors from '@/constants/colors';

type CohortPlayer = {
  playerId?: number;
  teamId?: number;
  name?: string;
  team?: string;
  statValue?: number | null;
  passAttempts?: number | null;
  teamPossession?: number | null;
  oppPossession?: number | null;
  tp?: number | null;
  crossPropStats?: Record<string, number>;
  minutes?: number | null;
  minutesPlayed?: number | null;
  position?: string;
  matchPosition?: string | null;
  observedPosition?: string | null;
  role?: string;
  date?: string;
  positionVerified?: boolean;
  positionSource?: string;
  roleMatchApplied?: boolean;
  roleInferred?: boolean;
};

export type SameRoleEvidence = {
  targetPosition?: string;
  targetRole?: string;
  positionShort?: string;
  opponent?: string;
  venue?: string;
  propType?: string;
  avgStatValue?: number | null;
  average?: number | null;
  weightedAverage?: number | null;
  avgPossession?: number | null;
  avgOpponentPossession?: number | null;
  expectedPlayerPossession?: number | null;
  possessionSampleSize?: number;
  teamPossessionSampleSize?: number;
  opponentPossessionSampleSize?: number;
  possessionStatus?: 'verified' | 'estimated' | 'unavailable' | string;
  possessionSource?: string | null;
  possessionComparison?: string;
  sampleSize?: number;
  minimumRecommendedSample?: number;
  sampleStatus?: string;
  overHitRate?: number | null;
  underHitRate?: number | null;
  players?: CohortPlayer[];
  sourceScope?: string;
  comparisonMode?: 'same-position' | 'same-role' | string;
  positionEvidenceType?: 'exact_position' | 'broad_category' | 'unavailable' | string;
  positionEvidenceNote?: string;
  comparisonUnavailableReason?: string | null;
  comparisonFixtureCount?: number;
  comparisonVenueFixtureCount?: number;
  verdict?: {
    verdict?: 'verifies' | 'contradicts' | 'neutral' | 'unavailable' | string;
    reason?: string;
    average?: number | null;
    line?: number | null;
    sampleSize?: number;
    recommendation?: string | null;
  };
  crossPropAverages?: Record<string, number>;
  crossPropSampleSizes?: Record<string, number>;
  weightMethod?: string;
  unweightedAverage?: number | null;
  sampleUnit?: 'team' | 'player' | string;
};

function label(value: unknown) {
  return String(value || 'prop').replace(/_/g, ' ').toUpperCase();
}

function positionLabel(value: unknown) {
  const labels: Record<string, string> = {
    GK: 'Goalkeeper',
    CB: 'Centre back',
    LB: 'Left back',
    RB: 'Right back',
    LWB: 'Left wing-back',
    RWB: 'Right wing-back',
    CDM: 'Defensive midfielder',
    CM: 'Central midfielder',
    CAM: 'Attacking midfielder',
    LM: 'Left midfielder',
    RM: 'Right midfielder',
    LW: 'Left winger',
    RW: 'Right winger',
    CF: 'Centre forward',
    ST: 'Striker',
    SS: 'Second striker',
    D: 'Defender',
    DEF: 'Defender',
    M: 'Midfielder',
    MID: 'Midfielder',
    F: 'Attacker',
    FWD: 'Attacker',
  };
  const normalized = String(value || '').trim().toUpperCase();
  return labels[normalized] || String(value || 'Position unavailable');
}

function formalPositionCode(value: unknown) {
  const normalized = String(value || '').trim().toUpperCase().replace(/[\s_-]+/g, '');
  const codes: Record<string, string> = {
    GOALKEEPER: 'GK',
    GOALKEEPERS: 'GK',
    GOALIE: 'GK',
    GOALKEEPERPOSITION: 'GK',
    CENTREBACK: 'CB',
    CENTERBACK: 'CB',
    DEFENDER: 'DEF',
    DEF: 'DEF',
    D: 'DEF',
    LEFTBACK: 'LB',
    RIGHTBACK: 'RB',
    LEFTWINGBACK: 'LWB',
    RIGHTWINGBACK: 'RWB',
    DEFENSIVEMIDFIELDER: 'CDM',
    MIDFIELDER: 'MID',
    MID: 'MID',
    M: 'MID',
    CENTRALMIDFIELDER: 'CM',
    ATTACKINGMIDFIELDER: 'CAM',
    LEFTMIDFIELDER: 'LM',
    RIGHTMIDFIELDER: 'RM',
    LEFTWINGER: 'LW',
    RIGHTWINGER: 'RW',
    ATTACKER: 'FWD',
    FORWARD: 'FWD',
    FWD: 'FWD',
    F: 'FWD',
    CENTREFORWARD: 'CF',
    CENTERFORWARD: 'CF',
    STRIKER: 'ST',
    SECONDSTRIKER: 'SS',
  };
  return codes[normalized] || normalized || 'POS?';
}
function cohortSubject(value: unknown) {
  const normalized = String(value || '').trim().toUpperCase().replace(/\s+/g, '');
  const labels: Record<string, string> = {
    GK: 'Goalkeepers',
    G: 'Goalkeepers',
    GOALKEEPER: 'Goalkeepers',
    GOALKEEPERS: 'Goalkeepers',
    CB: 'Centre-backs',
    CENTREBACK: 'Centre-backs',
    CENTERBACK: 'Centre-backs',
    DEFENDER: 'Defenders',
    DEFENDERS: 'Defenders',
    LB: 'Left-backs',
    RB: 'Right-backs',
    LWB: 'Left wing-backs',
    RWB: 'Right wing-backs',
    DEF: 'Defenders',
    D: 'Defenders',
    CDM: 'Defensive midfielders',
    DM: 'Defensive midfielders',
    CM: 'Central midfielders',
    MID: 'Midfielders',
    M: 'Midfielders',
    CAM: 'Attacking midfielders',
    AM: 'Attacking midfielders',
    LM: 'Left midfielders',
    RM: 'Right midfielders',
    LW: 'Left wingers',
    RW: 'Right wingers',
    CF: 'Forwards',
    ST: 'Strikers',
    SS: 'Second strikers',
    F: 'Forwards',
    FWD: 'Forwards',
  };
  return labels[normalized] || `${positionLabel(value)} players`;
}

function cohortPropLabel(value: unknown) {
  const labels: Record<string, string> = {
    pass_attempts: 'pass attempts',
    passes: 'passes',
    shots: 'shots',
    shots_on_target: 'shots on target',
    goals: 'goals',
    assists: 'assists',
    shots_assisted: 'shot assists',
    key_passes: 'key passes',
    tackles: 'tackles',
    saves: 'saves',
    goalie_saves: 'saves',
    interceptions: 'interceptions',
    blocks: 'blocks',
    dribbles: 'dribbles',
    dribbles_success: 'successful dribbles',
    fouls_drawn: 'fouls drawn',
    fouls_committed: 'fouls committed',
    crosses: 'crosses',
    clearances: 'clearances',
    duels_won: 'duels won',
    yellow_cards: 'yellow cards',
  };
  const raw = String(value || 'prop');
  return labels[raw] || raw.replace(/_/g, ' ');
}

function scopeIncludesPosition(value: unknown) {
  return String(value || '').includes('same_position');
}

function newestFirst(a: CohortPlayer, b: CohortPlayer) {
  const aTime = Date.parse(String(a.date || ''));
  const bTime = Date.parse(String(b.date || ''));
  if (Number.isFinite(aTime) && Number.isFinite(bTime) && aTime !== bTime) {
    return bTime - aTime;
  }
  return String(b.date || '').localeCompare(String(a.date || ''));
}

function displayDate(value: unknown) {
  return String(value || '').slice(0, 10) || 'DATE UNAVAILABLE';
}

/**
 * Exact-opponent, same-position or same-role evidence. This is deliberately separate from
 * Same-role evidence is kept focused on the comparison cohort shown above.
 */
export default function SameRoleEvidenceCard({
  data,
  recommendation,
  line,
}: {
  data?: SameRoleEvidence | null;
  recommendation?: string | null;
  line?: number | null;
}) {
  if (!data) return null;

  const sample = Number(data.sampleSize || 0);
  const minimum = Number(data.minimumRecommendedSample || 15);
  const average = data.average ?? data.avgStatValue;
  const rec = String(recommendation || '').toUpperCase();
  const numericLine = Number(line);
  const numericAverage = Number(average);
  const hasVerdict = Number.isFinite(numericLine) && Number.isFinite(numericAverage);
  const broadPositionOnly = data.positionEvidenceType === 'broad_category';

  // Broad D/M/F cohorts combine different tactical jobs (CB with fullback,
  // holding midfielder with winger, etc.). They are provenance/audit context,
  // not subscriber decision evidence, and made unrelated searches look like
  // the same generic result. Never render them as a player comparison.
  if (broadPositionOnly) return null;
  const verdict = broadPositionOnly
    ? 'CONTEXT'
    : String(data.verdict?.verdict || (
        hasVerdict
          ? rec === 'OVER' && numericAverage > numericLine || rec === 'UNDER' && numericAverage < numericLine
            ? 'verifies'
            : rec === 'OVER' || rec === 'UNDER' ? 'contradicts' : 'neutral'
          : 'unavailable'
      )).toUpperCase();
  const verdictColor = verdict === 'VERIFIES'
    ? Colors.success
    : verdict === 'CONTRADICTS' ? Colors.error : '#F59E0B';
  const prop = label(data.propType);
  const isSamePosition = data.comparisonMode === 'same-position'
    || scopeIncludesPosition(data.sourceScope);
  const exactPositionUnavailable = !broadPositionOnly && (
    data.positionEvidenceType === 'unavailable'
      || data.comparisonMode === 'unavailable'
  );
  const unavailableReason = String(
    data.positionEvidenceNote
      || data.comparisonUnavailableReason
      || '',
  ).trim();
  const role = isSamePosition ? '' : (data.targetRole || 'same role');
  const position = positionLabel(data.targetPosition || data.positionShort || 'same position');
  const limited = sample < minimum;
  const sourcePlayers = (data.players || []).slice().sort(newestFirst).slice(0, 15);
  const hasSourcePlayers = sourcePlayers.length > 0;
  const scope = String(data.sourceScope || '');
  const sampleUnit = String(data.sampleUnit || '').toLowerCase() === 'team'
    ? 'team'
    : 'player';
  const cohortUnitLabel = sampleUnit === 'team'
    ? 'source team'
    : broadPositionOnly
      ? 'broad-category player'
      : isSamePosition ? 'exact-position player' : 'same-role player';
  const scopeLabel = scope.includes('broad_category')
    ? `broad ${position.toLowerCase()} rows from ${data.venue || 'venue'} fixtures`
    : scope.includes('mixed_venue')
      ? 'same-opponent mixed-venue fixtures'
      : scope.includes('prior_seasons')
        ? `same-opponent ${data.venue || 'venue'} fixtures, including prior seasons`
        : `matching ${data.venue || 'venue'} fixtures`;
  const cohortAverageText = average != null ? Number(average).toFixed(1) : '—';
  const cohortSentence = average != null && sample > 0
    ? `Against ${data.opponent || 'this opponent'}, comparable ${cohortSubject(
        data.targetPosition || data.positionShort,
      ).toLowerCase()} averaged ${cohortAverageText} ${cohortPropLabel(data.propType)} in ${scopeLabel}.`
    : `No verified comparable player average is available for ${data.opponent || 'this opponent'}.`;
  const avgPossession = Number(data.avgPossession);
  const avgOpponentPossession = Number(data.avgOpponentPossession);
  const expectedPlayerPossession = Number(data.expectedPlayerPossession);
  const possessionSampleSize = Number(data.possessionSampleSize || 0);
  const teamPossessionSampleSize = Number(
    data.teamPossessionSampleSize || possessionSampleSize,
  );
  const opponentPossessionSampleSize = Number(
    data.opponentPossessionSampleSize || possessionSampleSize,
  );
  const possessionStatus = String(data.possessionStatus || (
    possessionSampleSize > 0 ? 'verified' : 'unavailable'
  )).toLowerCase();
  const hasPossessionComparison =
    Number.isFinite(avgPossession) &&
    Number.isFinite(avgOpponentPossession) &&
    possessionStatus !== 'unavailable';
  const hasComparableCohort = average != null && sample > 0;
  // An unavailable exact position is a valid evidence outcome, but it should
  // not consume the same visual weight as a verified comparison cohort. Keep
  // the disclosure visible without presenting broad-category rows as a
  // failed or misleading full analysis.
  if (exactPositionUnavailable && !hasComparableCohort && !broadPositionOnly) {
    const targetLabel = positionLabel(data.targetPosition || data.positionShort);
    const venueFixtureCount = Number(data.comparisonVenueFixtureCount || 0);
    const statusText = String(data.comparisonUnavailableReason || '').toLowerCase();
    const evidenceDetail = statusText === 'provider_timeout' || statusText === 'provider_unavailable'
      ? `Matching ${String(data.venue || 'venue').toUpperCase()} fixtures were found, but the comparison provider did not finish before the response limit. This does not mean ${data.opponent || 'the opponent'} has never faced ${targetLabel.toLowerCase()}s.`
      : venueFixtureCount > 0
        ? `No verified ${targetLabel.toLowerCase()} source-player rows were returned from ${venueFixtureCount} matching ${String(data.venue || 'venue').toUpperCase()} fixtures. Missing optional fields are not treated as zero.`
        : 'No matching venue fixtures were available in the bounded comparison window. This evidence does not change the projection.';
    return (
      <View style={{
        marginTop: 8,
        paddingHorizontal: 11,
        paddingVertical: 9,
        borderRadius: 9,
        backgroundColor: 'rgba(245,158,11,0.045)',
        borderWidth: 1,
        borderColor: 'rgba(245,158,11,0.34)',
      }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Text style={{ fontSize: 9, color: '#F59E0B', fontWeight: '900', letterSpacing: 0.9 }}>
            POSITION EVIDENCE
          </Text>
          <Text style={{ marginLeft: 'auto', fontSize: 9, color: '#F59E0B', fontWeight: '900' }}>
            UNAVAILABLE
          </Text>
        </View>
        <Text style={{ fontSize: 11, color: Colors.text, fontWeight: '800', lineHeight: 15, marginTop: 4 }}>
          No verified comparable {targetLabel.toLowerCase()} average is available for {data.opponent || 'this opponent'}.
        </Text>
        <Text style={{ fontSize: 9.5, color: Colors.textTertiary, lineHeight: 14, marginTop: 2 }}>
          {unavailableReason && statusText !== 'no_verified_exact_position_rows'
            ? unavailableReason
            : 'No recent same-position player evidence was verified. This does not change the projection.'}
        </Text>
      </View>
    );
  }

  return (
    <View style={{
      marginTop: 8,
      paddingHorizontal: 12,
      paddingVertical: 11,
      borderRadius: 10,
      backgroundColor: '#0A0A0A',
      borderWidth: 1,
      borderColor: `${verdictColor}55`,
    }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 7 }}>
        <Text style={{ fontSize: 9, color: verdictColor, fontWeight: '900', letterSpacing: 1 }}>
           {broadPositionOnly
             ? `BROAD ${position.toUpperCase()} EVIDENCE`
             : exactPositionUnavailable
             ? 'EXACT-POSITION EVIDENCE UNAVAILABLE'
             : isSamePosition
             ? `EXACT ${positionLabel(data.targetPosition || data.positionShort || 'POSITION').toUpperCase()} EVIDENCE`
             : 'SAME-ROLE OPPONENT EVIDENCE'}
        </Text>
        <Text style={{ marginLeft: 'auto', fontSize: 9, color: verdictColor, fontWeight: '900' }}>
          {verdict}
        </Text>
      </View>
       {hasPossessionComparison && (
         <View style={{
           marginTop: 0,
           marginBottom: 2,
           paddingBottom: 7,
           borderBottomWidth: 1,
           borderBottomColor: 'rgba(255,255,255,0.08)',
         }}>
           <Text style={{ fontSize: 9, color: Colors.textSecondary, fontWeight: '900', letterSpacing: 0.8 }}>
             POSSESSION CONTEXT · TEAM SCHEDULES
           </Text>
           <Text style={{ fontSize: 10, color: Colors.text, lineHeight: 15, marginTop: 4 }}>
             The selected team schedule averaged {avgPossession.toFixed(0)}% possession
             {' '}vs {avgOpponentPossession.toFixed(0)}% for {data.opponent || 'the opponent'}.
           </Text>
           <Text style={{ fontSize: 9, color: Colors.textTertiary, lineHeight: 14, marginTop: 2 }}>
             {possessionStatus === 'verified'
               ? `${teamPossessionSampleSize} selected-team matches and ${opponentPossessionSampleSize} opponent matches with verified possession · player appearances not required · context only`
               : 'Estimated possession context · context only'}
           </Text>
         </View>
       )}
       <Text style={{ fontSize: 12, color: Colors.text, fontWeight: '800', lineHeight: 17 }}>
         {cohortSentence}
       </Text>
       <Text style={{ fontSize: 10, color: Colors.textSecondary, lineHeight: 15, marginTop: 4 }}>
           {sample > 0
              ? `${sample} distinct ${cohortUnitLabel}${sample === 1 ? '' : 's'} in ${scopeLabel}`
            : broadPositionOnly
              ? `No verified broad ${position.toLowerCase()} source-player rows were returned`
              : `No exact ${positionLabel(data.targetPosition || data.positionShort || 'position')} source-player rows were returned`}
        {hasVerdict ? ` · line ${numericLine.toFixed(1)} · pick ${rec}` : ''}
        {limited ? ` · limited sample (target n≥${minimum})` : ' · target sample reached'}
      </Text>
      <Text style={{ fontSize: 9, color: Colors.textTertiary, lineHeight: 14, marginTop: 3 }}>
         {data.positionEvidenceNote
           || 'Exact observed position is required; tactical role is context only.'}
      </Text>
      {(data.overHitRate != null || data.underHitRate != null) && (
        <Text style={{ fontSize: 10, color: Colors.textTertiary, lineHeight: 15, marginTop: 3 }}>
          Against this line: {data.overHitRate != null ? `${data.overHitRate}% OVER` : 'OVER —'}
          {' · '}
          {data.underHitRate != null ? `${data.underHitRate}% UNDER` : 'UNDER —'}
        </Text>
      )}
      {hasSourcePlayers && (
        <View style={{
          marginTop: 8,
          paddingTop: 7,
          borderTopWidth: 1,
          borderTopColor: 'rgba(255,255,255,0.08)',
        }}>
             <Text style={{ fontSize: 10, color: Colors.textSecondary, fontWeight: '900', letterSpacing: 0.8, marginBottom: 5 }}>
              {sampleUnit === 'team' ? 'SOURCE TEAMS' : 'SOURCE PLAYERS'} · {prop} / POSSESSION
          </Text>
          {sourcePlayers.map((player, index) => {
            const statValue = player.statValue
              ?? player.passAttempts
              ?? player.crossPropStats?.[data.propType || ''];
            const observedPosition = player.position || player.matchPosition || player.observedPosition;
            const formalCode = formalPositionCode(observedPosition);
            const positionVerified = player.positionVerified === true;
            const roleText = String(player.role || '').trim();
            const roleLabel = roleText
              ? `${roleText.toUpperCase()}${player.roleInferred ? ' · INFERRED' : ''}`
              : 'ROLE UNAVAILABLE';
            return (
              <View
                key={`${player.playerId || player.name || 'player'}-${index}`}
                style={{
                  flexDirection: 'row',
                  alignItems: 'flex-start',
                  paddingVertical: 5,
                  borderBottomWidth: index < sourcePlayers.length - 1 ? 1 : 0,
                  borderBottomColor: 'rgba(255,255,255,0.05)',
                }}
              >
                 <View style={{ flex: 1, paddingRight: 8 }}>
                   <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
                  <Text
                     numberOfLines={1}
                     style={{ flex: 1, fontSize: 12, color: Colors.text, lineHeight: 17, fontWeight: '800' }}
                  >
                    {index + 1}. {player.name || 'Unknown player'}
                  </Text>
                   <View style={{
                     paddingHorizontal: 5,
                     paddingVertical: 1,
                     borderRadius: 4,
                     borderWidth: 1,
                     borderColor: positionVerified ? 'rgba(52,199,89,0.55)' : 'rgba(245,158,11,0.55)',
                     backgroundColor: positionVerified ? 'rgba(52,199,89,0.10)' : 'rgba(245,158,11,0.10)',
                   }}>
                     <Text style={{
                       fontSize: 8,
                       color: positionVerified ? Colors.success : '#F59E0B',
                       fontWeight: '900',
                       letterSpacing: 0.5,
                     }}>
                       {formalCode}
                     </Text>
                   </View>
                   </View>
                  <Text
                    numberOfLines={2}
                    style={{ fontSize: 10.5, color: Colors.textSecondary, lineHeight: 15, marginTop: 1 }}
                  >
                     {player.team || 'Team unavailable'} · {roleLabel}
                     {' · '}{displayDate(player.date)}
                  </Text>
                </View>
                <View style={{ width: 142, alignItems: 'flex-end' }}>
                  <Text style={{ fontSize: 11, color: Colors.primary, fontWeight: '900', lineHeight: 17 }}>
                    {label(data.propType)} {statValue != null ? Number(statValue).toFixed(0) : '—'}
                  </Text>
                  <Text style={{ fontSize: 10, color: Colors.textSecondary, fontWeight: '800', lineHeight: 15, marginTop: 1 }}>
                    {player.tp != null || player.teamPossession != null
                      ? `TP ${Number(player.tp ?? player.teamPossession).toFixed(0)}%`
                      : 'TP —'}
                  </Text>
                  <Text style={{ fontSize: 10, color: Colors.textSecondary, fontWeight: '800', lineHeight: 15 }}>
                    MIN {player.minutesPlayed ?? player.minutes ?? '—'}
                  </Text>
                </View>
              </View>
            );
          })}
        </View>
      )}
       {!hasSourcePlayers && (
        <Text style={{ fontSize: 10, color: Colors.textTertiary, lineHeight: 15, marginTop: 8 }}>
           Exact-position evidence is unavailable for this opponent window; broad-category rows are intentionally not relabeled as {position}.
        </Text>
      )}
    </View>
  );
}