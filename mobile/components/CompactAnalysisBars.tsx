import React from 'react';
import { ScrollView, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';

type CompactPrediction = {
  line?: number | null;
  recommendation?: string | null;
  gameLogs?: Array<Record<string, any>> | null;
  h2hPlayerStats?: Record<string, any> | null;
  [key: string]: any;
};

function shortOpponent(value: unknown) {
  return String(value || '?')
    .replace(/^(al-?|fc |cf |rc |sc |cd |ud |sd |rcd |as |ss |ac |us |sp |ca |cp |ue |ce |cm |se |sk )/i, '')
    .slice(0, 4)
    .toUpperCase();
}

export function CompactAnalysisBars({ prediction }: { prediction: CompactPrediction }) {
  const logs = (prediction.gameLogs ?? [])
    .filter((game) => !game.synthetic && game.value != null)
    .slice(0, 10);
  const h2h = prediction.h2hPlayerStats ?? {};
  const playerMatches = Array.isArray(h2h.matches) ? h2h.matches : [];
  const meetingsByVenue = h2h.teamMeetingsByVenue ?? {};
  const teamMeetings = [
    ...(Array.isArray(meetingsByVenue.home)
      ? meetingsByVenue.home.map((meeting: any) => ({
          ...meeting,
          venue: 'home',
          possession: meeting.homePossession,
        }))
      : []),
    ...(Array.isArray(meetingsByVenue.away)
      ? meetingsByVenue.away.map((meeting: any) => ({
          ...meeting,
          venue: 'away',
          possession: meeting.awayPossession,
        }))
      : []),
  ];
  const h2hRows = playerMatches.length
    ? playerMatches.slice(0, 8).map((match: any) => ({
        ...match,
        possession: match.teamPossession,
        displayValue: match.targetStat,
        teamOnly: false,
      }))
    : teamMeetings.slice(0, 8).map((meeting: any) => ({
        ...meeting,
        displayValue: meeting.possession,
        teamOnly: true,
      }));

  return (
    <>
      {logs.length > 0 && (
        <View style={styles.card}>
          <View style={styles.header}>
            <View style={styles.headerLeft}>
              <Ionicons name="pulse" size={11} color={Colors.primary} />
              <Text style={styles.title}>RECENT MATCHES · {logs.length}</Text>
            </View>
            {prediction.line != null && <Text style={styles.meta}>LINE {prediction.line}</Text>}
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
            <View style={{ width: logs.length * 39 + 10 }}>
              <View style={styles.chart}>
                {logs.map((game, index) => {
                  const value = Number(game.value);
                  const maxValue = Math.max(...logs.map((item) => Number(item.value) || 0), prediction.line ?? 0, 1) * 1.18;
                  const color = prediction.line != null && value > prediction.line ? Colors.success : Colors.error;
                  const height = Math.max(10, (value / maxValue) * 112);
                  const date = game.date ? String(game.date).slice(5, 10) : '—';
                  return (
                    <View key={`${date}-${index}`} style={styles.barColumn}>
                      <Text style={[styles.value, { color }]}>{game.value}</Text>
                      <View style={[styles.bar, { height, backgroundColor: color + 'B8' }]} />
                      <Text style={styles.date}>{date}</Text>
                      <Text style={[styles.opponent, { color: game.venue === 'home' ? Colors.success : '#60A5FA' }]}>
                        {shortOpponent(game.opponent)}
                      </Text>
                    </View>
                  );
                })}
              </View>
            </View>
          </ScrollView>
        </View>
      )}

      <View style={styles.card}>
        <View style={styles.header}>
          <View style={styles.headerLeft}>
            <Ionicons name="swap-horizontal-outline" size={11} color={Colors.primary} />
            <Text style={styles.title}>
              H2H · {h2h.sampleSize ? `${h2h.sampleSize} APPS` : h2hRows.length ? `${h2hRows.length} TEAM MEETS` : 'NO VERIFIED HISTORY'}
            </Text>
          </View>
          {h2h.avgVsOpponent != null && <Text style={styles.meta}>AVG {Number(h2h.avgVsOpponent).toFixed(1)}</Text>}
        </View>
        {h2hRows.length > 0 ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
            <View style={{ width: h2hRows.length * 39 + 10 }}>
              <View style={styles.chart}>
                {h2hRows.map((row: any, index: number) => {
                  const value = typeof row.displayValue === 'number' ? row.displayValue : null;
                  const maxValue = Math.max(...h2hRows.map((item: any) => Number(item.displayValue) || 0), row.teamOnly ? 100 : prediction.line ?? 0, 1) * 1.18;
                  const isOver = value != null && !row.teamOnly && prediction.line != null && value > prediction.line;
                  const color = row.teamOnly ? '#4A6CFF' : isOver ? Colors.success : value != null ? Colors.error : '#444';
                  const height = value != null ? Math.max(10, (value / maxValue) * 112) : 10;
                  const possession = row.possession != null ? `POSS ${row.possession}%` : 'POSS N/A';
                  const date = row.date ? String(row.date).slice(5, 10) : '—';
                  return (
                    <View key={`${date}-${index}`} style={styles.barColumn}>
                      <Text style={[styles.value, { color: value != null && !row.teamOnly ? color : Colors.textTertiary }]}>
                        {value != null && !row.teamOnly ? value : row.teamOnly && value != null ? `${value}%` : '—'}
                      </Text>
                      <View style={[styles.bar, { height, backgroundColor: color + 'B8' }]}>
                        <Text style={styles.possession}>{possession}</Text>
                      </View>
                      <Text style={styles.date}>{date}</Text>
                      <Text style={[styles.opponent, { color: row.teamOnly ? '#4A6CFF' : Colors.textSecondary }]}>
                        {shortOpponent(row.opponent || row.homeTeam)}
                      </Text>
                    </View>
                  );
                })}
              </View>
              <View style={styles.legend}>
                <View style={styles.legendDot} />
                <Text style={styles.legendText}>{h2hRows.every((row: any) => row.teamOnly) ? 'team meeting · player did not appear' : 'player appearance'}</Text>
                <Text style={[styles.legendText, { marginLeft: 'auto' }]}>POSS = verified team share</Text>
              </View>
            </View>
          </ScrollView>
        ) : (
          <Text style={styles.empty}>No verified history for this opponent</Text>
        )}
      </View>
    </>
  );
}

