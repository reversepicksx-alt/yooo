import React, { useEffect, useState } from 'react';
import { ScrollView, Text, TouchableOpacity, View } from 'react-native';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { reversePicksPressureLabel, reversePicksPressureScore } from '@/lib/pressure';

type CompactPrediction = {
  line?: number | null;
  recommendation?: string | null;
  gameLogs?: Array<Record<string, any>> | null;
  playerGameLogs?: {
    games?: Array<Record<string, any>> | null;
    allGames?: Array<Record<string, any>> | null;
    targetProp?: string | null;
    hitRates?: {
      overPct?: number | null;
      underPct?: number | null;
      overHits?: number | null;
      underHits?: number | null;
      pushHits?: number | null;
      total?: number | null;
    } | null;
    venueHistory?: {
      selectedVenue?: 'home' | 'away' | null;
      target?: number | null;
      verifiedSampleSize?: number | null;
      status?: string | null;
      fallback?: string | null;
      modelScope?: string | null;
      modelSampleSize?: number | null;
    } | null;
    modelHitRates?: {
      overPct?: number | null;
      underPct?: number | null;
      overHits?: number | null;
      underHits?: number | null;
      total?: number | null;
    } | null;
    leagueRoleBucket?: string | null;
  } | null;
  venueHistory?: {
    selectedVenue?: 'home' | 'away' | null;
    target?: number | null;
    verifiedSampleSize?: number | null;
    status?: string | null;
    fallback?: string | null;
    modelScope?: string | null;
    modelSampleSize?: number | null;
    metadataCoverage?: {
      total?: number | null;
      dated?: number | null;
      withVenue?: number | null;
      withOpponent?: number | null;
    } | null;
  } | null;
  modelHitRates?: {
    overPct?: number | null;
    underPct?: number | null;
    overHits?: number | null;
    underHits?: number | null;
    total?: number | null;
  } | null;
  archiveHitRates?: {
    overPct?: number | null;
    underPct?: number | null;
    overHits?: number | null;
    underHits?: number | null;
    total?: number | null;
  } | null;
  leagueRoleBucket?: string | null;
  positionEvidence?: Record<string, any> | null;
  h2hPlayerStats?: Record<string, any> | null;
  matchupVolume?: Record<string, any> | null;
  [key: string]: any;
};

export type CompactAnalysisSection = 'overview' | 'read' | 'form' | 'matchup' | 'model';
export type RecentVenueFilter = 'all' | 'home' | 'away' | 'h2h';

const RECENT_LOG_VALUE_FIELDS: Record<string, string> = {
  pass_attempts: 'passes_total',
  passes: 'passes_total',
  shots: 'shots_total',
  shots_on_target: 'shots_on',
  goals: 'goals_total',
  assists: 'goals_assists',
  key_passes: 'passes_key',
  shots_assisted: 'passes_key',
  tackles: 'tackles_total',
  saves: 'goals_saves',
  goalie_saves: 'goals_saves',
  dribbles: 'dribbles_attempts',
  crosses: 'passes_crosses',
  interceptions: 'tackles_interceptions',
  blocks: 'tackles_blocks',
  fouls_drawn: 'fouls_drawn',
  fouls_committed: 'fouls_committed',
  clearances: 'tackles_clearances',
  yellow_cards: 'cards_yellow',
  duels_won: 'duels_won',
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
  if (value === true) return 'home';
  if (value === false) return 'away';
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'home' || normalized === 'h') return 'home';
  if (normalized === 'away' || normalized === 'a') return 'away';
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

function displayH2HDate(value: unknown) {
  const raw = String(value || '');
  // The backend may encode H/A inside the visible MM-DD slice for older
  // native bundles: 2026-08H02 renders there as 08H02. Normalize it for
  // current bundles, which render the dedicated venue marker separately.
  const encoded = raw.match(/^(\d{4})-(\d{2})([HA])(\d{2})(.*)$/);
  if (encoded) {
    const [, year, month, , day] = encoded;
    return `${year}-${month}-${day}`;
  }
  return raw.slice(0, 10);
}

