import React from 'react';
import { Text, View } from 'react-native';
import Colors from '@/constants/colors';

type CohortPlayer = {
  playerId?: number;
  name?: string;
  team?: string;
  statValue?: number | null;
  passAttempts?: number | null;
  crossPropStats?: Record<string, number>;
  minutes?: number | null;
  position?: string;
  matchPosition?: string | null;
  observedPosition?: string | null;
  role?: string;
  date?: string;
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
  sampleSize?: number;
  minimumRecommendedSample?: number;
  sampleStatus?: string;
  overHitRate?: number | null;
  underHitRate?: number | null;
  players?: CohortPlayer[];
  sourceScope?: string;
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
};

function label(value: unknown) {
  return String(value || 'prop').replace(/_/g, ' ').toUpperCase();
}

/**
 * Exact-opponent, same-role evidence. This is deliberately separate from
 * PLAYER PROP HISTORY: the latter is the selected player's own history.
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
  if (!data || !(Number(data.sampleSize) > 0)) return null;

  const sample = Number(data.sampleSize);
  const minimum = Number(data.minimumRecommendedSample || 15);
  const average = data.average ?? data.avgStatValue;
  const rec = String(recommendation || '').toUpperCase();
  const numericLine = Number(line);
  const numericAverage = Number(average);
  const hasVerdict = Number.isFinite(numericLine) && Number.isFinite(numericAverage);
  const verdict = String(data.verdict?.verdict || (
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
  const role = data.targetRole || 'same role';
  const position = data.targetPosition || data.positionShort || 'same position';
  const limited = sample < minimum;
  const sourcePlayers = (data.players || []).slice(0, 15);
  const scope = String(data.sourceScope || '');
  const scopeLabel = scope.includes('mixed_venue')
    ? 'same-opponent home + away fixtures'
    : scope.includes('prior_seasons')
      ? `same-opponent ${data.venue || 'venue'} fixtures, including prior seasons`
      : `matching ${data.venue || 'venue'} fixtures`;

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
          SAME-ROLE OPPONENT EVIDENCE
        </Text>
        <Text style={{ marginLeft: 'auto', fontSize: 9, color: verdictColor, fontWeight: '900' }}>
          {verdict}
        </Text>
      </View>
      <Text style={{ fontSize: 12, color: Colors.text, fontWeight: '800', lineHeight: 17 }}>
        {data.opponent || 'Exact opponent'} allows an average of {average != null ? Number(average).toFixed(1) : '—'} {prop.toLowerCase()}
        {' '}to {position} · {role}
      </Text>
      <Text style={{ fontSize: 10, color: Colors.textSecondary, lineHeight: 15, marginTop: 4 }}>
         {sample} distinct same-role player{sample === 1 ? '' : 's'} in {scopeLabel}
        {hasVerdict ? ` · line ${numericLine.toFixed(1)} · pick ${rec}` : ''}
        {limited ? ` · limited sample (target n≥${minimum})` : ' · target sample reached'}
      </Text>
      <Text style={{ fontSize: 9, color: Colors.textTertiary, lineHeight: 14, marginTop: 3 }}>
        Weighted evidence average · minutes and repeated verified meetings count more
      </Text>
      {(data.overHitRate != null || data.underHitRate != null) && (
        <Text style={{ fontSize: 10, color: Colors.textTertiary, lineHeight: 15, marginTop: 3 }}>
          Against this line: {data.overHitRate != null ? `${data.overHitRate}% OVER` : 'OVER —'}
          {' · '}
          {data.underHitRate != null ? `${data.underHitRate}% UNDER` : 'UNDER —'}
        </Text>
      )}
      {sourcePlayers.length > 0 && (
        <View style={{
          marginTop: 8,
          paddingTop: 7,
          borderTopWidth: 1,
          borderTopColor: 'rgba(255,255,255,0.08)',
        }}>
          <Text style={{ fontSize: 9, color: Colors.textSecondary, fontWeight: '900', letterSpacing: 0.8, marginBottom: 4 }}>
            SOURCE PLAYERS · PASS ATTEMPTS
          </Text>
          {sourcePlayers.map((player, index) => {
            const passAttempts = player.passAttempts
              ?? player.crossPropStats?.pass_attempts
              ?? (data.propType === 'pass_attempts' ? player.statValue : null);
            return (
              <View
                key={`${player.playerId || player.name || 'player'}-${index}`}
                style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 3 }}
              >
                <Text
                  numberOfLines={1}
                  style={{ flex: 1, fontSize: 10, color: Colors.text, lineHeight: 15 }}
                >
                  {index + 1}. {player.name || 'Unknown player'}
                  {' · '}
                  {player.matchPosition || player.observedPosition || player.position || 'Position unavailable'}
                </Text>
                <Text style={{ fontSize: 10, color: Colors.primary, fontWeight: '900', marginLeft: 8 }}>
                  {passAttempts != null ? Number(passAttempts).toFixed(0) : '—'}
                </Text>
              </View>
            );
          })}
        </View>
      )}
      {Object.keys(data.crossPropAverages || {}).filter((key) => key !== data.propType).length > 0 && (
        <Text style={{ fontSize: 10, color: Colors.textTertiary, lineHeight: 15, marginTop: 3 }}>
          Role profile:{' '}
          {Object.entries(data.crossPropAverages || {})
            .filter(([key]) => key !== data.propType)
            .slice(0, 4)
            .map(([key, value]) => `${label(key)} ${Number(value).toFixed(1)}`)
            .join(' · ')}
        </Text>
      )}
      <Text style={{ fontSize: 9, color: Colors.textTertiary, lineHeight: 14, marginTop: 5 }}>
        Evidence only; it does not change the projection.
      </Text>
    </View>
  );
}