const styles = {
  card: {
    marginTop: 8,
    backgroundColor: '#0A0A0A',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    overflow: 'hidden' as const,
  },
  header: {
    paddingHorizontal: 14,
    paddingTop: 12,
    paddingBottom: 8,
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
  },
  headerLeft: { flexDirection: 'row' as const, alignItems: 'center' as const, gap: 6 },
  title: { fontSize: 9, color: Colors.textSecondary, fontWeight: '800' as const, letterSpacing: 1 },
  meta: { marginLeft: 'auto' as const, fontSize: 9, color: Colors.textTertiary, fontFamily: 'JetBrainsMono_700Bold' },
  scrollContent: { paddingHorizontal: 14, paddingBottom: 12 },
  chart: { height: 137, flexDirection: 'row' as const, alignItems: 'flex-end' as const, gap: 5 },
  barColumn: { width: 34, height: 137, alignItems: 'center' as const, justifyContent: 'flex-end' as const },
  value: { fontSize: 8, fontWeight: '800' as const, marginBottom: 2 },
  bar: { width: 28, minHeight: 10, borderRadius: 3, justifyContent: 'flex-end' as const, alignItems: 'center' as const, position: 'relative' as const },
  possession: { position: 'absolute' as const, bottom: 4, color: '#FFF', fontSize: 6.5, fontWeight: '900' as const },
  date: { fontSize: 7, color: '#555', lineHeight: 10, marginTop: 4 },
  opponent: { fontSize: 7, fontWeight: '700' as const, lineHeight: 10 },
  legend: { marginTop: 5, flexDirection: 'row' as const, alignItems: 'center' as const, gap: 5 },
  legendDot: { width: 5, height: 5, borderRadius: 3, backgroundColor: '#4A6CFF' },
  legendText: { fontSize: 7, color: '#555' },
  empty: { paddingHorizontal: 14, paddingBottom: 12, fontSize: 9, color: Colors.textTertiary, fontWeight: '700' as const, letterSpacing: 0.5 },
};