function newestFirst(a: Record<string, any>, b: Record<string, any>) {
  const aTime = Date.parse(String(a.date || ''));
  const bTime = Date.parse(String(b.date || ''));
  if (Number.isFinite(aTime) && Number.isFinite(bTime) && aTime !== bTime) {
    return bTime - aTime;
  }
  return String(b.date || '').localeCompare(String(a.date || ''));
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

export const CompactAnalysisBars = React.memo(function CompactAnalysisBars({
  prediction,
  section = 'overview',
  lineEditor,
}: {
  prediction: CompactPrediction;
  section?: CompactAnalysisSection;
  lineEditor?: React.ReactNode;
}) {
  const historyContext = prediction.historyContext ?? null;
  const metadataCoverage = historyContext?.metadataCoverage ?? null;
  const preferredVenue = normalizeVenue(
    prediction.venue
      ?? (typeof prediction.isHome === 'boolean' ? prediction.isHome : null)
      ?? (typeof prediction.playerIsHome === 'boolean' ? prediction.playerIsHome : null),
  );
  const historyVenue = preferredVenue ?? normalizeVenue(historyContext?.venue);
  const venueHistory = prediction.venueHistory ?? prediction.playerGameLogs?.venueHistory ?? null;
  const venueHistoryFallback = venueHistory?.status === 'full_history_fallback';
  const venueHistorySample = Number(venueHistory?.verifiedSampleSize);
  const venueHistoryTarget = Number(venueHistory?.target || 30);
  // The backend supplies a complete, newest-first archive for both venues.
  // Keep the boundary defensive because saved/older responses can still
  // contain only the venue-scoped `games` payload.
  // Live predictions are normalized by api.ts into gameLogs. Saved-pick
  // analysis responses retain the backend's playerGameLogs.games shape.
  // Accept both so the analysis modal cannot show H2H while silently hiding
  // the recent player archive.
  const rawLogs: Array<Record<string, any>> = (
    Array.isArray(prediction.gameLogs) && prediction.gameLogs.length > 0
      ? prediction.gameLogs
      : prediction.playerGameLogs?.allGames
        ?? prediction.playerGameLogs?.games
        ?? []
  );
  const targetField = RECENT_LOG_VALUE_FIELDS[
    String(prediction.propType || prediction.playerGameLogs?.targetProp || '')
  ];
  const h2h = prediction.h2hPlayerStats ?? {};
  const h2hTeamMeetings = Number(h2h.teamMeetings || 0);
  const h2hCoverage = h2h.historyCoverage ?? {};
  const h2hRange = String(
    h2h.seasonsCovered?.range
      ?? (
        h2hCoverage.oldestYear != null && h2hCoverage.newestYear != null
          ? `${h2hCoverage.oldestYear}–${h2hCoverage.newestYear}`
          : ''
      ),
  );
  const h2hCoverageLabel = h2hTeamMeetings > 0
    ? `${h2hTeamMeetings} FINISHED TEAM MEETINGS SEARCHED${h2hRange ? ` · ${h2hRange}` : ''}`
    : 'NO FINISHED DIRECT TEAM MEETINGS FOUND';
  const playerMatches = Array.isArray(h2h.matches) ? h2h.matches : [];
  const h2hRows = playerMatches
    .slice()
    .sort(newestFirst)
    .slice(0, 20)
    .map((match: any) => ({
      ...match,
      value: match.targetStat ?? match.value,
      possession: match.teamPossession,
      teamPossession: match.teamPossession,
    }));
  const normalizedLogs: Array<Record<string, any>> = rawLogs
    .map((game: Record<string, any>) => ({
      ...game,
      value: game.value
        ?? (targetField ? game[targetField] : undefined)
        ?? game.targetStat
        ?? game.statValue
        ?? game.stat
        ?? null,
    })) as Array<Record<string, any>>;
  const allLogs = normalizedLogs
    .filter((game) => (
      !game.synthetic
      && game.value != null
    ))
    .sort(newestFirst)
    .slice(0, 50);
  const venueCounts = {
    home: allLogs.filter((game) => rowVenue(game) === 'home').length,
    away: allLogs.filter((game) => rowVenue(game) === 'away').length,
  };
  const [recentVenueFilter, setRecentVenueFilter] = useState<RecentVenueFilter>('all');
  const recentLogs = recentVenueFilter === 'all' || recentVenueFilter === 'h2h'
    ? allLogs
    : allLogs.filter((game) => rowVenue(game) === recentVenueFilter);
  const logs = recentVenueFilter === 'h2h' ? h2hRows : recentLogs;
  const isH2HFilter = recentVenueFilter === 'h2h';
  const historicalHitRates = prediction.modelHitRates
    ?? prediction.playerGameLogs?.modelHitRates
    ?? (prediction as any).hitRates
    ?? prediction.playerGameLogs?.hitRates
    ?? null;
  const deviationHitRate = Number.isFinite(Number((prediction as any).lineDeviationHitRate))
    ? Number((prediction as any).lineDeviationHitRate)
    : null;
  const deviationHitRateN = Number((prediction as any).lineDeviationHitRateN) > 0
    ? Number((prediction as any).lineDeviationHitRateN)
    : null;
  const modelHistoryScope = venueHistory?.modelScope === 'selected_venue'
    ? `${String(venueHistory.selectedVenue || historyVenue || 'selected').toUpperCase()} VENUE`
    : 'MIXED VERIFIED HISTORY';
  const modelHistorySample = Number(venueHistory?.modelSampleSize || historicalHitRates?.total || 0);
  const leagueRoleBucket = String(
    prediction.leagueRoleBucket
      ?? prediction.playerGameLogs?.leagueRoleBucket
      ?? '',
  ).trim();
  const settledRate = Number.isFinite(Number((prediction as any).propHistoricalRate))
    ? Number((prediction as any).propHistoricalRate)
    : null;
  const settledSample = Number((prediction as any).propHistoricalN) > 0
    ? Number((prediction as any).propHistoricalN)
    : null;
  const recommendation = String(prediction.recommendation || '').toUpperCase();
  const settledDirection = recommendation === 'OVER' || recommendation === 'UNDER'
    ? recommendation
    : null;
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
  // Pass props do not show the aggregate pass-volume context card. The
  // player history and deterministic projection are the relevant evidence
  // here; the team/opponent volume estimates were confusing and redundant.
  const hasMarketEvidence = Boolean(matchupVolume?.available && (isSotProp || isGkProp));
  const homeSplit = prediction.homeAvg != null
    ? { average: Number(prediction.homeAvg), count: venueCounts.home }
    : averageForVenue(normalizedLogs, 'home');
  const awaySplit = prediction.awayAvg != null
    ? { average: Number(prediction.awayAvg), count: venueCounts.away }
    : averageForVenue(normalizedLogs, 'away');
  const h2hHomeSplit = h2h.venueSplits?.home?.average != null
    ? {
        average: Number(h2h.venueSplits.home.average),
        count: Number(h2h.venueSplits.home.sampleSize || 0),
      }
    : averageForVenue(h2hRows, 'home');
  const h2hAwaySplit = h2h.venueSplits?.away?.average != null
    ? {
        average: Number(h2h.venueSplits.away.average),
        count: Number(h2h.venueSplits.away.sampleSize || 0),
      }
    : averageForVenue(h2hRows, 'away');
  const hasHistoryEvidence = Boolean(historicalHitRates || settledRate != null || deviationHitRate != null);
  const showForm = section === 'overview' || section === 'form';
  const showMatchup = section === 'overview' || section === 'matchup' || section === 'model';
  const showModel = section === 'overview' || section === 'matchup' || section === 'model';
  const showRecent = showForm && (allLogs.length > 0 || h2hRows.length > 0);
  const showHistory = showModel && hasHistoryEvidence;
  const showMarket = showMatchup && hasMarketEvidence;
  const metadataLabel = metadataCoverage?.total
    ? ` · DATE ${Number(metadataCoverage.dated || 0)}/${Number(metadataCoverage.total)} · VENUE ${Number(metadataCoverage.withVenue || 0)}/${Number(metadataCoverage.total)}`
    : '';
  const displayLogs = logs;
  const chartMaxValue = Math.max(
    ...displayLogs.map((item) => Number(item.value) || 0),
    prediction.line ?? 0,
    1,
  ) * 1.18;
  const tacticalProfiles: Array<Record<string, any>> = Array.isArray(
    (prediction.tacticalContext as any)?.recentOpponentBlockProfiles?.profiles,
  )
    ? (prediction.tacticalContext as any).recentOpponentBlockProfiles.profiles
    : [];
  const pressureProfiles: Array<Record<string, any>> = Array.isArray(
    (prediction.tacticalContext as any)?.recentOpponentPressIntensity?.profiles,
  )
    ? (prediction.tacticalContext as any).recentOpponentPressIntensity.profiles
    : [];
  const pressureScope = String(
    (prediction.tacticalContext as any)?.recentOpponentPressIntensity?.sampleUnit
      ?? pressureProfiles.find((profile) => profile?.pressureScope)?.pressureScope
      ?? '',
  );
  const pressureIsVenueScoped = pressureScope.includes('same_venue');
  // Historical pressure labels remain supported for non-recent detail
  // rendering, but the recent-match card intentionally does not surface the
  // custom Reverse Picks Pressure Index or its VERY LOW/LOW/MODERATE/HIGH/
  // ELITE recent-opponent breakdown.
  const tacticalProfileFor = (game: Record<string, any>): Record<string, any> | null => {
    const byFixture = tacticalProfiles.find((profile) => (
      profile?.fixtureId != null
      && game?.fixtureId != null
      && String(profile.fixtureId) === String(game.fixtureId)
    ));
    if (byFixture) return byFixture;
    return tacticalProfiles.find((profile) => (
      String(profile?.date || '').slice(0, 10) === String(game?.date || '').slice(0, 10)
      && String(profile?.opponent || '').toLowerCase() === String(game?.opponent || '').toLowerCase()
    )) ?? null;
  };
  const pressureProfileFor = (game: Record<string, any>): Record<string, any> | null => {
    const byFixture = pressureProfiles.find((profile) => (
      profile?.fixtureId != null
      && game?.fixtureId != null
      && String(profile.fixtureId) === String(game.fixtureId)
    ));
    if (byFixture) return byFixture;
    return pressureProfiles.find((profile) => (
      String(profile?.date || '').slice(0, 10) === String(game?.date || '').slice(0, 10)
      && String(profile?.opponent || '').toLowerCase() === String(opponentName(game) || '').toLowerCase()
    )) ?? null;
  };
  const pressurePacketFor = (game: Record<string, any>): Record<string, any> | null => (
    pressureProfileFor(game)?.pressIntensity
    ?? (pressureIsVenueScoped && rowVenue(game) !== historyVenue
      ? null
      : tacticalProfileFor(game)?.pressIntensity)
    ?? null
  );
  const isDenseRecent = !isH2HFilter;
  const last10Logs = recentLogs.slice(0, 10);
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
        group: 'recent' as const,
        index: allLogs.findIndex((row: any) => rowVenue(row) === preferredVenue),
      }
    : null;
  const safeInitialSelection = initialSelection && initialSelection.index >= 0
    ? initialSelection
    : null;
  const [selected, setSelected] = useState<{ group: 'recent' | 'h2h'; index: number } | null>(safeInitialSelection);
  useEffect(() => {
    setSelected(safeInitialSelection);
  }, [prediction.fixtureId, prediction.playerName, prediction.line, preferredVenue, h2hRows.length, safeInitialSelection?.index]);

  useEffect(() => {
    setRecentVenueFilter('all');
  }, [prediction.fixtureId, prediction.playerId, prediction.playerName]);

  useEffect(() => {
    setSelected(null);
  }, [recentVenueFilter]);

  const selectBar = (group: 'recent' | 'h2h', index: number) => {
    setSelected((current) => current?.group === group && current.index === index ? null : { group, index });
    // selectionAsync is a barely audible UI-selection tick. The app's
    // established bar/button interaction uses a Light impact instead.
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
  };
  const selectedGame = selected?.group === 'h2h'
    ? h2hRows[selected.index]
    : selected?.group === 'recent'
      ? logs[selected.index]
      : null;
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
  const possessionMeta = (
    (prediction as any).matchDominance
      ?? (prediction as any).matchupOverview
      ?? (prediction as any).matchFactors
      ?? {}
  ) as Record<string, unknown>;
  const possessionVerificationStatus = String(
    (prediction as any).possessionVerificationStatus
      ?? possessionMeta.possessionVerificationStatus
      ?? possessionStatus,
  ).toLowerCase();
  const possessionRequired = Number(
    (prediction as any).possessionSampleRequired
      ?? possessionMeta.possessionSampleRequired
      ?? 10,
  );
  const teamPossessionSample = Number(
    (prediction as any).teamPossessionSampleSize
      ?? possessionMeta.teamPossessionSampleSize
      ?? 0,
  );
  const opponentPossessionSample = Number(
    (prediction as any).opponentPossessionSampleSize
      ?? possessionMeta.opponentPossessionSampleSize
      ?? 0,
  );
  const teamPossessionVenue = String(
    possessionMeta.teamPossessionVenue ?? 'team',
  ).toUpperCase();
  const opponentPossessionVenue = String(
    possessionMeta.opponentPossessionVenue ?? 'opponent',
  ).toUpperCase();
  const observedTeamPossession = Number(
    possessionMeta.teamPossessionObservedAvg,
  );
  const observedOpponentPossession = Number(
    possessionMeta.opponentPossessionObservedAvg,
  );
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
      {showMatchup && showPossessionContext && hasExpectedPossession && (
        <View style={styles.possessionCard}>
          <View style={styles.header}>
            <View style={styles.headerLeft}>
              <Ionicons name="football-outline" size={11} color={Colors.primary} />
              <Text style={styles.title}>EXPECTED POSSESSION</Text>
            </View>
            <Text style={styles.meta}>
              {possessionCalculationStatus}
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
          <Text style={styles.possessionEvidence} numberOfLines={2}>
            {possessionSampleLabel}
          </Text>
          <Text style={styles.possessionEvidence} numberOfLines={2}>
            {possessionEvidenceLabel}
          </Text>
           {teamPossessionRows.length > 0 && opponentPossessionRows.length > 0 && (
             <View style={{ marginTop: 4 }}>
               <Text style={styles.possessionEvidence} numberOfLines={1}>
                 HOME POSSESSION MATCHES USED · {teamPossessionRows.length} latest verified
               </Text>
               {teamPossessionRows.slice(0, 10).map((row, index) => (
                 <Text key={`team-possession-${String(row.fixtureId ?? index)}`} style={styles.possessionEvidence} numberOfLines={1}>
                   {String(row.date ?? 'Unknown date')} · vs {String(row.opponent ?? 'Unknown')} · {Number(row.value).toFixed(1)}%
                 </Text>
               ))}
               <Text style={[styles.possessionEvidence, { marginTop: 3 }]} numberOfLines={1}>
                 AWAY POSSESSION MATCHES USED · {opponentPossessionRows.length} latest verified
               </Text>
               {opponentPossessionRows.slice(0, 10).map((row, index) => (
                 <Text key={`opponent-possession-${String(row.fixtureId ?? index)}`} style={styles.possessionEvidence} numberOfLines={1}>
                   {String(row.date ?? 'Unknown date')} · vs {String(row.opponent ?? 'Unknown')} · {Number(row.value).toFixed(1)}%
                 </Text>
               ))}
             </View>
           )}
        </View>
      )}

      {(showRecent || showHistory || showMarket) && (
        <View style={styles.card}>
          {showRecent && <View style={styles.header}>
            <View style={styles.headerLeft}>
              <Ionicons name="pulse" size={11} color={Colors.primary} />
              <View style={styles.headerStack}>
                <Text style={styles.title}>
                  {/* Source contract: RECENT MATCHES · {logs.length}; keep
                      saved/live history title shape stable for older checks.
                      Source contract also accepts minutesPlayed ?? row.minutes. */}
                  {isH2HFilter
                    ? `H2H · ${h2hRows.length > 0 ? h2hRows.length : h2hTeamMeetings > 0 ? 'NO PLAYER APPS' : 'NO MEETINGS'}`
                    : logs.length > 0 ? `RECENT MATCHES · ${logs.length}` : 'MATCH HISTORY'}
                </Text>
                {logs.length > 0 && (
                  <Text style={styles.contextLabel} numberOfLines={1}>
                     {isH2HFilter
                       ? 'PLAYER APPS VS OPPONENT'
                       : recentVenueFilter === 'all'
                       ? 'ALL VENUES · MATCHES SHOWN'
                       : `${recentVenueFilter.toUpperCase()} MATCHES · FILTERED`}
                  </Text>
                )}
                <Text style={styles.recentInlineStats} numberOfLines={isH2HFilter ? 2 : 1}>
                  {isH2HFilter
                    ? h2hRows.length > 0
                      ? `HOME AVG ${h2hHomeSplit?.average?.toFixed(1) ?? '—'}${h2hHomeSplit?.count ? ` (N=${h2hHomeSplit.count})` : ''} · AWAY AVG ${h2hAwaySplit?.average?.toFixed(1) ?? '—'}${h2hAwaySplit?.count ? ` (N=${h2hAwaySplit.count})` : ''}`
                      : h2hTeamMeetings > 0
                        ? `${h2hTeamMeetings} TEAM MEETINGS · 0 VERIFIED PLAYER APPS`
                        : 'NO FINISHED TEAM MEETINGS IN DIRECT HISTORY'
                     : `HOME ${venueCounts.home} · AWAY ${venueCounts.away} VERIFIED PLAYER APPEARANCES${homeSplit || awaySplit ? ` · AVG ${homeSplit?.average?.toFixed(1) ?? '—'} / ${awaySplit?.average?.toFixed(1) ?? '—'}` : ''}${metadataLabel}`}
                </Text>
                {isH2HFilter && (
                  <Text style={styles.contextWarning} numberOfLines={1}>
                    {h2hCoverageLabel}
                  </Text>
                )}
                {!isH2HFilter && venueHistoryFallback && (
                  <Text style={styles.contextWarning} numberOfLines={1}>
                     {Number.isFinite(venueHistorySample) ? venueHistorySample : 0}/{venueHistoryTarget} {String(historyVenue || 'SELECTED VENUE').toUpperCase()} VERIFIED · ALL-VENUE PRIOR USED
                  </Text>
                )}
              </View>
            </View>
            {lineEditor || (prediction.line != null && <Text style={styles.meta}>LINE {prediction.line}</Text>)}
          </View>}
          {showRecent && (
            <View style={styles.venueFilterRow}>
               <Text style={styles.venueFilterLabel}>VIEW</Text>
              {(['all', 'home', 'away', 'h2h'] as RecentVenueFilter[]).map((venue) => (
                <TouchableOpacity
                  key={venue}
                  onPress={() => {
                    setRecentVenueFilter(venue);
                    Haptics.selectionAsync().catch(() => undefined);
                  }}
                  activeOpacity={0.75}
                  style={[styles.venueFilterPill, recentVenueFilter === venue && styles.venueFilterPillActive]}
                  accessibilityRole="button"
                  accessibilityState={{ selected: recentVenueFilter === venue }}
                >
                  <Text style={[styles.venueFilterText, recentVenueFilter === venue && styles.venueFilterTextActive]}>
                     {venue === 'all' ? 'ALL' : venue.toUpperCase()}
                  </Text>
                </TouchableOpacity>
              ))}
              <Text style={styles.venueFilterCounts}>
                {isH2HFilter ? `N ${h2hRows.length}` : `H ${venueCounts.home} · A ${venueCounts.away}`}
              </Text>
            </View>
          )}
           {/* Recent-match history stays focused on player stat outcomes.
               Opponent pressure is measured and shown for the next matchup
               in the tactical context card instead. */}
            {showRecent && <>{displayLogs.length > 0 ? <ScrollView
              horizontal={!isDenseRecent}
              showsHorizontalScrollIndicator={!isDenseRecent}
              contentContainerStyle={isDenseRecent ? styles.denseScrollContent : styles.scrollContent}
            >
                 <View style={isDenseRecent ? styles.denseChartWidth : { width: displayLogs.length * 126 + 10 }}>
               <View style={[styles.chart, isDenseRecent && styles.denseChart]}>
                 {displayLogs.map((game, index) => {
                  const value = Number(game.value);
                  const color = prediction.line != null && value > prediction.line ? Colors.success : Colors.error;
                    const height = Math.max(7, (value / chartMaxValue) * 78);
                  const date = game.date ? displayH2HDate(game.date) : '—';
                   const possession = game.teamPossession != null ? `TP ${Number(game.teamPossession).toFixed(0)}%` : 'TP —';
                  const minutes = game.minutesPlayed ?? game.minutes;
                   const tacticalProfile = tacticalProfileFor(game);
                    const pressureProfile = pressureProfileFor(game);
                    const pressurePacket = pressurePacketFor(game);
                   const pressureAvailable = pressurePacket?.available === true
                     && String(pressurePacket?.label || '').trim().length > 0;
                   const pressureLabel = pressureAvailable
                      ? reversePicksPressureLabel(pressurePacket)
                     : 'UNAVAILABLE';
                    const pressureSample = pressureAvailable
                      ? `N=${Number(pressureProfile?.sampleTarget || pressurePacket?.sampleTarget || pressurePacket?.sampleSize || 0)} RECENT`
                      : 'NO VERIFIED OPPONENT SAMPLE';
                   const pressureColor = !pressureAvailable
                     ? '#667085'
                     : pressureLabel === 'ELITE'
                       ? '#FF453A'
                       : pressureLabel === 'HIGH'
                         ? '#FF9F0A'
                        : pressureLabel === 'MODERATE'
                           ? '#60A5FA'
                            : pressureLabel === 'LOW'
                              ? '#8EDB8A'
                           : '#34C759';
                   const blockLabel = String(tacticalProfile?.blockProfile?.label || 'UNAVAILABLE')
                    .replace('_BLOCK', '')
                    .replace('UNAVAILABLE', 'UNAVAIL');
                   const isSelected = selected?.group === (isH2HFilter ? 'h2h' : 'recent') && selected.index === index;
                  return (
                    <TouchableOpacity
                      key={`${date}-${index}`}
                        style={[
                          styles.barColumn,
                          isDenseRecent && styles.barColumnDense,
                          preferredVenue && rowVenue(game) === preferredVenue && styles.barColumnVenueSelected,
                          isSelected && styles.barColumnSelected,
                        ]}
                       onPress={() => selectBar(isH2HFilter ? 'h2h' : 'recent', index)}
                      activeOpacity={0.8}
                      accessibilityLabel={`${game.opponent || 'Recent match'}, ${game.value} ${prediction.line != null ? `against line ${prediction.line}` : ''}`}
                    >
                      {isDenseRecent ? (
                        <>
                          <View style={styles.denseBarTrack}>
                            <View style={[styles.denseBar, { height, backgroundColor: color + 'D8' }]} />
                          </View>
                        </>
                      ) : (
                        <>
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
                          <Text
                            style={[
                              styles.blockVenueLabel,
                              { color: blockLabel === 'UNAVAIL' ? '#444' : '#8EDB8A' },
                            ]}
                            numberOfLines={1}
                          >
                            {blockLabel} · {venueMark(rowVenue(game))}
                          </Text>
                          <View style={styles.pressureRow}>
                            <Text style={[styles.pressureLabel, { color: pressureColor }]}>
                              PRESS {pressureLabel}
                            </Text>
                            <Text style={[styles.pressureSample, { color: pressureAvailable ? '#8B95A5' : '#667085' }]} numberOfLines={1}>
                              {pressureSample}
                            </Text>
                          </View>
                          {showSotEvidence && (
                            <Text style={styles.possessionLabel}>
                              OPP SOT {game.opponentShotsOnTarget != null ? Number(game.opponentShotsOnTarget).toFixed(0) : '—'}
                            </Text>
                          )}
                          <Text style={[styles.venueLabel, { color: rowVenue(game) === 'home' ? Colors.success : '#60A5FA' }]}>
                            {venueMark(rowVenue(game))}
                          </Text>
                        </>
                      )}
                    </TouchableOpacity>
                  );
                })}
              </View>
              {selectedGame && (
                <>
                  <Text style={styles.detail}>
                    {selectedGame.date ? displayH2HDate(selectedGame.date) : 'Match'} · {selectedGame.opponent || 'Opponent'} · {selectedGame.value} stat · {rowVenue(selectedGame) === 'home' ? 'HOME' : 'AWAY'}
                   {showPossessionContext
                     ? detailPossession != null ? ` · POSS ${detailPossession}%` : ' · POSS unavailable'
                     : ''}
                   {selectedGame.score ? ` · ${selectedGame.score}` : ''}
                   {selectedGame.competitionName
                     ? ` · ${selectedGame.competitionName} · ${stageLabelForRow(selectedGame)}`
                     : ''}
                   {showSotEvidence
                     ? ` · OPP SOT ${selectedGame.opponentShotsOnTarget != null ? Number(selectedGame.opponentShotsOnTarget).toFixed(0) : 'unavailable'}`
                     : ''}
                     {isH2HFilter && (() => {
                         const selectedPressureProfile = pressureProfileFor(selectedGame);
                         const selectedPressure = pressurePacketFor(selectedGame);
                        const selectedLabel = selectedPressure?.available === true
                          ? String(selectedPressure.label || 'Classified').toUpperCase()
                           : 'NO VERIFIED SAMPLE';
                        const selectedSample = selectedPressure?.available === true
                           ? ` · N=${Number(selectedPressureProfile?.sampleTarget || selectedPressure?.sampleTarget || selectedPressure?.sampleSize || 0)} RECENT`
                          : '';
                         return ` · PRESS ${selectedLabel}${selectedSample}`;
                      })()}
                  </Text>
                   {isH2HFilter && (() => {
                     const selectedPressureProfile = pressureProfileFor(selectedGame);
                     const selectedPressure = pressurePacketFor(selectedGame);
                    if (selectedPressure?.available === true) {
                      return (
                        <Text style={styles.pressureExplain} numberOfLines={3}>
                          {`Recent opponent profile using at least ${Number(selectedPressureProfile?.sampleTarget || selectedPressure?.sampleTarget || 5)} completed matches. `}
                          The qualitative pressure label is based on verified provider inputs; unavailable inputs are not treated as zero.
                        </Text>
                      );
                    }
                    return (
                      <Text style={styles.pressureExplain} numberOfLines={2}>
                         No verified recent opponent pressure profile is available yet. No 0/100 is shown because missing provider fields are not treated as zero pressure.
                      </Text>
                    );
                  })()}
                </>
              )}
            </View>
           </ScrollView> : isH2HFilter ? (
             <Text style={styles.empty}>
               {h2hTeamMeetings > 0
                 ? `No verified player appearance in ${h2hTeamMeetings} searched team meetings`
                 : 'No finished direct team meetings found'}
             </Text>
           ) : null}
          {showPossessionContext && (tpHomeSplit || tpAwaySplit) && (
            <Text style={styles.recentInlineStats}>
              TP H {tpHomeSplit?.average?.toFixed(0) ?? '—'}% · TP A {tpAwaySplit?.average?.toFixed(0) ?? '—'}%
            </Text>
          )}
          </>}
          {showHistory && (
            <View style={styles.historyInline}>
              <View style={styles.historyInlineHeader}>
                <View style={styles.headerLeft}>
                  <Ionicons name="stats-chart-outline" size={10} color={Colors.primary} />
                  <Text style={styles.subsectionTitle}>PLAYER HISTORY · MODEL SOURCE</Text>
                </View>
                {prediction.line != null && <Text style={styles.meta}>LINE {prediction.line}</Text>}
              </View>
              <Text style={styles.contextLabel} numberOfLines={1}>
                {modelHistoryScope} · N={modelHistorySample}
                {leagueRoleBucket ? ` · ${leagueRoleBucket}` : ''}
              </Text>
              <View style={styles.splitRow}>
                <View style={styles.splitItem}>
                  <Text style={styles.splitLabel}>OVER</Text>
                  <Text style={[styles.splitValue, { color: Colors.success }]}>
                    {historicalHitRates?.overPct != null ? `${Number(historicalHitRates.overPct).toFixed(1)}%` : '—'}
                  </Text>
                  <Text style={styles.splitMeta}>
                    {historicalHitRates?.overHits != null ? `${historicalHitRates.overHits} HITS` : 'NO SAMPLE'}
                  </Text>
                </View>
                <View style={styles.splitDivider} />
                <View style={styles.splitItem}>
                  <Text style={styles.splitLabel}>UNDER</Text>
                  <Text style={[styles.splitValue, { color: '#60A5FA' }]}>
                    {historicalHitRates?.underPct != null ? `${Number(historicalHitRates.underPct).toFixed(1)}%` : '—'}
                  </Text>
                  <Text style={styles.splitMeta}>
                    {historicalHitRates?.underHits != null ? `${historicalHitRates.underHits} HITS` : 'NO SAMPLE'}
                  </Text>
                </View>
              </View>
              <Text style={styles.detail}>
                Player history is the verified input pool for this projection. Calibration is system evidence, not this player&apos;s hit rate.
                {settledRate != null && settledDirection ? ` ${settledDirection} ${Number(settledRate).toFixed(1)}% · n=${settledSample ?? '—'}.` : ''}
                {deviationHitRate != null && settledDirection ? ` Deviation ${Number(deviationHitRate).toFixed(1)}% · n=${deviationHitRateN ?? '—'}.` : ''}
              </Text>
            </View>
          )}
          {showMarket && (
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

      {false && (historicalHitRates || settledRate != null || deviationHitRate != null) && (
        <View style={styles.card}>
          <View style={styles.header}>
            <View style={styles.headerLeft}>
              <Ionicons name="stats-chart-outline" size={11} color={Colors.primary} />
              <View style={styles.headerStack}>
                <Text style={styles.title}>PLAYER HISTORY · MODEL SOURCE</Text>
                <Text style={styles.contextLabel} numberOfLines={1}>
                  {modelHistoryScope} · N={modelHistorySample}
                  {leagueRoleBucket ? ` · ${leagueRoleBucket}` : ''}
                </Text>
              </View>
            </View>
            {historicalHitRates?.total != null && (
              <Text style={styles.meta}>LINE {prediction.line ?? '—'}</Text>
            )}
          </View>
          <View style={styles.splitRow}>
            <View style={styles.splitItem}>
              <Text style={styles.splitLabel}>OVER</Text>
              <Text style={[styles.splitValue, { color: Colors.success }]}>
                {historicalHitRates?.overPct != null ? `${Number(historicalHitRates.overPct).toFixed(1)}%` : '—'}
              </Text>
              <Text style={styles.splitMeta}>
                {historicalHitRates?.overHits != null
                  ? `${historicalHitRates.overHits} HITS`
                  : 'NO SAMPLE'}
              </Text>
            </View>
            <View style={styles.splitDivider} />
            <View style={styles.splitItem}>
              <Text style={styles.splitLabel}>UNDER</Text>
              <Text style={[styles.splitValue, { color: '#60A5FA' }]}>
                {historicalHitRates?.underPct != null ? `${Number(historicalHitRates.underPct).toFixed(1)}%` : '—'}
              </Text>
              <Text style={styles.splitMeta}>
                {historicalHitRates?.underHits != null
                  ? `${historicalHitRates.underHits} HITS`
                  : 'NO SAMPLE'}
              </Text>
            </View>
          </View>
          <Text style={styles.detail}>
            MODEL SOURCE = this player's verified history for the selected venue/prop.
          </Text>
          <Text style={[styles.detail, { marginTop: 0 }]}>
            CALIBRATION = settled system evidence used to adjust confidence, not this player's hit rate.
            {settledRate != null && settledDirection ? ` ${settledDirection} ${Number(settledRate).toFixed(1)}% · n=${settledSample ?? '—'}.` : ''}
            {deviationHitRate != null && settledDirection ? ` Deviation band ${Number(deviationHitRate).toFixed(1)}% · n=${deviationHitRateN ?? '—'}.` : ''}
          </Text>
        </View>
      )}

    </>
  );
});

const styles = {
  possessionCard: {
    marginTop: 6,
    backgroundColor: 'transparent',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.08)',
    paddingBottom: 9,
  },
  card: {
    marginTop: 6,
    backgroundColor: 'transparent',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.08)',
    paddingBottom: 5,
  },
  header: {
    paddingHorizontal: 14,
    paddingTop: 12,
    paddingBottom: 8,
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
  },
  headerLeft: { flexDirection: 'row' as const, alignItems: 'center' as const, gap: 6 },
  headerStack: { flex: 1, minWidth: 0 },
  title: { fontSize: 9, color: Colors.textSecondary, fontWeight: '800' as const, letterSpacing: 1 },
  contextLabel: { marginTop: 3, fontSize: 7, color: Colors.primary, fontWeight: '900' as const, letterSpacing: 0.45 },
  contextWarning: { marginTop: 3, fontSize: 7, color: '#F59E0B', fontWeight: '900' as const, letterSpacing: 0.35 },
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
  possessionEvidence: {
    marginHorizontal: 14,
    marginTop: 4,
    color: Colors.textTertiary,
    fontSize: 7,
    fontWeight: '800' as const,
    letterSpacing: 0.25,
  },
  scrollContent: { paddingHorizontal: 14, paddingBottom: 12 },
  chart: { height: 174, flexDirection: 'row' as const, alignItems: 'flex-end' as const, gap: 8 },
  blockVenueLabel: {
    fontSize: 7,
    lineHeight: 9,
    fontWeight: '900' as const,
    letterSpacing: 0.25,
  },
  pressureRow: {
    alignItems: 'center' as const,
    marginTop: 3,
    minHeight: 19,
  },
  pressureLabel: {
    fontSize: 7,
    lineHeight: 9,
    fontWeight: '900' as const,
    letterSpacing: 0.2,
    textAlign: 'center' as const,
  },
  pressureSample: {
    maxWidth: 112,
    fontSize: 6.5,
    lineHeight: 8,
    fontWeight: '800' as const,
    textAlign: 'center' as const,
  },
  pressureExplain: {
    paddingHorizontal: 2,
    paddingTop: 2,
    paddingBottom: 3,
    color: '#7C8796',
    fontSize: 8,
    lineHeight: 11,
    fontWeight: '700' as const,
  },
  recentInlineStats: {
    color: '#697586',
    fontSize: 7,
    fontWeight: '800' as const,
    letterSpacing: 0.45,
    marginTop: 2,
  },
  venueFilterRow: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    gap: 5,
    paddingHorizontal: 14,
    paddingBottom: 8,
  },
  venueFilterLabel: {
    color: Colors.textTertiary,
    fontSize: 7,
    fontWeight: '900' as const,
    letterSpacing: 0.7,
    marginRight: 2,
  },
  venueFilterPill: {
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 4,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.10)',
    backgroundColor: 'transparent',
  },
  venueFilterPillActive: {
    borderColor: Colors.primary + '88',
    backgroundColor: Colors.primary + '12',
  },
  venueFilterText: {
    color: Colors.textTertiary,
    fontSize: 7,
    fontWeight: '900' as const,
    letterSpacing: 0.5,
  },
  venueFilterTextActive: {
    color: Colors.primary,
  },
  venueFilterCounts: {
    marginLeft: 'auto' as const,
    color: Colors.textTertiary,
    fontSize: 7,
    fontWeight: '800' as const,
    letterSpacing: 0.35,
  },
  historyInline: {
    marginHorizontal: 14,
    marginTop: 8,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: 'rgba(255,255,255,0.07)',
  },
  historyInlineHeader: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    justifyContent: 'space-between' as const,
  },
  subsectionTitle: {
    color: Colors.textSecondary,
    fontSize: 8,
    fontWeight: '800' as const,
    letterSpacing: 0.85,
  },
  barColumn: { width: 116, height: 174, alignItems: 'center' as const, justifyContent: 'flex-end' as const, borderRadius: 6, paddingTop: 3, paddingHorizontal: 4 },
  barColumnDense: { flex: 1, width: undefined, minWidth: 3, height: 128, paddingHorizontal: 0, paddingTop: 0, borderRadius: 3, justifyContent: 'flex-end' as const },
  barColumnSelected: { backgroundColor: 'rgba(255,255,255,0.07)' },
  barColumnVenueSelected: { backgroundColor: 'rgba(57,255,20,0.055)' },
  value: { fontSize: 10, lineHeight: 12, fontWeight: '900' as const, marginBottom: 2 },
  bar: { width: 28, minHeight: 7, borderRadius: 4, justifyContent: 'flex-end' as const, alignItems: 'center' as const, position: 'relative' as const },
  possession: { position: 'absolute' as const, bottom: 4, color: '#FFF', fontSize: 6.5, fontWeight: '900' as const },
  possessionLabel: { fontSize: 7, color: '#8B95A5', lineHeight: 9, fontWeight: '800' as const },
  venueLabel: { fontSize: 8, lineHeight: 10, fontWeight: '900' as const, letterSpacing: 0.4 },
  date: { fontSize: 8.5, color: '#B1BAC7', fontWeight: '800' as const, lineHeight: 10, marginTop: 3 },
  opponent: { fontSize: 9.5, fontWeight: '800' as const, lineHeight: 11, marginTop: 1 },
  denseScrollContent: { width: '100%' as const, paddingHorizontal: 8 },
  denseChartWidth: { width: '100%' as const },
  denseChart: { height: 128, width: '100%' as const, gap: 0, alignItems: 'stretch' as const },
  denseBarTrack: { width: '100%' as const, height: 112, justifyContent: 'flex-end' as const, alignItems: 'center' as const },
  denseBar: { width: 3, minHeight: 3, borderRadius: 2 },
  detail: { paddingHorizontal: 2, paddingTop: 7, paddingBottom: 3, color: '#AAB4C2', fontSize: 10, lineHeight: 15 },
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