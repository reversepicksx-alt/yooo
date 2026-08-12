import React, { useEffect, useState } from 'react';
import { ScrollView, Text, TouchableOpacity, View } from 'react-native';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';

type CompactPrediction = {
  line?: number | null;
  recommendation?: string | null;
  gameLogs?: Array<Record<string, any>> | null;
  h2hPlayerStats?: Record<string, any> | null;
  matchupVolume?: Record<string, any> | null;
  [key: string]: any;
};

/**
 * Pick the first complete customer-facing explanation. Older saved picks can
 * contain a partial async placeholder such as "For the", so reject fragments
 * and build a deterministic read from the saved prediction when necessary.
 */
export function getTacticalRead(prediction: Record<string, any> | null | undefined): string | null {
  if (!prediction) return null;
  const candidates = [
    prediction.tacticalBreakdown,
    prediction.reasoning,
    prediction.sharpSummary,
    prediction.explanation,
    prediction.keyEvidence,
  ]
    .map((value) => String(value || '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim())
    .filter((value) => (
      value.length >= 40
      && !/^structured analysis loading\b/i.test(value)
      && !/^for the\b[,.]?\s*$/i.test(value)
      && !/^analysis (is )?pending\b/i.test(value)
    ));
  if (candidates.length) return candidates[0];

  const player = prediction.playerName || 'The player';
  const prop = String(prediction.propType || 'prop').replace(/_/g, ' ');
  const line = prediction.line != null ? Number(prediction.line).toFixed(1) : 'the posted';
  const projection = prediction.projection ?? prediction.projectedValue ?? prediction.bayesianProjection;
  const recommendation = String(prediction.recommendation || prediction.passLeaning || 'PASS').toUpperCase();
  const matchup = [prediction.teamName, prediction.opponentName || prediction.opponent]
    .filter(Boolean)
    .join(' vs ');
  const venue = prediction.venue === 'away' || prediction.playerIsHome === false ? 'away' : 'home';
  const expectedPossession = prediction.expectedPossession;
  const possessionStatus = String(
    prediction.possessionStatus
      ?? (prediction as any).matchupOverview?.possessionStatus
      ?? (prediction as any).possessionSource
      ?? 'unavailable',
  ).toLowerCase();
  const possessionText = expectedPossession && Number.isFinite(Number(expectedPossession[venue]))
    ? ` Expected possession is ${Math.round(Number(expectedPossession[venue]))}% on the ${venue} side.`
    : '';
  const projectionText = projection != null
    ? `projected for ${Number(projection).toFixed(1)} ${prop}`
    : `evaluated on ${prop}`;
  return `${player} is ${projectionText} against a ${line} line${matchup ? ` in ${matchup}` : ''}. The model leans ${recommendation} from the ${venue} side.${possessionText}`;
}

function shortOpponent(value: unknown) {
  return String(value || '?')
    .replace(/^(al-?|fc |cf |rc |sc |cd |ud |sd |rcd |as |ss |ac |us |sp |ca |cp |ue |ce |cm |se |sk )/i, '')
    .slice(0, 4)
    .toUpperCase();
}

function normalizeVenue(value: unknown): 'home' | 'away' | null {
  if (value === 'home' || value === 'away') return value;
  if (value === true) return 'home';
  if (value === false) return 'away';
  return null;
}

function rowVenue(row: Record<string, any>): 'home' | 'away' | null {
  return normalizeVenue(row.venue)
    ?? normalizeVenue(row.isHome)
    ?? normalizeVenue(row.playerIsHome);
}

function opponentName(row: Record<string, any>) {
  return row.opponent
    || (rowVenue(row) === 'home' ? row.awayTeam : row.homeTeam)
    || row.homeTeam
    || row.awayTeam
    || 'Opponent';
}

function venueMark(value: unknown) {
  const venue = normalizeVenue(value);
  return venue === 'home' ? 'H' : venue === 'away' ? 'A' : '–';
}

function displayH2HDate(value: unknown, full = false) {
  const raw = String(value || '');
  // The backend may encode H/A inside the visible MM-DD slice for older
  // native bundles: 2026-08H02 renders there as 08H02. Normalize it for
  // current bundles, which render the dedicated venue marker separately.
  const encoded = raw.match(/^(\d{4})-(\d{2})([HA])(\d{2})(.*)$/);
  if (encoded) {
    const [, year, month, , day] = encoded;
    return full ? `${year}-${month}-${day}` : `${month}-${day}`;
  }
  return full ? raw.slice(0, 10) : raw.slice(5, 10);
}

function stageLabelForRow(row: Record<string, any>) {
  const stageClass = String(row.stageClass || '').toLowerCase();
  if (stageClass.includes('knockout')) return 'KNOCKOUT STAGES';
  if (stageClass === 'group_stage') return 'LEAGUE GROUP';
  if (stageClass === 'regular_season') return 'REGULAR SEASON';
  const round = String(row.round || '').toLowerCase();
  if (/(final|semi|quarter|round of|playoff|knockout)/.test(round)) return 'KNOCKOUT STAGES';
  if (round.includes('group')) return 'LEAGUE GROUP';
  return row.round || 'COMPETITION';
}

function averageForVenue(rows: Array<Record<string, any>>, venue: 'home' | 'away') {
  const values = rows
    .filter((row) => rowVenue(row) === venue && row.value != null)
    .map((row) => Number(row.value))
    .filter((value) => Number.isFinite(value));
  return values.length
    ? { average: values.reduce((sum, value) => sum + value, 0) / values.length, count: values.length }
    : null;
}

function compactTeamName(value: unknown, fallback: string) {
  const name = String(value || '').trim();
  if (!name) return fallback;
  return name.length > 16 ? `${name.slice(0, 15)}…` : name;
}

function VolumeMetric({
  label,
  value,
  sample,
  color,
}: {
  label: string;
  value: unknown;
  sample: unknown;
  color: string;
}) {
  const numericValue = Number(value);
  const hasValue = Number.isFinite(numericValue);
  return (
    <View style={styles.volumeMetric}>
      <Text style={styles.volumeMetricLabel} numberOfLines={1}>{label}</Text>
      <Text style={[styles.volumeMetricValue, { color }]}>
        {hasValue ? numericValue.toFixed(1) : '—'}
      </Text>
      <Text style={styles.volumeMetricSample}>
        {hasValue && Number(sample) > 0 ? `N=${Number(sample)}` : 'UNAVAILABLE'}
      </Text>
    </View>
  );
}

export function CompactAnalysisBars({ prediction }: { prediction: CompactPrediction }) {
  const logs = (prediction.gameLogs ?? [])
    .filter((game) => !game.synthetic && game.value != null)
    .slice(0, 20);
  const historyContext = prediction.historyContext ?? null;
  const preferredVenue = normalizeVenue(
    prediction.venue
      ?? (typeof prediction.isHome === 'boolean' ? prediction.isHome : null)
      ?? (typeof prediction.playerIsHome === 'boolean' ? prediction.playerIsHome : null),
  );
  const h2h = prediction.h2hPlayerStats ?? {};
  const historyVenue = preferredVenue ?? normalizeVenue(prediction.historyContext?.venue);
  const showSelectedVenueOnly = historyVenue === 'home' || historyVenue === 'away';
  const matchupVolume = prediction.matchupVolume ?? null;
  const isSotProp = prediction.propType === 'shots_on_target';
  const isGkProp = prediction.propType === 'saves' || prediction.propType === 'goalie_saves';
  const isPassProp = prediction.propType === 'pass_attempts' || prediction.propType === 'passes';
  const showSotEvidence = isSotProp || isGkProp;
  const showPossessionContext = !isGkProp;
  const evidenceVenue = preferredVenue ?? (matchupVolume?.venue === 'away' ? 'away' : 'home');
  const opponentEvidenceVenue = evidenceVenue === 'home' ? 'away' : 'home';
  const selectedFixtureSplit = matchupVolume?.fixtureSplits?.[evidenceVenue] ?? {};
  const selectedOpponentFixtureSplit = matchupVolume?.fixtureSplits?.[opponentEvidenceVenue] ?? {};
  const selectedPlayerSaveRate = matchupVolume?.goalkeeperSaveRate?.selectedVenue;
  const selectedPlayerPassShare = matchupVolume?.playerPassInvolvement?.selectedVenue;
  const hasMarketEvidence = Boolean(matchupVolume?.available && (isSotProp || isGkProp || isPassProp));
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

  const homeSplit = prediction.homeAvg != null
    ? { average: Number(prediction.homeAvg), count: logs.filter((row) => rowVenue(row) === 'home').length }
    : averageForVenue(logs, 'home');
  const awaySplit = prediction.awayAvg != null
    ? { average: Number(prediction.awayAvg), count: logs.filter((row) => rowVenue(row) === 'away').length }
    : averageForVenue(logs, 'away');
  const last10Logs = logs.slice(0, 10);
  const tpHomeValues = last10Logs
    .filter((row) => rowVenue(row) === 'home' && row.teamPossession != null)
    .map((row) => Number(row.teamPossession))
    .filter(Number.isFinite);
  const tpAwayValues = last10Logs
    .filter((row) => rowVenue(row) === 'away' && row.teamPossession != null)
    .map((row) => Number(row.teamPossession))
    .filter(Number.isFinite);
  const tpHomeSplit = prediction.tpHomeAvg != null
    ? { average: Number(prediction.tpHomeAvg), count: Number(prediction.tpHomeCount ?? tpHomeValues.length) }
    : tpHomeValues.length
      ? { average: tpHomeValues.reduce((sum, value) => sum + value, 0) / tpHomeValues.length, count: tpHomeValues.length }
      : null;
  const tpAwaySplit = prediction.tpAwayAvg != null
    ? { average: Number(prediction.tpAwayAvg), count: Number(prediction.tpAwayCount ?? tpAwayValues.length) }
    : tpAwayValues.length
      ? { average: tpAwayValues.reduce((sum, value) => sum + value, 0) / tpAwayValues.length, count: tpAwayValues.length }
      : null;

  // Keep the prediction's venue visibly selected from the first render. This
  // applies to both recent logs and H2H, so an away pick never opens on home
  // history by accident.
  const initialSelection = preferredVenue
    ? {
        group: 'h2h' as const,
        index: h2hRows.findIndex((row: any) => rowVenue(row) === preferredVenue),
      }
    : null;
  const safeInitialSelection = initialSelection && initialSelection.index >= 0
    ? initialSelection
    : null;
  const [selected, setSelected] = useState<{ group: 'recent' | 'h2h'; index: number } | null>(safeInitialSelection);
  useEffect(() => {
    setSelected(safeInitialSelection);
  }, [prediction.fixtureId, prediction.playerName, prediction.line, preferredVenue, h2hRows.length, safeInitialSelection?.index]);

  const selectBar = (group: 'recent' | 'h2h', index: number) => {
    setSelected((current) => current?.group === group && current.index === index ? null : { group, index });
    // selectionAsync is a barely audible UI-selection tick. The app's
    // established bar/button interaction uses a Light impact instead.
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
  };
  const selectedGame = selected?.group === 'recent' ? logs[selected.index] : null;
  const detailRow = selectedGame;
  const detailPossession = detailRow?.teamPossession ?? detailRow?.possession;
  const detailOpponentPossession = detailRow?.opponentPossession;
  const expectedPossession = prediction.expectedPossession;
  const possessionStatus = String(
    prediction.possessionStatus
      ?? (prediction as any).matchupOverview?.possessionStatus
      ?? (prediction as any).possessionSource
      ?? 'unavailable',
  ).toLowerCase();
  const expectedHome = Number(expectedPossession?.home);
  const expectedAway = Number(expectedPossession?.away);
  const hasExpectedPossession = Number.isFinite(expectedHome)
    && Number.isFinite(expectedAway)
    && expectedHome >= 0
    && expectedAway >= 0
    && possessionStatus !== 'unavailable';
  const expectedVenue = preferredVenue ?? 'home';
  // The possession bar is fixture-oriented, not player-oriented: home is
  // always the left segment and away is always the right segment. The player
  // may be the away team, but that must never make the away team appear on the
  // left or make the labels look like a reversed matchup.
  const expectedHomeTeam = compactTeamName(
    prediction.homeTeam
      || (expectedVenue === 'home' ? prediction.teamName : prediction.opponentName || prediction.opponent),
    'HOME TEAM',
  );
  const expectedAwayTeam = compactTeamName(
    prediction.awayTeam
      || (expectedVenue === 'away' ? prediction.teamName : prediction.opponentName || prediction.opponent),
    'AWAY TEAM',
  );

  return (
    <>
      {showPossessionContext && hasExpectedPossession && (
        <View style={styles.possessionCard}>
          <View style={styles.header}>
            <View style={styles.headerLeft}>
              <Ionicons name="football-outline" size={11} color={Colors.primary} />
              <Text style={styles.title}>EXPECTED POSSESSION</Text>
            </View>
            <Text style={styles.meta}>
              {possessionStatus === 'verified' ? `${expectedVenue.toUpperCase()} SIDE` : 'ESTIMATE'}
            </Text>
          </View>
          <View style={styles.expectedPossessionBar}>
            <View style={[styles.expectedPossessionPlayer, { flex: Math.max(expectedHome, 0.1) }]} />
            <View style={[styles.expectedPossessionOpponent, { flex: Math.max(expectedAway, 0.1) }]} />
          </View>
          <View style={styles.expectedPossessionLabels}>
            <Text style={styles.expectedPossessionPlayerText} numberOfLines={1}>
              {expectedHomeTeam} {Math.round(expectedHome)}%
            </Text>
            <Text style={styles.expectedPossessionOpponentText} numberOfLines={1}>
              {Math.round(expectedAway)}% {expectedAwayTeam}
            </Text>
          </View>
        </View>
      )}

      {logs.length > 0 && (
        <View style={styles.card}>
          <View style={styles.header}>
            <View style={styles.headerLeft}>
              <Ionicons name="pulse" size={11} color={Colors.primary} />
              <View style={styles.headerStack}>
                <Text style={styles.title}>RECENT MATCHES · {logs.length}</Text>
                {historyContext?.label && (
                  <Text style={styles.contextLabel} numberOfLines={1}>
                    {historyContext.label}
                  </Text>
                )}
              </View>
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
                  const date = game.date ? displayH2HDate(game.date) : '—';
                   const possession = game.teamPossession != null ? `TP ${Number(game.teamPossession).toFixed(0)}%` : 'TP —';
                  const minutes = game.minutesPlayed ?? game.minutes;
                  const isSelected = selected?.group === 'recent' && selected.index === index;
                  return (
                    <TouchableOpacity
                      key={`${date}-${index}`}
                       style={[
                         styles.barColumn,
                         preferredVenue && rowVenue(game) === preferredVenue && styles.barColumnVenueSelected,
                         isSelected && styles.barColumnSelected,
                       ]}
                      onPress={() => selectBar('recent', index)}
                      activeOpacity={0.8}
                      accessibilityLabel={`${game.opponent || 'Recent match'}, ${game.value} ${prediction.line != null ? `against line ${prediction.line}` : ''}`}
                    >
                      <Text style={[styles.value, { color }]}>{game.value}</Text>
                      <View style={[styles.bar, { height, backgroundColor: color + 'B8' }]} />
                      <Text style={styles.date}>{date}</Text>
                      <Text style={[styles.opponent, { color: rowVenue(game) === 'home' ? Colors.success : '#60A5FA' }]}>
                        {shortOpponent(opponentName(game))}
                      </Text>
                      {showPossessionContext && <Text style={styles.possessionLabel}>{possession}</Text>}
                      <Text style={styles.possessionLabel}>
                        MIN {minutes != null ? Number(minutes).toFixed(0) : '—'}
                      </Text>
                    {showSotEvidence && (
                      <Text style={styles.possessionLabel}>
                        OPP SOT {game.opponentShotsOnTarget != null ? Number(game.opponentShotsOnTarget).toFixed(0) : '—'}
                      </Text>
                    )}
                    {isPassProp && (
                      <Text style={styles.possessionLabel}>
                        OPP PASS {game.opponentPassAttempts != null ? Number(game.opponentPassAttempts).toFixed(0) : '—'}
                      </Text>
                    )}
                      <Text style={[styles.venueLabel, { color: rowVenue(game) === 'home' ? Colors.success : '#60A5FA' }]}>
                        {venueMark(rowVenue(game))}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
              {selectedGame && (
                <Text style={styles.detail}>
                   {selectedGame.date ? displayH2HDate(selectedGame.date, true) : 'Match'} · {selectedGame.opponent || 'Opponent'} · {selectedGame.value} stat · {selectedGame.venue === 'home' ? 'HOME' : 'AWAY'}
                   {showPossessionContext
                     ? detailPossession != null ? ` · POSS ${detailPossession}%` : ' · POSS unavailable'
                     : ''}
                   {selectedGame.score ? ` · ${selectedGame.score}` : ''}
                   {selectedGame.competitionName
                     ? ` · ${selectedGame.competitionName} · ${stageLabelForRow(selectedGame)}`
                     : ''}
                  {showSotEvidence
                    ? ` · OPP SOT ${selectedGame.opponentShotsOnTarget != null ? Number(selectedGame.opponentShotsOnTarget).toFixed(0) : 'unavailable'}`
                    : isPassProp
                      ? ` · OPP PASS ${selectedGame.opponentPassAttempts != null ? Number(selectedGame.opponentPassAttempts).toFixed(0) : 'unavailable'}`
                      : ''}
                </Text>
              )}
            </View>
          </ScrollView>
          <View style={styles.splitRow}>
            {showSelectedVenueOnly ? (
              <View style={styles.splitItem}>
                <Text style={styles.splitLabel}>{historyVenue === 'home' ? 'HOME SPLIT' : 'AWAY SPLIT'}</Text>
                <Text style={[styles.splitValue, { color: historyVenue === 'home' ? Colors.success : '#60A5FA' }]}>
                  {(historyVenue === 'home' ? homeSplit : awaySplit)
                    ? (historyVenue === 'home' ? homeSplit : awaySplit)!.average.toFixed(1)
                    : '—'}
                </Text>
                <Text style={styles.splitMeta}>
                  {(historyVenue === 'home' ? homeSplit : awaySplit)
                    ? `${(historyVenue === 'home' ? homeSplit : awaySplit)!.count} MATCHES`
                    : 'NO SAMPLE'}
                </Text>
              </View>
            ) : (
              <>
                <View style={styles.splitItem}>
                  <Text style={styles.splitLabel}>HOME SPLIT</Text>
                  <Text style={[styles.splitValue, { color: Colors.success }]}>
                    {homeSplit ? homeSplit.average.toFixed(1) : '—'}
                  </Text>
                  <Text style={styles.splitMeta}>{homeSplit ? `${homeSplit.count} MATCHES` : 'NO SAMPLE'}</Text>
                </View>
                <View style={styles.splitDivider} />
                <View style={styles.splitItem}>
                  <Text style={styles.splitLabel}>AWAY SPLIT</Text>
                  <Text style={[styles.splitValue, { color: '#60A5FA' }]}>
                    {awaySplit ? awaySplit.average.toFixed(1) : '—'}
                  </Text>
                  <Text style={styles.splitMeta}>{awaySplit ? `${awaySplit.count} MATCHES` : 'NO SAMPLE'}</Text>
                </View>
              </>
            )}
          </View>
          {showPossessionContext && (tpHomeSplit || tpAwaySplit) && (
            <View style={styles.splitRow}>
              {showSelectedVenueOnly ? (
                <View style={styles.splitItem}>
                  <Text style={styles.splitLabel}>
                    {historyVenue === 'home' ? 'TP HOME · LAST 10' : 'TP AWAY · LAST 10'}
                  </Text>
                  <Text style={[styles.splitValue, { color: historyVenue === 'home' ? Colors.success : '#60A5FA' }]}>
                    {(historyVenue === 'home' ? tpHomeSplit : tpAwaySplit)
                      ? `${(historyVenue === 'home' ? tpHomeSplit : tpAwaySplit)!.average.toFixed(1)}%`
                      : '—'}
                  </Text>
                  <Text style={styles.splitMeta}>
                    {(historyVenue === 'home' ? tpHomeSplit : tpAwaySplit)
                      ? `${(historyVenue === 'home' ? tpHomeSplit : tpAwaySplit)!.count} MATCHES`
                      : 'NO SAMPLE'}
                  </Text>
                </View>
              ) : (
                <>
                  <View style={styles.splitItem}>
                    <Text style={styles.splitLabel}>TP HOME · LAST 10</Text>
                    <Text style={[styles.splitValue, { color: Colors.success }]}>
                      {tpHomeSplit ? `${tpHomeSplit.average.toFixed(1)}%` : '—'}
                    </Text>
                    <Text style={styles.splitMeta}>{tpHomeSplit ? `${tpHomeSplit.count} MATCHES` : 'NO SAMPLE'}</Text>
                  </View>
                  <View style={styles.splitDivider} />
                  <View style={styles.splitItem}>
                    <Text style={styles.splitLabel}>TP AWAY · LAST 10</Text>
                    <Text style={[styles.splitValue, { color: '#60A5FA' }]}>
                      {tpAwaySplit ? `${tpAwaySplit.average.toFixed(1)}%` : '—'}
                    </Text>
                    <Text style={styles.splitMeta}>{tpAwaySplit ? `${tpAwaySplit.count} MATCHES` : 'NO SAMPLE'}</Text>
                  </View>
                </>
              )}
            </View>
          )}
          {hasMarketEvidence && (
            <View style={styles.marketEvidence}>
              <Text style={styles.volumeSectionTitle}>
                {isGkProp ? 'GOALKEEPER CONTEXT' : 'PASS VOLUME CONTEXT'}
              </Text>
              <View style={styles.volumeGrid}>
                {isGkProp ? (
                  <>
                    <VolumeMetric
                      label="OPPONENT SOT"
                      value={selectedOpponentFixtureSplit.sotCreated?.average}
                      sample={selectedOpponentFixtureSplit.sotCreated?.sampleSize}
                      color="#60A5FA"
                    />
                    <VolumeMetric
                      label="EXPECTED OPPONENT SOT"
                      value={matchupVolume?.shotsOnTarget?.expectedOpponent?.average}
                      sample={matchupVolume?.shotsOnTarget?.expectedOpponent?.sampleSize}
                      color={Colors.primary}
                    />
                    <VolumeMetric
                      label="PLAYER SAVE RATE"
                      value={selectedPlayerSaveRate?.average}
                      sample={selectedPlayerSaveRate?.sampleSize}
                      color={Colors.success}
                    />
                  </>
                ) : isPassProp ? (
                  <>
                    <VolumeMetric
                      label="TEAM PASSES"
                      value={selectedFixtureSplit.passesCreated?.average}
                      sample={selectedFixtureSplit.passesCreated?.sampleSize}
                      color={Colors.success}
                    />
                    <VolumeMetric
                      label="OPPONENT ALLOWS"
                      value={selectedOpponentFixtureSplit.passesAllowed?.average}
                      sample={selectedOpponentFixtureSplit.passesAllowed?.sampleSize}
                      color="#F59E0B"
                    />
                    <VolumeMetric
                      label="PLAYER HOME SHARE"
                      value={matchupVolume?.playerPassInvolvement?.byVenue?.home?.average}
                      sample={matchupVolume?.playerPassInvolvement?.byVenue?.home?.sampleSize}
                      color={Colors.success}
                    />
                    <VolumeMetric
                      label="PLAYER AWAY SHARE"
                      value={matchupVolume?.playerPassInvolvement?.byVenue?.away?.average}
                      sample={matchupVolume?.playerPassInvolvement?.byVenue?.away?.sampleSize}
                      color="#60A5FA"
                    />
                    <VolumeMetric
                      label="EXPECTED TEAM PASSES"
                      value={matchupVolume?.passes?.expectedTeam?.average}
                      sample={matchupVolume?.passes?.expectedTeam?.sampleSize}
                      color={Colors.primary}
                    />
                    <VolumeMetric
                      label="EXPECTED PLAYER PASSES"
                      value={selectedPlayerPassShare?.expectedPlayerPasses}
                      sample={selectedPlayerPassShare?.sampleSize}
                      color={Colors.primary}
                    />
                  </>
                ) : (
                  <>
                    <VolumeMetric
                      label="TEAM SOT"
                      value={selectedFixtureSplit.sotCreated?.average}
                      sample={selectedFixtureSplit.sotCreated?.sampleSize}
                      color={Colors.success}
                    />
                    <VolumeMetric
                      label="OPPONENT ALLOWS"
                      value={selectedOpponentFixtureSplit.sotAllowed?.average}
                      sample={selectedOpponentFixtureSplit.sotAllowed?.sampleSize}
                      color="#F59E0B"
                    />
                    <VolumeMetric
                      label="EXPECTED TEAM SOT"
                      value={matchupVolume?.shotsOnTarget?.expectedTeam?.average}
                      sample={matchupVolume?.shotsOnTarget?.expectedTeam?.sampleSize}
                      color={Colors.primary}
                    />
                  </>
                )}
              </View>
            </View>
          )}
        </View>
      )}

      <View style={styles.card}>
        <View style={styles.h2hHeader}>
          <View style={styles.headerLeft}>
            <Ionicons name="swap-horizontal-outline" size={11} color={Colors.primary} />
            <Text style={styles.title}>
              H2H · {h2h.sampleSize ? `${h2h.sampleSize} APPS` : h2hRows.length ? `${h2hRows.length} TEAM MEETS` : 'NO VERIFIED HISTORY'}
            </Text>
          </View>
          {h2h.avgVsOpponent != null && <Text style={styles.meta}>AVG {Number(h2h.avgVsOpponent).toFixed(1)}</Text>}
        </View>
        {h2hRows.length > 0 ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.h2hScrollContent}>
            <View style={{ width: h2hRows.length * 46 + 10 }}>
              <View style={styles.h2hChart}>
                {h2hRows.map((row: any, index: number) => {
                  const value = typeof row.displayValue === 'number' ? row.displayValue : null;
                  const maxValue = Math.max(...h2hRows.map((item: any) => Number(item.displayValue) || 0), row.teamOnly ? 100 : prediction.line ?? 0, 1) * 1.18;
                  const isOver = value != null && !row.teamOnly && prediction.line != null && value > prediction.line;
                  const color = row.teamOnly ? '#4A6CFF' : isOver ? Colors.success : value != null ? Colors.error : '#444';
                  const height = value != null ? Math.max(4, (value / maxValue) * 22) : 4;
                   const possession = showPossessionContext
                     ? row.possession != null ? `TP ${Number(row.possession).toFixed(0)}%` : 'TP —'
                     : null;
                  const date = row.date ? displayH2HDate(row.date) : '—';
                  const isSelected = selected?.group === 'h2h' && selected.index === index;
                  return (
                    <TouchableOpacity
                      key={`${date}-${index}`}
                       style={[
                          styles.h2hBarColumn,
                         preferredVenue && rowVenue(row) === preferredVenue && styles.barColumnVenueSelected,
                         isSelected && styles.barColumnSelected,
                       ]}
                      onPress={() => selectBar('h2h', index)}
                      activeOpacity={0.8}
                      accessibilityLabel={`${row.opponent || row.homeTeam || 'H2H meeting'}, ${row.teamOnly ? 'team meeting' : `${value ?? 'unavailable'} stat`}`}
                    >
                      <Text style={[styles.h2hValue, { color: value != null && !row.teamOnly ? color : Colors.textTertiary }]}>
                        {value != null && !row.teamOnly ? value : row.teamOnly && value != null ? `${value}%` : '—'}
                      </Text>
                      <View style={[styles.h2hBar, { height, backgroundColor: color + 'B8' }]} />
                      <Text style={styles.h2hDate}>{date}</Text>
                      <Text style={[styles.h2hMeta, { color: rowVenue(row) === 'home' ? Colors.success : '#60A5FA' }]}>
                        {possession ? `${possession} · ` : ''}{venueMark(rowVenue(row))}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
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
  possessionCard: {
    marginTop: 8,
    backgroundColor: '#0A0A0A',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.16)',
    paddingBottom: 11,
    overflow: 'hidden' as const,
  },
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
  h2hHeader: {
    paddingHorizontal: 14,
    paddingTop: 7,
    paddingBottom: 4,
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
  },
  headerLeft: { flexDirection: 'row' as const, alignItems: 'center' as const, gap: 6 },
  headerStack: { flex: 1, minWidth: 0 },
  title: { fontSize: 9, color: Colors.textSecondary, fontWeight: '800' as const, letterSpacing: 1 },
  contextLabel: { marginTop: 3, fontSize: 7, color: Colors.primary, fontWeight: '900' as const, letterSpacing: 0.45 },
  meta: { marginLeft: 'auto' as const, fontSize: 9, color: Colors.textTertiary, fontFamily: 'JetBrainsMono_700Bold' },
  expectedPossessionBar: {
    height: 7,
    marginHorizontal: 14,
    flexDirection: 'row' as const,
    borderRadius: 4,
    overflow: 'hidden' as const,
    backgroundColor: 'rgba(96,165,250,0.45)',
  },
  expectedPossessionPlayer: { backgroundColor: Colors.success },
  expectedPossessionOpponent: { backgroundColor: '#60A5FA' },
  expectedPossessionLabels: {
    marginHorizontal: 14,
    marginTop: 5,
    flexDirection: 'row' as const,
    justifyContent: 'space-between' as const,
    alignItems: 'center' as const,
    gap: 12,
  },
  expectedPossessionPlayerText: {
    flex: 1,
    color: Colors.success,
    fontSize: 8,
    fontWeight: '900' as const,
  },
  expectedPossessionOpponentText: {
    flex: 1,
    color: '#60A5FA',
    fontSize: 8,
    fontWeight: '900' as const,
    textAlign: 'right' as const,
  },
  scrollContent: { paddingHorizontal: 14, paddingBottom: 12 },
  h2hScrollContent: { paddingHorizontal: 14, paddingBottom: 8 },
  chart: { height: 151, flexDirection: 'row' as const, alignItems: 'flex-end' as const, gap: 5 },
  barColumn: { width: 34, height: 151, alignItems: 'center' as const, justifyContent: 'flex-end' as const, borderRadius: 5, paddingTop: 2 },
  // H2H is intentionally a little taller than the old strip. Each column has
  // reserved rows for value → bar → date → possession/venue, so the bar can
  // never cover the customer-facing numbers.
  h2hChart: { height: 78, flexDirection: 'row' as const, alignItems: 'flex-start' as const, gap: 5 },
  h2hBarColumn: { width: 43, height: 74, alignItems: 'center' as const, justifyContent: 'flex-start' as const, borderRadius: 5, paddingTop: 1 },
  h2hValue: { fontSize: 11, fontWeight: '900' as const, lineHeight: 14, height: 14, marginBottom: 2 },
  h2hBar: { width: 26, minHeight: 4, borderRadius: 2 },
  h2hDate: { fontSize: 9, color: '#888', lineHeight: 11, height: 11, marginTop: 3 },
  h2hMeta: { fontSize: 9, lineHeight: 11, height: 11, fontWeight: '900' as const, marginTop: 1 },
  barColumnSelected: { backgroundColor: 'rgba(255,255,255,0.07)' },
  barColumnVenueSelected: { backgroundColor: 'rgba(57,255,20,0.055)' },
  value: { fontSize: 8, fontWeight: '800' as const, marginBottom: 2 },
  bar: { width: 28, minHeight: 10, borderRadius: 3, justifyContent: 'flex-end' as const, alignItems: 'center' as const, position: 'relative' as const },
  possession: { position: 'absolute' as const, bottom: 4, color: '#FFF', fontSize: 6.5, fontWeight: '900' as const },
  possessionLabel: { fontSize: 6.5, color: '#7D8796', lineHeight: 9, fontWeight: '800' as const },
  venueLabel: { fontSize: 7, lineHeight: 9, fontWeight: '900' as const, letterSpacing: 0.5 },
  date: { fontSize: 7, color: '#555', lineHeight: 10, marginTop: 4 },
  opponent: { fontSize: 7, fontWeight: '700' as const, lineHeight: 10 },
  detail: { paddingHorizontal: 2, paddingTop: 4, paddingBottom: 2, color: '#9CA3AF', fontSize: 8, lineHeight: 12 },
  splitRow: {
    marginHorizontal: 14,
    marginBottom: 12,
    paddingTop: 9,
    borderTopWidth: 1,
    borderTopColor: 'rgba(255,255,255,0.07)',
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
  },
  splitItem: { flex: 1, alignItems: 'center' as const },
  splitDivider: { width: 1, height: 29, backgroundColor: 'rgba(255,255,255,0.08)' },
  splitLabel: { fontSize: 7, color: '#7D8796', fontWeight: '800' as const, letterSpacing: 0.7 },
  splitValue: { fontSize: 14, fontWeight: '900' as const, marginTop: 2 },
  splitMeta: { fontSize: 6.5, color: '#555', fontWeight: '800' as const, marginTop: 1 },
  marketEvidence: {
    marginHorizontal: 14,
    marginBottom: 12,
    paddingTop: 9,
    borderTopWidth: 1,
    borderTopColor: 'rgba(255,255,255,0.07)',
  },
  volumeSectionTitle: {
    marginTop: 0,
    marginBottom: 7,
    color: '#7D8796',
    fontSize: 7,
    fontWeight: '900' as const,
    letterSpacing: 0.8,
  },
  volumeGrid: {
    flexDirection: 'row' as const,
    flexWrap: 'wrap' as const,
    gap: 7,
  },
  volumeMetric: {
    width: '48%' as const,
    minHeight: 48,
    padding: 8,
    borderRadius: 7,
    backgroundColor: 'rgba(255,255,255,0.035)',
  },
  volumeMetricLabel: {
    color: '#7D8796',
    fontSize: 6.5,
    fontWeight: '900' as const,
    letterSpacing: 0.35,
  },
  volumeMetricValue: { fontSize: 15, fontWeight: '900' as const, marginTop: 2 },
  volumeMetricSample: { color: '#555', fontSize: 6.5, fontWeight: '800' as const, marginTop: 1 },
  legend: { marginTop: 5, flexDirection: 'row' as const, alignItems: 'center' as const, gap: 5 },
  legendDot: { width: 5, height: 5, borderRadius: 3, backgroundColor: '#4A6CFF' },
  legendText: { fontSize: 7, color: '#555' },
  empty: { paddingHorizontal: 14, paddingBottom: 12, fontSize: 9, color: Colors.textTertiary, fontWeight: '700' as const, letterSpacing: 0.5 },
};