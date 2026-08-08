import React, { useState } from 'react';
import { ScrollView, Text, TouchableOpacity, View } from 'react-native';
import * as Haptics from 'expo-haptics';
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
    .slice(0, 20);
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
    ? playerMatches.slice(0, 20).map((match: any) => ({
        ...match,
        possession: match.teamPossession,
        displayValue: match.targetStat,
        teamOnly: false,
      }))
    : teamMeetings.slice(0, 20).map((meeting: any) => ({
        ...meeting,
        displayValue: meeting.possession,
        teamOnly: true,
      }));

  const [selected, setSelected] = useState<{ group: 'recent' | 'h2h'; index: number } | null>(null);
  const selectBar = (group: 'recent' | 'h2h', index: number) => {
    setSelected((current) => current?.group === group && current.index === index ? null : { group, index });
    Haptics.selectionAsync().catch(() => undefined);
  };
  const selectedGame = selected?.group === 'recent' ? logs[selected.index] : null;
  const selectedH2H = selected?.group === 'h2h' ? h2hRows[selected.index] : null;
  const detailRow = selectedGame || selectedH2H;
  const detailPossession = detailRow?.teamPossession ?? detailRow?.possession;
  const detailOpponentPossession = detailRow?.opponentPossession;

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
                  const possession = game.teamPossession != null ? `P${game.teamPossession}%` : 'P—';
                  const isSelected = selected?.group === 'recent' && selected.index === index;
                  return (
                    <TouchableOpacity
                      key={`${date}-${index}`}
                      style={[styles.barColumn, isSelected && styles.barColumnSelected]}
                      onPress={() => selectBar('recent', index)}
                      activeOpacity={0.8}
                      accessibilityLabel={`${game.opponent || 'Recent match'}, ${game.value} ${prediction.line != null ? `against line ${prediction.line}` : ''}`}
                    >
                      <Text style={[styles.value, { color }]}>{game.value}</Text>
                      <View style={[styles.bar, { height, backgroundColor: color + 'B8' }]} />
                      <Text style={styles.date}>{date}</Text>
                      <Text style={[styles.opponent, { color: game.venue === 'home' ? Colors.success : '#60A5FA' }]}>
                        {shortOpponent(game.opponent)}
                      </Text>
                      <Text style={styles.possessionLabel}>{possession}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
              {selectedGame && (
                <Text style={styles.detail}>
                  {selectedGame.date ? String(selectedGame.date).slice(0, 10) : 'Match'} · {selectedGame.opponent || 'Opponent'} · {selectedGame.value} stat · {selectedGame.venue === 'home' ? 'HOME' : 'AWAY'}
                  {detailPossession != null ? ` · POSS ${detailPossession}%` : ' · POSS unavailable'}
                  {selectedGame.score ? ` · ${selectedGame.score}` : ''}
                </Text>
              )}
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
                  const isSelected = selected?.group === 'h2h' && selected.index === index;
                  return (
                    <TouchableOpacity
                      key={`${date}-${index}`}
                      style={[styles.barColumn, isSelected && styles.barColumnSelected]}
                      onPress={() => selectBar('h2h', index)}
                      activeOpacity={0.8}
                      accessibilityLabel={`${row.opponent || row.homeTeam || 'H2H meeting'}, ${row.teamOnly ? 'team meeting' : `${value ?? 'unavailable'} stat`}`}
                    >
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
                      <Text style={styles.possessionLabel}>{possession.replace('POSS ', 'P')}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
              {selectedH2H && (
                <Text style={styles.detail}>
                  {selectedH2H.date ? String(selectedH2H.date).slice(0, 10) : 'Meeting'} · {selectedH2H.opponent || selectedH2H.homeTeam || 'Opponent'} · {selectedH2H.teamOnly ? 'team meeting; player did not appear' : `${selectedH2H.displayValue ?? 'stat unavailable'} stat`}
                  {detailPossession != null ? ` · POSS ${detailPossession}%` : ' · POSS unavailable'}
                  {detailOpponentPossession != null ? ` / OPP ${detailOpponentPossession}%` : ''}
                  {selectedH2H.matchScore || selectedH2H.score ? ` · ${selectedH2H.matchScore || selectedH2H.score}` : ''}
                </Text>
              )}
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
  chart: { height: 151, flexDirection: 'row' as const, alignItems: 'flex-end' as const, gap: 5 },
  barColumn: { width: 34, height: 151, alignItems: 'center' as const, justifyContent: 'flex-end' as const, borderRadius: 5, paddingTop: 2 },
  barColumnSelected: { backgroundColor: 'rgba(255,255,255,0.07)' },
  value: { fontSize: 8, fontWeight: '800' as const, marginBottom: 2 },
  bar: { width: 28, minHeight: 10, borderRadius: 3, justifyContent: 'flex-end' as const, alignItems: 'center' as const, position: 'relative' as const },
  possession: { position: 'absolute' as const, bottom: 4, color: '#FFF', fontSize: 6.5, fontWeight: '900' as const },
  possessionLabel: { fontSize: 6.5, color: '#7D8796', lineHeight: 9, fontWeight: '800' as const },
  date: { fontSize: 7, color: '#555', lineHeight: 10, marginTop: 4 },
  opponent: { fontSize: 7, fontWeight: '700' as const, lineHeight: 10 },
  detail: { paddingHorizontal: 2, paddingTop: 4, paddingBottom: 2, color: '#9CA3AF', fontSize: 8, lineHeight: 12 },
  legend: { marginTop: 5, flexDirection: 'row' as const, alignItems: 'center' as const, gap: 5 },
  legendDot: { width: 5, height: 5, borderRadius: 3, backgroundColor: '#4A6CFF' },
  legendText: { fontSize: 7, color: '#555' },
  empty: { paddingHorizontal: 14, paddingBottom: 12, fontSize: 9, color: Colors.textTertiary, fontWeight: '700' as const, letterSpacing: 0.5 },
};