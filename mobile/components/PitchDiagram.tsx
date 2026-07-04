import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Svg, { Rect, Line, Circle, G, Text as SvgText } from 'react-native-svg';
import Colors from '@/constants/colors';
import type { PredictionResult } from '@/lib/api';

interface Props {
  lineup: NonNullable<PredictionResult['lineup']>;
  highlightPlayerName?: string;
}

const PITCH_W = 320;
const PITCH_H = 200;

function TeamDots({
  players,
  highlightPlayerName,
}: {
  players: { name: string; x: number; y: number; number?: number; position?: string }[];
  highlightPlayerName?: string;
}) {
  return (
    <>
      {players.map((p, i) => {
        const cx = p.x * PITCH_W;
        const cy = p.y * PITCH_H;
        const isHighlighted =
          !!highlightPlayerName &&
          p.name?.toLowerCase().includes(highlightPlayerName.toLowerCase());
        return (
          <G key={`${p.name}-${i}`}>
            <Circle
              cx={cx}
              cy={cy}
              r={isHighlighted ? 9 : 7}
              fill={isHighlighted ? Colors.primary : '#1c1c1c'}
              stroke={isHighlighted ? '#0a2e05' : '#3a3a3a'}
              strokeWidth={isHighlighted ? 2 : 1}
            />
            <SvgText
              x={cx}
              y={cy + 3.5}
              fontSize={7.5}
              fontWeight="700"
              fill={isHighlighted ? '#000' : '#aaa'}
              textAnchor="middle"
            >
              {p.number ?? ''}
            </SvgText>
            <SvgText
              x={cx}
              y={cy + (p.y < 0.5 ? -12 : 16)}
              fontSize={8}
              fontWeight={isHighlighted ? '800' : '500'}
              fill={isHighlighted ? Colors.primary : Colors.textTertiary}
              textAnchor="middle"
            >
              {p.name?.split(' ').slice(-1)[0]?.slice(0, 12) ?? ''}
            </SvgText>
          </G>
        );
      })}
    </>
  );
}

export default function PitchDiagram({ lineup, highlightPlayerName }: Props) {
  const { home, away, status } = lineup;

  return (
    <View style={styles.wrap}>
      <View style={styles.headerRow}>
        <View style={styles.teamTag}>
          <Text style={styles.teamTagName} numberOfLines={1}>{home.teamName ?? 'Home'}</Text>
          <Text style={styles.formation}>{home.formation ?? '—'}</Text>
        </View>
        <View style={[styles.statusPill, status === 'confirmed' ? styles.statusConfirmed : styles.statusPredicted]}>
          <View style={[styles.statusDot, { backgroundColor: status === 'confirmed' ? Colors.primary : '#FFB020' }]} />
          <Text style={[styles.statusText, { color: status === 'confirmed' ? Colors.primary : '#FFB020' }]}>
            {status === 'confirmed' ? 'CONFIRMED XI' : 'PROJECTED XI'}
          </Text>
        </View>
        <View style={[styles.teamTag, { alignItems: 'flex-end' }]}>
          <Text style={styles.teamTagName} numberOfLines={1}>{away.teamName ?? 'Away'}</Text>
          <Text style={styles.formation}>{away.formation ?? '—'}</Text>
        </View>
      </View>

      <View style={styles.pitchOuter}>
        <Svg width={PITCH_W} height={PITCH_H} viewBox={`0 0 ${PITCH_W} ${PITCH_H}`}>
          <Rect x={0} y={0} width={PITCH_W} height={PITCH_H} rx={10} fill="#0c1b0c" stroke="#1f3a1f" strokeWidth={1.5} />
          {[0.2, 0.4, 0.6, 0.8].map(f => (
            <Rect key={f} x={0} y={f * PITCH_H - 0.4} width={PITCH_W} height={0.8} fill="#173417" />
          ))}
          <Line x1={0} y1={PITCH_H / 2} x2={PITCH_W} y2={PITCH_H / 2} stroke="#2a4a2a" strokeWidth={1.2} />
          <Circle cx={PITCH_W / 2} cy={PITCH_H / 2} r={26} stroke="#2a4a2a" strokeWidth={1.2} fill="none" />
          <Rect x={PITCH_W / 2 - 55} y={0} width={110} height={26} stroke="#2a4a2a" strokeWidth={1.2} fill="none" />
          <Rect x={PITCH_W / 2 - 55} y={PITCH_H - 26} width={110} height={26} stroke="#2a4a2a" strokeWidth={1.2} fill="none" />

          <TeamDots players={home.players} highlightPlayerName={highlightPlayerName} />
          <TeamDots players={away.players} highlightPlayerName={highlightPlayerName} />
        </Svg>
      </View>

      <View style={styles.coachRow}>
        <Text style={styles.coachText} numberOfLines={1}>{home.coach ? `Coach: ${home.coach}` : ''}</Text>
        <Text style={styles.coachText} numberOfLines={1}>{away.coach ? `Coach: ${away.coach}` : ''}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    backgroundColor: '#0a0a0a',
    borderRadius: 16,
    padding: 14,
    borderWidth: 1,
    borderColor: '#1c1c1c',
  },
  headerRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  teamTag: { flex: 1 },
  teamTagName: { fontSize: 12, fontWeight: '700', color: Colors.text },
  formation: { fontSize: 10, color: Colors.textTertiary, fontWeight: '600', marginTop: 1 },
  statusPill: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingHorizontal: 8, paddingVertical: 4, borderRadius: 20,
    borderWidth: 1,
  },
  statusConfirmed: { backgroundColor: Colors.primary + '15', borderColor: Colors.primary + '40' },
  statusPredicted: { backgroundColor: '#FFB02015', borderColor: '#FFB02040' },
  statusDot: { width: 5, height: 5, borderRadius: 2.5 },
  statusText: { fontSize: 8.5, fontWeight: '800', letterSpacing: 0.6 },
  pitchOuter: { alignItems: 'center', justifyContent: 'center' },
  coachRow: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 8 },
  coachText: { fontSize: 9, color: Colors.textTertiary, flex: 1 },
});
