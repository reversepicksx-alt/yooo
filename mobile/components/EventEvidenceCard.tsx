import React from 'react';
import { Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';

type PositionRow = {
  attempted?: number;
  completed?: number;
  per90?: number;
};

type PositionPassEvidence = {
  status?: string;
  provider?: string;
  normalization?: string;
  targetTeam?: Record<string, PositionRow>;
  opponent?: Record<string, PositionRow>;
  opponentAllowedToTargetPositions?: Record<string, PositionRow>;
  sampleMatches?: number;
  limitations?: string[];
  reason?: string;
};

function label(position: string) {
  return position === 'LM/LW' ? 'LM / LW' : position === 'RM/RW' ? 'RM / RW' : position;
}

export default function EventEvidenceCard({
  data,
}: {
  data: PositionPassEvidence | null | undefined;
}) {
  if (!data) return null;
  const verified = data.status === 'event_derived';
  const opponentRows = Object.entries(
    data.opponentAllowedToTargetPositions ?? data.targetTeam ?? {},
  )
    .filter(([, row]) => Number(row?.completed ?? 0) > 0 || Number(row?.attempted ?? 0) > 0)
    .sort((a, b) => Number(b[1]?.completed ?? 0) - Number(a[1]?.completed ?? 0));
  if (!verified || opponentRows.length === 0) {
    return null;
  }

  return (
    <View style={{
      marginTop: 8,
      paddingHorizontal: 12,
      paddingVertical: 10,
      borderRadius: 10,
      backgroundColor: '#0A0A0A',
      borderWidth: 1,
      borderColor: 'rgba(96,165,250,0.28)',
    }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 7 }}>
        <Ionicons name="pulse-outline" size={12} color="#60A5FA" />
        <Text style={{ fontSize: 9, color: '#60A5FA', fontWeight: '800', letterSpacing: 1 }}>
          EVENT EVIDENCE
        </Text>
        <Text style={{ marginLeft: 'auto', fontSize: 8, color: Colors.textTertiary }}>
          {data.sampleMatches ?? 1} exact match
        </Text>
      </View>
      <Text style={{ fontSize: 11, color: Colors.textSecondary, lineHeight: 16 }}>
        Completed passes received by verified lineup positions. This is match evidence,
        not a league baseline, and remains shadow-only.
      </Text>
      {opponentRows.length > 0 && (
        <View style={{ marginTop: 8 }}>
          <Text style={{ fontSize: 8, color: Colors.textTertiary, fontWeight: '800', letterSpacing: 0.6 }}>
            OPPONENT ALLOWED · TARGET POSITIONS
          </Text>
          {opponentRows.slice(0, 5).map(([position, row]) => (
            <View key={`opp-${position}`} style={{ flexDirection: 'row', alignItems: 'center', marginTop: 4 }}>
              <Text style={{ flex: 1, fontSize: 10, color: Colors.textSecondary }}>{label(position)}</Text>
              <Text style={{ fontSize: 10, color: '#60A5FA', fontWeight: '800' }}>
                {row.completed ?? 0} completed
              </Text>
              <Text style={{ width: 74, textAlign: 'right', fontSize: 9, color: Colors.textTertiary }}>
                {row.attempted ?? 0} attempted
              </Text>
            </View>
          ))}
        </View>
      )}
      <Text style={{ marginTop: 8, fontSize: 8, color: Colors.textTertiary, lineHeight: 12 }}>
        Source: {data.provider ?? 'StatsBomb Open Data'} · {data.normalization ?? 'exact-match event count'}
      </Text>
    </View>
  );
}