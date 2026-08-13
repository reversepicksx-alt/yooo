// This screen contains legacy sport-specific JSX kept for response compatibility.
// The active result path is the compact deterministic layout below; Expo/Babel
// remains the source-of-truth compiler for the intentionally dormant legacy tree.
// @ts-nocheck
import React, { useState, useRef, useEffect } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  TextInput, ActivityIndicator, Alert, Platform, Modal, Image, Dimensions,
  KeyboardAvoidingView, Animated, Linking,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import Colors from '@/constants/colors';
import NotificationBell from '@/components/NotificationBell';
import { useQueryClient } from '@tanstack/react-query';
import { scanProp, predict, cs2Predict, wtaPredict, nbaPredict, nhlPredict, mlbPredict, nflPredict, savePick, searchCs2Players, searchCs2Teams, searchWtaPlayers, searchNbaPlayers, searchNhlPlayers, searchMlbPlayers, searchNflPlayers, PROP_TYPES, CS2_PROP_TYPES, WTA_PROP_TYPES, WTA_SURFACES, WTA_ROUNDS, NBA_PROP_TYPES, NHL_PROP_TYPES, MLB_PROP_TYPES, NFL_PROP_TYPES, LEAGUES, PredictionResult, ScanResult, Cs2Player, Cs2Team, WtaPlayer, NbaPlayer, NhlPlayer, MlbPlayer, NflPlayer, getPlayerContexts, getTeamNextMatch, getLeagueById, PlayerContext, NextMatchData, getCs2NextMatch, getWtaNextMatch, getNbaNextMatch, getNhlNextMatch, getMlbNextMatch, getNflNextMatch, Cs2NextMatch, WtaNextMatch, NbaNextMatch, NhlNextMatch, MlbNextMatch, NflNextMatch, resolvePlayerRole, PlayerRoleResult, startChat, sendChatMessage, syncAppleAccess, verifySession } from '@/lib/api';
import FuzzySearchInput, { FuzzyTeamResult, FuzzyPlayerResult, FuzzyLeagueResult, StaticItem, UniversalPlayerResult } from '@/components/FuzzySearchInput';
import LeaguePickerModal from '@/components/LeaguePickerModal';
import { useAuth } from '@/contexts/AuthContext';
import LoadingScreen from '@/components/LoadingScreen';
import PitchDiagram from '@/components/PitchDiagram';
import { CompactAnalysisBars, getTacticalRead } from '@/components/CompactAnalysisBars';
import EventEvidenceCard from '@/components/EventEvidenceCard';
import SameRoleEvidenceCard from '@/components/SameRoleEvidenceCard';
import Reanimated, { FadeInDown } from 'react-native-reanimated';
import { LinearGradient } from 'expo-linear-gradient';
import Purchases from 'react-native-purchases';
import { REVENUECAT_ENTITLEMENT_IDENTIFIER, useSubscription } from '@/lib/revenuecat';


const SCREEN_W = Dimensions.get('window').width;
const SCREEN_H = Dimensions.get('window').height;
const INPUT_STYLE = Platform.OS === 'web' ? { outlineWidth: 0 } as object : {};
type RenderPredictionValue<T> =
  T extends (...args: any[]) => any ? T :
  T extends readonly (infer U)[] ? RenderPredictionValue<U>[] :
  T extends object ? { [K in keyof T]-?: RenderPredictionValue<NonNullable<T[K]>> } :
  NonNullable<T>;

/**
 * H2H uses the same dense vertical-bar language as the recent game log.
 * Player appearances use the prop value for height; team-only meetings use
 * verified player-team possession so an opponent meeting is still useful
 * without pretending the player logged a stat.
 */
function H2HBarCard({ prediction }: { prediction: PredictionResult }) {
  const h2h = prediction.h2hPlayerStats as any;
  const playerMatches = Array.isArray(h2h?.matches) ? h2h.matches : [];
  const meetingsByVenue = h2h?.teamMeetingsByVenue ?? {};
  const teamMeetings = [
    ...(Array.isArray(meetingsByVenue.home)
      ? meetingsByVenue.home.map((m: any) => ({ ...m, venue: 'home', possession: m.homePossession }))
      : []),
    ...(Array.isArray(meetingsByVenue.away)
      ? meetingsByVenue.away.map((m: any) => ({ ...m, venue: 'away', possession: m.awayPossession }))
      : []),
  ];
  const rows = playerMatches.length
    ? playerMatches.slice(0, 8).map((m: any) => ({
        ...m,
        possession: m.teamPossession,
        displayValue: m.targetStat,
        teamOnly: false,
      }))
    : teamMeetings.slice(0, 8).map((m: any) => ({
        ...m,
        displayValue: m.possession,
        teamOnly: true,
      }));

  if (rows.length === 0) {
    return (
      <View style={{ marginTop: 8, padding: 12, borderTopWidth: 1, borderTopColor: '#181818' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Ionicons name="swap-horizontal-outline" size={12} color={Colors.textTertiary} />
          <Text style={{ fontSize: 9, color: Colors.textTertiary, fontWeight: '800', letterSpacing: 1 }}>
            H2H · NO VERIFIED HISTORY
          </Text>
        </View>
      </View>
    );
  }

  const numericValues = rows
    .map((row: any) => typeof row.displayValue === 'number' ? row.displayValue : null)
    .filter((value: number | null): value is number => value != null);
  const possessionOnly = rows.every((row: any) => row.teamOnly);
  const maxValue = Math.max(
    ...numericValues,
    possessionOnly ? 100 : (prediction.line ?? 0),
    1,
  ) * 1.18;
  const chartHeight = 112;
  const barWidth = 34;
  const gap = 5;
  const accent = prediction.recommendation === 'UNDER' ? Colors.error : Colors.success;

  return (
    <View style={{
      marginTop: 8, backgroundColor: '#0A0A0A', borderRadius: 12,
      borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)', overflow: 'hidden',
    }}>
      <View style={{ paddingHorizontal: 14, paddingTop: 12, paddingBottom: 8, flexDirection: 'row', alignItems: 'center' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Ionicons name="swap-horizontal-outline" size={11} color={Colors.primary} />
          <Text style={{ fontSize: 9, color: Colors.textSecondary, fontWeight: '800', letterSpacing: 1 }}>
            H2H · {h2h?.sampleSize ? `${h2h.sampleSize} APPS` : `${rows.length} TEAM MEETS`}
          </Text>
        </View>
        {h2h?.avgVsOpponent != null && (
          <Text style={{ marginLeft: 'auto', fontSize: 10, color: Colors.textTertiary, fontFamily: 'JetBrainsMono_700Bold' }}>
            AVG {Number(h2h.avgVsOpponent).toFixed(1)}
          </Text>
        )}
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 14, paddingBottom: 9 }}>
        <View style={{ width: rows.length * (barWidth + gap) + 10 }}>
          <View style={{ height: chartHeight, flexDirection: 'row', alignItems: 'flex-end', gap }}>
            {rows.map((row: any, index: number) => {
              const value = typeof row.displayValue === 'number' ? row.displayValue : null;
              const barHeight = value != null
                ? Math.max(10, (value / maxValue) * chartHeight)
                : 10;
              const isOver = value != null && prediction.line != null && !row.teamOnly && value > prediction.line;
              const barColor = row.teamOnly
                ? '#4A6CFF'
                : isOver ? Colors.success : value != null ? Colors.error : '#444';
              const possessionText = row.possession != null ? `POSS ${row.possession}%` : 'POSS N/A';
              const date = row.date ? String(row.date).slice(5, 10) : '—';
              const venue = row.venue === 'home' || row.venue === 'away'
                ? row.venue.slice(0, 1).toUpperCase()
                : '–';
              const opponent = row.opponent || row.homeTeam || 'TEAM';
              const opponentShort = String(opponent).replace(/^(al-?|fc |cf |rc |sc |cd |ud |sd |rcd |as |ss |ac |us |sp |ca |cp |ue |ce |cm |se |sk )/i, '').slice(0, 4).toUpperCase();
              return (
                <View key={`${date}-${index}`} style={{ width: barWidth, height: chartHeight, alignItems: 'center', justifyContent: 'flex-end' }}>
                  <Text style={{ fontSize: 8, color: value != null && !row.teamOnly ? barColor : Colors.textTertiary, fontWeight: '800', marginBottom: 2 }}>
                    {value != null && !row.teamOnly ? value : row.teamOnly && value != null ? `${value}%` : '—'}
                  </Text>
                  <View style={{
                    width: barWidth - 6, height: barHeight, borderRadius: 3,
                    backgroundColor: barColor + 'B8', position: 'relative',
                    justifyContent: 'flex-end', alignItems: 'center',
                  }}>
                    <Text style={{ position: 'absolute', bottom: 4, color: '#FFF', fontSize: 6.5, fontWeight: '900', letterSpacing: 0.1 }}>
                      {possessionText}
                    </Text>
                  </View>
                   <Text style={{ fontSize: 7, color: '#777', lineHeight: 10, marginTop: 4 }}>
                     {date} · {venue}
                   </Text>
                  <Text style={{ fontSize: 7, color: row.teamOnly ? '#4A6CFF' : accent, fontWeight: '700', lineHeight: 10 }}>{opponentShort}</Text>
                </View>
              );
            })}
          </View>
          <View style={{ marginTop: 5, flexDirection: 'row', alignItems: 'center', gap: 5 }}>
            <View style={{ width: 5, height: 5, borderRadius: 3, backgroundColor: '#4A6CFF' }} />
            <Text style={{ fontSize: 7, color: '#555' }}>{possessionOnly ? 'team meeting · player did not appear' : 'player appearance'}</Text>
            <Text style={{ marginLeft: 'auto', fontSize: 7, color: '#444' }}>POSS = verified team share</Text>
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

function RecentBarCard({ prediction }: { prediction: PredictionResult }) {
  const logs = (prediction.gameLogs ?? []).filter((game: any) => !game.synthetic && game.value != null).slice(0, 10);
  if (logs.length === 0) return null;
  const line = prediction.line;
  const maxValue = Math.max(...logs.map((game: any) => Number(game.value) || 0), line ?? 0, 1) * 1.18;
  const chartHeight = 112;
  const barWidth = 34;
  const gap = 5;

  return (
    <View style={{
      marginTop: 8, backgroundColor: '#0A0A0A', borderRadius: 12,
      borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)', overflow: 'hidden',
    }}>
      <View style={{ paddingHorizontal: 14, paddingTop: 12, paddingBottom: 8, flexDirection: 'row', alignItems: 'center' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Ionicons name="pulse" size={11} color={Colors.primary} />
          <Text style={{ fontSize: 9, color: Colors.textSecondary, fontWeight: '800', letterSpacing: 1 }}>
            RECENT MATCHES · {logs.length}
          </Text>
        </View>
        {line != null && (
          <Text style={{ marginLeft: 'auto', fontSize: 9, color: Colors.textTertiary, fontFamily: 'JetBrainsMono_700Bold' }}>
            LINE {line}
          </Text>
        )}
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 14, paddingBottom: 12 }}>
        <View style={{ width: logs.length * (barWidth + gap) + 10 }}>
          <View style={{ height: chartHeight, flexDirection: 'row', alignItems: 'flex-end', gap }}>
            {logs.map((game: any, index: number) => {
              const value = Number(game.value);
              const isOver = line != null && value > line;
              const color = isOver ? Colors.success : Colors.error;
              const height = Math.max(10, (value / maxValue) * chartHeight);
              const date = game.date ? String(game.date).slice(5, 10) : '—';
              const opponent = String(game.opponent || '?')
                .replace(/^(al-?|fc |cf |rc |sc |cd |ud |sd |rcd |as |ss |ac |us |sp |ca |cp |ue |ce |cm |se |sk )/i, '')
                .slice(0, 4).toUpperCase();
              return (
                <View key={`${date}-${index}`} style={{ width: barWidth, height: chartHeight, alignItems: 'center', justifyContent: 'flex-end' }}>
                  <Text style={{ fontSize: 9, color, fontWeight: '800', marginBottom: 2 }}>{game.value}</Text>
                  <View style={{ width: barWidth - 6, height, borderRadius: 3, backgroundColor: color + 'B8' }} />
                  <Text style={{ fontSize: 7, color: '#555', lineHeight: 10, marginTop: 4 }}>{date}</Text>
                  <Text style={{ fontSize: 7, color: game.venue === 'home' ? Colors.success : '#60A5FA', fontWeight: '700', lineHeight: 10 }}>
                    {opponent}
                  </Text>
                                   {['saves', 'goalie_saves'].includes(String(prediction.propType || '')) && (
                                     <Text style={{ fontSize: 7, color: '#60A5FA', fontWeight: '700', lineHeight: 10 }}>
                                       SOT {game.opponentShotsOnTarget != null ? Number(game.opponentShotsOnTarget).toFixed(0) : '—'}
                                     </Text>
                                   )}
                </View>
              );
            })}
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

// Static list of FIFA international nations for World Cup opponent fuzzy search
const WC_NATIONS: StaticItem[] = [
  'Afghanistan','Albania','Algeria','Andorra','Angola','Antigua and Barbuda','Argentina','Armenia',
  'Australia','Austria','Azerbaijan','Bahrain','Bangladesh','Belarus','Belgium','Benin',
  'Bolivia','Bosnia and Herzegovina','Botswana','Brazil','Bulgaria','Burkina Faso','Burundi',
  'Cameroon','Canada','Cape Verde','Central African Republic','Chile','China','Colombia',
  'Comoros','Congo','Costa Rica','Croatia','Cuba','Cyprus','Czech Republic','Côte d\'Ivoire',
  'Denmark','Dominican Republic','DR Congo','Ecuador','Egypt','El Salvador','England',
  'Equatorial Guinea','Estonia','Ethiopia','Faroe Islands','Finland','France','Gabon',
  'Georgia','Germany','Ghana','Gibraltar','Greece','Guatemala','Guinea','Guinea-Bissau',
  'Haiti','Honduras','Hungary','Iceland','India','Indonesia','Iran','Iraq','Ireland',
  'Israel','Italy','Jamaica','Japan','Jordan','Kazakhstan','Kenya','Kosovo','Kuwait',
  'Latvia','Lebanon','Liberia','Libya','Liechtenstein','Lithuania','Luxembourg','Malawi',
  'Malaysia','Mali','Malta','Mauritania','Mexico','Moldova','Montenegro','Morocco',
  'Mozambique','Namibia','Netherlands','New Zealand','Nicaragua','Niger','Nigeria',
  'North Korea','North Macedonia','Northern Ireland','Norway','Oman','Palestine','Panama',
  'Paraguay','Peru','Philippines','Poland','Portugal','Qatar','Republic of Ireland',
  'Romania','Rwanda','San Marino','Saudi Arabia','Scotland','Senegal','Serbia',
  'Sierra Leone','Slovakia','Slovenia','Somalia','South Africa','South Korea','Spain',
  'Sudan','Sweden','Switzerland','Syria','Tanzania','Thailand','Togo','Trinidad and Tobago',
  'Tunisia','Turkey','Uganda','Ukraine','United Arab Emirates','United States','Uruguay',
  'Uzbekistan','Venezuela','Vietnam','Wales','Zambia','Zimbabwe',
].map(name => ({ id: name, primary: name }));

const PROP_LABELS: Record<string, string> = {
  // Soccer
  pass_attempts: 'Pass Attempts', shots: 'Shots', shots_on_target: 'Shots on Target',
  goals: 'Goals', assists: 'Assists', key_passes: 'Key Passes',
  tackles: 'Tackles', saves: 'Saves', dribbles: 'Dribbles', crosses: 'Crosses',
  interceptions: 'Interceptions', blocks: 'Blocks', fouls_drawn: 'Fouls Drawn',
  fouls_committed: 'Fouls', clearances: 'Clearances', yellow_cards: 'Yellow Cards',
  shots_assisted: 'Shot Assists', duels_won: 'Duels Won', passes: 'Passes',
  // MLB — hitter
  hits: 'Hits', home_runs: 'Home Runs', rbi: 'RBI', runs: 'Runs',
  walks: 'Walks', strikeouts: 'Strikeouts', total_bases: 'Total Bases',
  stolen_bases: 'Stolen Bases', doubles: 'Doubles', plate_appearances: 'Plate Appearances',
  hits_runs_rbis: 'H+R+RBI', hitter_fantasy_points: 'Fantasy Pts (Hit)',
  // MLB — pitcher
  pitcher_strikeouts: 'Pitcher Ks', innings_pitched: 'Innings Pitched',
  hits_allowed: 'Hits Allowed', earned_runs: 'Earned Runs',
  walks_allowed: 'Walks Allowed', pitches_thrown: 'Pitches Thrown',
  batters_faced: 'Batters Faced', pitcher_fantasy_score: 'Fantasy Pts (Pitch)',
  pitching_outs: 'Pitching Outs',
  // CS2
  maps_1_3_kills: 'Maps 1-3 Kills', maps_1_3_headshots: 'Maps 1-3 Headshots',
  // WTA
  games: 'Games', sets: 'Sets', aces: 'Aces', double_faults: 'Double Faults',
  service_games_won: 'Service Games Won', total_games: 'Total Games',
};

const BAND_ACCENT: Record<string, string> = {
  aligned:  '#39FF14',
  aligned_warn: '#39FF14',
  mild:     '#B5FF14',
  moderate: '#FFCC00',
  elevated: '#FF8C00',
  extreme:  '#FF3B30',
};
const BAND_LABEL: Record<string, string> = {
  aligned:  'ALIGNED',
  aligned_warn: 'ALIGNED',
  mild:     'MILD',
  moderate: 'MODERATE',
  elevated: 'ELEVATED',
  extreme:  'EXTREME',
};

type Mode = 'scan' | 'manual';
type Phase = 'idle' | 'scanning' | 'detected' | 'analyzing' | 'result' | 'saved';
type Sport = 'soccer' | 'cs2' | 'wta' | 'nba' | 'nhl' | 'mlb' | 'nfl';

export default function ScanScreen() {
  const insets = useSafeAreaInsets();
  const { session, logout, accessType, loginWithResponse } = useAuth();
  const { isSubscribed: hasNativeAppleEntitlement } = useSubscription();
  // Paywall gating — all platforms (web Stripe + native RevenueCat) enforce subscription
  const isNoSub = (!accessType || accessType === 'NoSubscription')
    && !(Platform.OS === 'ios' && hasNativeAppleEntitlement);
  const accessRefreshRef = useRef<string | null>(null);
  const qc = useQueryClient();
  // AbortController ref so reset() can cancel an in-flight prediction
  const cancelAbortRef = useRef<AbortController | null>(null);
  const [mode, setMode] = useState<Mode>('scan');
  const [phase, setPhase] = useState<Phase>('idle');
  const [sport, setSport] = useState<Sport>('soccer');

  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scannedImageUri, setScannedImageUri] = useState<string | null>(null);
  // The result view is gated by phase. Keep this render value concrete so
  // TypeScript preserves narrowing inside the inline sport-specific renderers.
  // Provider responses are normalized before this screen receives them.
  const [predictionState, setPrediction] = useState<PredictionResult | null>(null);
  // The result screen is rendered only behind the phase/prediction guard. The
  // alias keeps older inline sport renderers from losing that narrowing inside
  // their nested callbacks.
  const prediction = predictionState as RenderPredictionValue<PredictionResult>;
  const [predictionRequest, setPredictionRequest] = useState<Record<string, unknown> | null>(null);
  const [tacticalAnalysis, setTacticalAnalysis] = useState<string | null>(null);
  const [showAltPlayers, setShowAltPlayers] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [manualError, setManualError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [pickSaved, setPickSaved] = useState(false);

  // Scan-fill hint shown at top of manual form after a non-soccer scan
  const [scanFillHint, setScanFillHint] = useState<string | null>(null);

  // User-controlled venue override — always resolves to home/away. "Neutral" isn't
  // real: even at a neutral tournament site, one team effectively plays like the
  // home side (bigger crowd support) and the other like the away side.
  const [venueOverride, setVenueOverride] = useState<'home' | 'away'>('home');
  const [gameLogFilter, setGameLogFilter] = useState<'all' | 'home' | 'away' | 'opp' | 'h2h'>('all');
  const [adjustedLine, setAdjustedLine] = useState<number | null>(null);
  const [deselectedLogIndices, setDeselectedLogIndices] = useState<Set<number>>(new Set());

  // Manual team override — user can tap the team badge to change it
  const [showTeamEdit, setShowTeamEdit] = useState(false);
  const [teamEditValue, setTeamEditValue] = useState('');

  // Opponent edit with fuzzy search
  const [showOppEdit, setShowOppEdit] = useState(false);
  const [oppEditValue, setOppEditValue] = useState('');

  // Player name edit (scan mode)
  const [showPlayerEdit, setShowPlayerEdit] = useState(false);
  const [playerEditValue, setPlayerEditValue] = useState('');
  const [resolvedScanPlayer, setResolvedScanPlayer] = useState<FuzzyPlayerResult | null>(null);

  // Prop type edit (scan mode)
  const [showPropEditScan, setShowPropEditScan] = useState(false);

  // Line edit (scan mode)
  const [showLineEdit, setShowLineEdit] = useState(false);
  const [lineEditValue, setLineEditValue] = useState('');

  // League edit (scan mode)
  const [showLeagueEditScan, setShowLeagueEditScan] = useState(false);

  // Manual mode fields
  const [playerQuery, setPlayerQuery] = useState('');
  const [resolvedPlayer, setResolvedPlayer] = useState<FuzzyPlayerResult | null>(null);
  const [resolvedRole, setResolvedRole] = useState<PlayerRoleResult | null>(null);
  const [roleLoading, setRoleLoading] = useState(false);
  const [manualOpponentQuery, setManualOpponentQuery] = useState('');
  const [resolvedManualOpponent, setResolvedManualOpponent] = useState<FuzzyTeamResult | null>(null);

  // Team context picker — shown when a player has both club and national team entries
  const [playerContexts, setPlayerContexts] = useState<PlayerContext[]>([]);
  const [selectedContext, setSelectedContext] = useState<PlayerContext | null>(null);
  const [contextsLoading, setContextsLoading] = useState(false);
  const [clubVerificationStatus, setClubVerificationStatus] = useState<'idle' | 'loading' | 'verified' | 'last_known' | 'unavailable'>('idle');
  const [nextMatchLoading, setNextMatchLoading] = useState(false);
  const [autoMatch, setAutoMatch] = useState<NextMatchData | null>(null);
  const [propType, setPropType] = useState(PROP_TYPES[0].value);
  const [line, setLine] = useState('');
  const [leagueId, setLeagueId] = useState(0);
  const [leagueQuery, setLeagueQuery] = useState('');
  const [showPropPicker, setShowPropPicker] = useState(false);
  const [showLeaguePicker, setShowLeaguePicker] = useState(false);

  // Older/reinstalled iOS sessions can have an active StoreKit entitlement
  // while the backend session still says NoSubscription. Refresh the
  // authenticated account immediately before Analyze so the customer does
  // not have to log out, reinstall, or guess which restore flow to use.
  const refreshAppleAccess = async (): Promise<boolean> => {
    if (!session?.email || !session.token || Platform.OS !== 'ios') return !isNoSub;
    const sessionKey = `${session.email}:${session.token}`;
    if (accessRefreshRef.current === sessionKey) return !isNoSub;
    try {
      const info = await Purchases.getCustomerInfo();
      const entitlement = info?.entitlements?.active?.[REVENUECAT_ENTITLEMENT_IDENTIFIER];
      if (!entitlement) return !isNoSub;
      const customerId = await Purchases.getAppUserID();
      const synced = await syncAppleAccess(session.email, session.token, customerId);
      if (!synced.access_type) return false;
      await loginWithResponse({
        email: session.email,
        session_token: session.token,
        access_type: synced.access_type,
      });
      accessRefreshRef.current = sessionKey;
      return true;
    } catch (error) {
      console.warn('[Subscription] Analyze-time Apple sync skipped:', error);
      return false;
    }
  };

  const ensurePredictionAccess = async (): Promise<boolean> => {
    if (Platform.OS === 'ios') return refreshAppleAccess();
    if (!isNoSub) return true;
    // Web/Stripe sessions can also be stale after a checkout or webhook.
    // verifySession uses the same server-side access check as /api/predict.
    if (!session?.email || !session.token) return false;
    try {
      const refreshed = await verifySession(session.email, session.token);
      if (refreshed?.valid && refreshed.access_type && refreshed.access_type !== 'NoSubscription') {
        await loginWithResponse({
          email: session.email,
          session_token: session.token,
          access_type: refreshed.access_type,
        });
        return true;
      }
    } catch (error) {
      console.warn('[Subscription] Analyze-time session refresh skipped:', error);
    }
    return false;
  };

  // CS2 manual mode fields
  const [cs2PlayerQuery, setCs2PlayerQuery] = useState('');
  const [cs2ResolvedPlayer, setCs2ResolvedPlayer] = useState<Cs2Player | null>(null);
  const [cs2OpponentQuery, setCs2OpponentQuery] = useState('');
  const [cs2ResolvedOpponent, setCs2ResolvedOpponent] = useState<Cs2Team | null>(null);
  const [cs2PropType, setCs2PropType] = useState('maps_1_2_kills');
  const [cs2ShowPropPicker, setCs2ShowPropPicker] = useState(false);
  const [cs2MapName, setCs2MapName] = useState('');
  const [cs2TeamStartsCt, setCs2TeamStartsCt] = useState<boolean | null>(null);

  // WTA manual mode fields
  const [wtaPlayerQuery, setWtaPlayerQuery] = useState('');
  const [wtaResolvedPlayer, setWtaResolvedPlayer] = useState<WtaPlayer | null>(null);
  const [wtaOpponentQuery, setWtaOpponentQuery] = useState('');
  const [wtaResolvedOpponent, setWtaResolvedOpponent] = useState<WtaPlayer | null>(null);
  const [wtaPropType, setWtaPropType] = useState('total_games');
  const [wtaShowPropPicker, setWtaShowPropPicker] = useState(false);
  const [wtaSurface, setWtaSurface] = useState<string>('Hard');
  const [wtaShowSurfacePicker, setWtaShowSurfacePicker] = useState(false);
  const [wtaRound, setWtaRound] = useState<string>('R32');
  const [wtaShowRoundPicker, setWtaShowRoundPicker] = useState(false);

  // CS2 / WTA next-match auto-fill
  const [cs2NextMatch, setCs2NextMatch] = useState<Cs2NextMatch | null>(null);
  const [cs2NextMatchLoading, setCs2NextMatchLoading] = useState(false);
  const [wtaNextMatch, setWtaNextMatch] = useState<WtaNextMatch | null>(null);
  const [wtaNextMatchLoading, setWtaNextMatchLoading] = useState(false);

  // NBA manual mode fields
  const [nbaPlayerQuery, setNbaPlayerQuery] = useState('');
  const [nbaResolvedPlayer, setNbaResolvedPlayer] = useState<NbaPlayer | null>(null);
  const [nbaOpponentQuery, setNbaOpponentQuery] = useState('');
  const [nbaPropType, setNbaPropType] = useState('pts');
  const [nbaShowPropPicker, setNbaShowPropPicker] = useState(false);
  const [nbaVenue, setNbaVenue] = useState<'home' | 'away'>('home');
  const [nbaNextMatch, setNbaNextMatch] = useState<NbaNextMatch | null>(null);
  const [nbaNextMatchLoading, setNbaNextMatchLoading] = useState(false);

  // NHL manual mode fields
  const [nhlPlayerQuery, setNhlPlayerQuery] = useState('');
  const [nhlResolvedPlayer, setNhlResolvedPlayer] = useState<NhlPlayer | null>(null);
  const [nhlOpponentQuery, setNhlOpponentQuery] = useState('');
  const [nhlPropType, setNhlPropType] = useState('goals');
  const [nhlShowPropPicker, setNhlShowPropPicker] = useState(false);
  const [nhlVenue, setNhlVenue] = useState<'home' | 'away'>('home');
  const [nhlNextMatch, setNhlNextMatch] = useState<NhlNextMatch | null>(null);
  const [nhlNextMatchLoading, setNhlNextMatchLoading] = useState(false);

  // NFL manual mode fields
  const [nflPlayerQuery, setNflPlayerQuery] = useState('');
  const [nflPendingPlayer, setNflPendingPlayer] = useState<NflPlayer | null>(null);
  const [nflResolvedPlayer, setNflResolvedPlayer] = useState<NflPlayer | null>(null);
  const [nflOpponentQuery, setNflOpponentQuery] = useState('');
  const [nflPropType, setNflPropType] = useState('passing_yards');
  const [nflShowPropPicker, setNflShowPropPicker] = useState(false);
  const [nflVenue, setNflVenue] = useState<'home' | 'away'>('home');
  const [nflNextMatch, setNflNextMatch] = useState<NflNextMatch | null>(null);
  const [nflNextMatchLoading, setNflNextMatchLoading] = useState(false);

  // MLB manual mode fields
  const [mlbPlayerQuery, setMlbPlayerQuery] = useState('');
  const [mlbPendingPlayer, setMlbPendingPlayer] = useState<MlbPlayer | null>(null);
  const [mlbResolvedPlayer, setMlbResolvedPlayer] = useState<MlbPlayer | null>(null);
  const [mlbOpponentQuery, setMlbOpponentQuery] = useState('');
  const [mlbPropType, setMlbPropType] = useState('hits');
  const [mlbShowPropPicker, setMlbShowPropPicker] = useState(false);
  const [mlbVenue, setMlbVenue] = useState<'home' | 'away'>('home');
  const [mlbNextMatch, setMlbNextMatch] = useState<MlbNextMatch | null>(null);
  const [mlbNextMatchLoading, setMlbNextMatchLoading] = useState(false);

  const selectSport = (next: Sport) => {
    cancelAbortRef.current?.abort();
    setSport(next);
    setMode('manual');
    setPhase('idle');
    setPrediction(null as any);
    setPredictionRequest(null);
    setScanResult(null);
    setTacticalAnalysis(null);
    setAnalyzeError(null);
    setManualError(null);
    setScanFillHint(null);
    setPickSaved(false);
  };

  const handleSoccerPlayerSelect = async (p: FuzzyPlayerResult | UniversalPlayerResult) => {
    setPlayerQuery(p.playerName);
    setResolvedPlayer({
      playerId: p.playerId,
      playerName: p.playerName,
      teamId: p.teamId,
      teamName: p.teamName || '',
      leagueId: p.leagueId,
      position: p.position,
    });
    setResolvedRole(null);
    setSelectedContext(null);
    setAutoMatch(null);
    setPlayerContexts([]);
    setLeagueId(0);
    setLeagueQuery('');
    setClubVerificationStatus('loading');
    Haptics.selectionAsync();
    setRoleLoading(true);
    resolvePlayerRole(
      p.playerId || null,
      p.playerName,
      p.teamName,
      p.position || '',
    ).then((result) => {
      if (result.position || result.role) setResolvedRole(result);
    }).catch(() => {}).finally(() => setRoleLoading(false));
    if (p.playerId) {
      setContextsLoading(true);
      try {
        const res = await getPlayerContexts(p.playerId);
        const ctxs = res?.contexts || [];
        setPlayerContexts(ctxs);
        const verifiedClub = res?.teamVerified === true
          ? ctxs.find(c => !c.isNational && c.verified !== false)
          : null;
        if (verifiedClub) {
          setClubVerificationStatus('verified');
          setResolvedPlayer(current => current ? {
            ...current,
            teamId: verifiedClub.teamId,
            teamName: verifiedClub.teamName,
            leagueId: verifiedClub.leagueId,
          } : current);
          const preferredContext = verifiedClub;
          setSelectedContext(preferredContext);
          setNextMatchLoading(true);
          try {
            const nm = await getTeamNextMatch(preferredContext.teamId);
            if (nm?.found) {
              setAutoMatch(nm);
              setVenueOverride(nm.isHome ? 'home' : 'away');
            }
            if (nm?.leagueId) {
              setLeagueId(nm.leagueId); setLeagueQuery(nm.leagueName || '');
            } else {
              const fallbackId = preferredContext.leagueId || 0;
              if (fallbackId && fallbackId !== 667) {
                const lgInfo = await getLeagueById(fallbackId);
                setLeagueId(fallbackId);
                setLeagueQuery(lgInfo?.name || '');
              }
            }
          } catch {}
          setNextMatchLoading(false);
        } else {
          // A previous-season club is useful context, but it is not current
          // club evidence and must never drive an automatic matchup.
          const status = res?.verificationStatus === 'last_known' ? 'last_known' : 'unavailable';
          setClubVerificationStatus(status);
          setResolvedPlayer(current => current ? {
            ...current,
            teamId: 0,
            teamName: '',
            leagueId: 0,
          } : current);
          setSelectedContext(null);
          setAutoMatch(null);
          setManualError(
            status === 'last_known'
              ? `Last known club: ${res?.lastKnownClub?.teamName || 'previous club'}. Current club is not confirmed, so no old-team matchup was filled.`
              : 'Current club could not be verified right now. No old-team matchup was filled.',
          );
        }
      } catch {
        // Context enrichment may fail independently of the verified search
        // result (for example, Atlas can be write-blocked). Preserve the
        // provider-backed club selected by the user instead of converting a
        // valid player into manual setup.
        if (p.teamId && p.teamName) {
          const fallbackContext = {
            teamId: p.teamId,
            teamName: p.teamName,
            leagueId: p.leagueId || 0,
            isNational: false,
            verified: true,
          };
          setClubVerificationStatus('verified');
          setResolvedPlayer(current => current ? {
            ...current,
            teamId: p.teamId,
            teamName: p.teamName,
            leagueId: p.leagueId || current.leagueId,
          } : current);
          setSelectedContext(fallbackContext);
          setNextMatchLoading(true);
          try {
            const nm = await getTeamNextMatch(p.teamId);
            if (nm?.found) {
              setAutoMatch(nm);
              setVenueOverride(nm.isHome ? 'home' : 'away');
            }
            if (nm?.leagueId) {
              setLeagueId(nm.leagueId);
              setLeagueQuery(nm.leagueName || '');
            }
          } catch {}
          setNextMatchLoading(false);
          setManualError(null);
        } else {
          setClubVerificationStatus('unavailable');
          setResolvedPlayer(current => current ? {
            ...current,
            teamId: 0,
            teamName: '',
            leagueId: 0,
          } : current);
          setSelectedContext(null);
          setAutoMatch(null);
          setManualError('Current club could not be verified right now. No old-team matchup was filled.');
        }
      }
      setContextsLoading(false);
    } else {
      setClubVerificationStatus('unavailable');
      setManualError('This player has no verifiable current club. No old-team matchup was filled.');
    }
  };

  const handleUniversalPlayerSelect = async (p: UniversalPlayerResult) => {
    setManualError(null);
    selectSport(p.sport);
    if (p.sport === 'soccer') {
      await handleSoccerPlayerSelect(p);
      return;
    }
    if (p.sport === 'mlb') {
      const player = p.raw as MlbPlayer;
      setMlbPlayerQuery(p.playerName);
      // Universal search results are already provider-verified. Commit the
      // player immediately instead of stopping at the old second confirmation
      // step, which made a tap appear to do nothing and never fetched the game.
      await confirmMlbPlayer(player);
      return;
    }
    const player = p.raw as NflPlayer;
    setNflPlayerQuery(p.playerName);
    // Same immediate commit for NFL so the existing next-match lookup starts
    // from the universal result tap.
    await confirmNflPlayer(player);
  };

  // Auto-quality-filter whenever a new prediction loads:
  // sub-60-min games are excluded automatically so the hit rate is clean by default.
  // User can still tap any grey tile to restore it.
  useEffect(() => {
    if (!prediction?.gameLogs) {
      setDeselectedLogIndices(new Set());
      return;
    }
    const realLogs = prediction.gameLogs.filter(g => !g.synthetic);
    const toDeselect = new Set<number>();
    realLogs.forEach((g, idx) => {
      if ((g.minutes || 0) > 0 && (g.minutes || 0) < 60) toDeselect.add(idx);
    });
    setDeselectedLogIndices(toDeselect);
  }, [prediction?.playerId]);


  // The web subscription banner is rendered above the navigator in normal
  // document flow. Adding another fixed web inset here creates a large empty
  // black band between the banner and the scan header on mobile Safari.
  const topPad = Platform.OS === 'web' ? 0 : insets.top;
  const analysisRef = useRef<any>(null);
  const [savingImage, setSavingImage] = useState(false);

  const handleSaveImage = async () => {
    if (!prediction) return;
    setSavingImage(true);
    try {
      if (Platform.OS === 'web') {
        const html2canvas = (await import('html2canvas')).default;
        const node = analysisRef.current;
        if (!node) { setSavingImage(false); return; }
        const domNode = (node as any)?.getNativeScrollRef?.() ?? node;
        const canvas = await html2canvas(domNode, {
          backgroundColor: '#000000',
          scale: 2,
          useCORS: true,
          allowTaint: true,
          scrollY: -window.scrollY,
          windowWidth: document.documentElement.scrollWidth,
          windowHeight: document.documentElement.scrollHeight,
          height: domNode.scrollHeight,
        });
        const dataUrl = canvas.toDataURL('image/png');
        // iOS Safari ignores link.download — open in new tab so user can long-press save
        const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
        if (isIOS) {
          const win = window.open('', '_blank');
          if (win) {
            win.document.write(`<html><body style="margin:0;background:#000"><img src="${dataUrl}" style="max-width:100%"/></body></html>`);
            win.document.close();
          }
        } else {
          const link = document.createElement('a');
          link.download = `${prediction.playerName || 'pick'}-analysis.png`;
          link.href = dataUrl;
          link.click();
        }
      }
    } catch (e) {
      console.warn('Save image error:', e);
      Alert.alert('Save failed', 'Could not capture the analysis. Try scrolling to the top and trying again.');
    }
    setSavingImage(false);
  };

  const reset = () => {
    cancelAbortRef.current?.abort();
    cancelAbortRef.current = null;
    setMode('scan');
    setPhase('idle');
    setSport('soccer');
    setScanResult(null);
    setScannedImageUri(null);
    setPrediction(null as any);
    setPredictionRequest(null);
    setTacticalAnalysis(null);
    setAnalyzeError(null);
    setManualError(null);
    setSaveError(null);
    setSaving(false);
    setSavingImage(false);
    setScanFillHint(null);
    setPickSaved(false);
    setShowAltPlayers(false);
    setPlayerQuery('');
    setResolvedPlayer(null);
    setResolvedRole(null);
    setRoleLoading(false);
    setManualOpponentQuery('');
    setResolvedManualOpponent(null);
    setLine('');
    setVenueOverride('home');
    setPlayerContexts([]);
    setSelectedContext(null);
    setAutoMatch(null);
    setGameLogFilter('all');
    setAdjustedLine(null);
    setShowPlayerEdit(false);
    setShowTeamEdit(false);
    setShowOppEdit(false);
    setShowPropEditScan(false);
    setShowLineEdit(false);
    setShowLeagueEditScan(false);
    setResolvedScanPlayer(null);
    setCs2PlayerQuery('');
    setCs2ResolvedPlayer(null);
    setCs2OpponentQuery('');
    setCs2ResolvedOpponent(null);
    setCs2MapName('');
    setCs2TeamStartsCt(null);
    setCs2NextMatch(null);
    setCs2NextMatchLoading(false);
    setWtaPlayerQuery('');
    setWtaResolvedPlayer(null);
    setWtaOpponentQuery('');
    setWtaResolvedOpponent(null);
    setWtaPropType('total_games');
    setWtaSurface('Hard');
    setWtaRound('R32');
    setWtaNextMatch(null);
    setWtaNextMatchLoading(false);
    setNbaPlayerQuery('');
    setNbaResolvedPlayer(null);
    setNbaOpponentQuery('');
    setNbaPropType('pts');
    setNbaVenue('home');
    setNbaNextMatch(null);
    setNbaNextMatchLoading(false);
    setNhlPlayerQuery('');
    setNhlResolvedPlayer(null);
    setNhlOpponentQuery('');
    setNhlPropType('goals');
    setNhlVenue('home');
    setNhlNextMatch(null);
    setNhlNextMatchLoading(false);
    setMlbPlayerQuery('');
    setNflPendingPlayer(null);
    setNflPlayerQuery('');
    setNflResolvedPlayer(null);
    setNflOpponentQuery('');
    setNflNextMatch(null);
    setNflNextMatchLoading(false);
    setMlbPendingPlayer(null);
    setMlbResolvedPlayer(null);
    setMlbOpponentQuery('');
    setMlbPropType('hits');
    setMlbVenue('home');
    setMlbNextMatch(null);
    setMlbNextMatchLoading(false);
  };

  const processImage = async (base64: string, uri: string) => {
    setScannedImageUri(uri);
    setPhase('scanning');
    setAnalyzeError(null);
    setScanFillHint(null);
    try {
      const scanned = await scanProp(base64, sport);

      // Shared prop-type fuzzy mapper — used for all sports
      const mapProp = (raw: string | undefined, validKeys: string[], defaultKey: string): string => {
        if (!raw) return defaultKey;
        const r = raw.toLowerCase().replace(/[^a-z0-9_]/g, '_');
        const exact = validKeys.find(k => k === r);
        if (exact) return exact;
        const partial = validKeys.find(k => r.includes(k) || k.includes(r.split('_')[0]));
        return partial || defaultKey;
      };

      // ── Non-soccer: fill sport-specific manual form, never show soccer detected card ──
      if (sport !== 'soccer') {
        if (sport === 'cs2') {
          const cs2Keys = CS2_PROP_TYPES.map((p: { value: string }) => p.value);
          if (scanned.playerName) setCs2PlayerQuery(scanned.playerName);
          if (scanned.opponentName) setCs2OpponentQuery(scanned.opponentName);
          setCs2PropType(mapProp(scanned.propType, cs2Keys, 'maps_1_2_kills'));
          if (scanned.line) setLine(String(scanned.line));
          // Auto-resolve player and opponent so IDs are populated
          if (scanned.playerName) {
            searchCs2Players(scanned.playerName).then(results => {
              if (results && results.length > 0) {
                setCs2ResolvedPlayer(results[0]);
                setCs2PlayerQuery(results[0].nickname || scanned.playerName!);
              }
            }).catch(() => {});
          }
          if (scanned.opponentName) {
            searchCs2Teams(scanned.opponentName).then(results => {
              if (results && results.length > 0) {
                setCs2ResolvedOpponent(results[0]);
                setCs2OpponentQuery(results[0].name || scanned.opponentName!);
              }
            }).catch(() => {});
          }
        } else if (sport === 'wta') {
          const wtaKeys = WTA_PROP_TYPES.map((p: { value: string }) => p.value);
          if (scanned.playerName) setWtaPlayerQuery(scanned.playerName);
          if (scanned.opponentName) setWtaOpponentQuery(scanned.opponentName);
          setWtaPropType(mapProp(scanned.propType, wtaKeys, 'total_games'));
          if (scanned.line) setLine(String(scanned.line));
          // Auto-resolve player and opponent
          if (scanned.playerName) {
            searchWtaPlayers(scanned.playerName).then(results => {
              if (results && results.length > 0) {
                setWtaResolvedPlayer(results[0]);
                setWtaPlayerQuery(results[0].fullName || scanned.playerName!);
              }
            }).catch(() => {});
          }
          if (scanned.opponentName) {
            searchWtaPlayers(scanned.opponentName).then(results => {
              if (results && results.length > 0) {
                setWtaResolvedOpponent(results[0]);
                setWtaOpponentQuery(results[0].fullName || scanned.opponentName!);
              }
            }).catch(() => {});
          }
        } else if (sport === 'mlb') {
          const mlbKeys = MLB_PROP_TYPES.map((p: { value: string }) => p.value);
          if (scanned.playerName) setMlbPlayerQuery(scanned.playerName);
          if (scanned.opponentName) setMlbOpponentQuery(scanned.opponentName);
          setMlbPropType(mapProp(scanned.propType, mlbKeys, 'hits'));
          if (scanned.line) setLine(String(scanned.line));
          if (scanned.playerName) {
            searchMlbPlayers(scanned.playerName).then(results => {
              if (results?.length) {
                setMlbResolvedPlayer(results[0]);
                setMlbPlayerQuery(results[0].fullName || scanned.playerName!);
              }
            }).catch(() => {});
          }
        } else if (sport === 'nfl') {
          const nflKeys = NFL_PROP_TYPES.map((p: { value: string }) => p.value);
          if (scanned.playerName) setNflPlayerQuery(scanned.playerName);
          if (scanned.opponentName) setNflOpponentQuery(scanned.opponentName);
          setNflPropType(mapProp(scanned.propType, nflKeys, 'passing_yards'));
          if (scanned.line) setLine(String(scanned.line));
          if (scanned.playerName) {
            searchNflPlayers(scanned.playerName).then(results => {
              if (results?.length) {
                setNflResolvedPlayer(results[0]);
                setNflPlayerQuery(results[0].fullName || scanned.playerName!);
              }
            }).catch(() => {});
          }
        }

        const filled = scanned.playerName
          ? `✓ Scanned "${scanned.playerName}" — review and adjust below`
          : scanned.error
          ? `⚠ Partial scan — fill in missing fields`
          : `✓ Scanned — review and adjust below`;
        setScanFillHint(filled);
        setMode('manual');
        setPhase('idle');
        Haptics.notificationAsync(
          scanned.playerName
            ? Haptics.NotificationFeedbackType.Success
            : Haptics.NotificationFeedbackType.Warning
        );
        return;
      }

      // ── Soccer: fill manual form, same as other sports ──
      if (scanned.error && !scanned.playerName && !scanned.propType && !scanned.line && !scanned.opponentName) {
        setAnalyzeError(scanned.error || 'Could not read prop slip. Try a clearer screenshot.');
        setPhase('idle');
        return;
      }
      const soccerKeys = PROP_TYPES.map((p: { value: string }) => p.value);
      if (scanned.playerName) setPlayerQuery(scanned.playerName);
      if (scanned.opponentName) setManualOpponentQuery(scanned.opponentName);
      if (scanned.leagueId) { setLeagueId(scanned.leagueId); setLeagueQuery(scanned.leagueName || ''); }
      const detectedVenue = (scanned.venue || 'home').toLowerCase();
      setVenueOverride(detectedVenue === 'away' ? 'away' : 'home');
      setPropType(mapProp(scanned.propType, soccerKeys, PROP_TYPES[0].value));
      if (scanned.line) setLine(String(scanned.line));

      const filledMsg = scanned.playerName
        ? `✓ Scanned "${scanned.playerName}" — review and adjust below`
        : `⚠ Partial scan — fill in missing fields`;
      setScanFillHint(filledMsg);
      setMode('manual');
      setPhase('idle');
      Haptics.notificationAsync(
        scanned.playerName
          ? Haptics.NotificationFeedbackType.Success
          : Haptics.NotificationFeedbackType.Warning
      );
    } catch (e: unknown) {
      setAnalyzeError(e instanceof Error ? e.message : 'Failed to scan image');
      setPhase('idle');
    }
  };

  const handleGallery = async () => {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Permission needed', 'Allow photo library access to scan prop slips.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.85,
      base64: true,
    });
    if (result.canceled || !result.assets[0].base64) return;
    await processImage(result.assets[0].base64, result.assets[0].uri);
  };

  const fetchTacticalChat = async (
    pred: PredictionResult,
    signal?: AbortSignal,
  ): Promise<string> => {
    try {
      const propLabel = (pred.propType || '').replace(/_/g, ' ');
      const proj = pred.bayesianProjection ?? pred.projection ?? pred.priorMean;
      const pO = pred.pOver != null ? Math.round(pred.pOver) : null;
      const pU = pred.pUnder != null ? Math.round(pred.pUnder) : null;
      const rec = pred.recommendation ?? 'UNKNOWN';
      const conf = pred.confidenceScore ?? pred.confidence;
      const confLvl = pred.confidenceLevel ?? '';
      const logs = (pred.gameLogs ?? []).slice(0, 10);

      // Build a compact game-log summary: "M1:102 M2:88 M3:71 …"
      const logSummary = logs.length
        ? logs.map((g, i) => {
            const val = g.value ?? null;
            return `M${i + 1}:${val != null ? Number(val).toFixed(1) : '?'}`;
          }).join(' ')
        : 'no logs';

      // Bayesian mechanics summary
      const bm = pred.bayesianMetrics as Record<string, unknown> | undefined;
      const priorMean = pred.priorMean ?? bm?.prior_mean;
      const momEffect = pred.momentumEffect ?? bm?.momentumEffect;
      const covAdj = pred.covariateAdjustment ?? bm?.covariateAdjustment;
      const momLabel = pred.momentumLabel ?? bm?.momentumLabel ?? '';

      const bayesianSummary = [
        priorMean != null ? `prior_mean=${Number(priorMean).toFixed(1)}` : null,
        momEffect != null ? `momentum=${Number(momEffect) > 0 ? '+' : ''}${Number(momEffect).toFixed(1)} (${momLabel})` : null,
        covAdj != null ? `covariate_adj=${Number(covAdj) > 0 ? '+' : ''}${Number(covAdj).toFixed(1)}` : null,
      ].filter(Boolean).join(', ');

      // Hit-rate context
      const hr = pred.hitRates;
      const hrSummary = hr
        ? `historical hit rates: OVER ${hr.overPct}% (${hr.overHits}/${hr.total}), UNDER ${(100 - hr.overPct).toFixed(0)}%`
        : '';

      // Possession + game script
      const expPoss = pred.expectedPossession;
      const possSummary = expPoss
        ? `expected possession: home ${expPoss.home}% / away ${expPoss.away}%`
        : '';
      const keyFinding = pred.gameScript?.key_finding ?? '';

      // H2H vs this opponent
      const h2hAvg = pred.h2hPlayerStats?.avgVsOpponent;
      const h2hSummary = h2hAvg != null
        ? `H2H vs ${pred.opponentName ?? pred.opponent}: avg ${Number(h2hAvg).toFixed(1)}`
        : '';

      // Venue averages
      const venueAvg = pred.analysisSummary?.venueAverage;
      const oppAllowed = pred.analysisSummary?.opponentAllowedAverage;
      const venueSummary = [
        venueAvg != null ? `venue avg ${Number(venueAvg).toFixed(1)}` : null,
        oppAllowed != null ? `matching opponent sample avg ${Number(oppAllowed).toFixed(1)}` : null,
      ].filter(Boolean).join(', ');

      const prompt =
        `THE BAYESIAN ENGINE HAS ALREADY RUN. DO NOT OVERRIDE ITS VERDICT.\n\n` +
        `Player: ${pred.playerName ?? ''} | Role: ${pred.playerRole ?? pred.playerPosition ?? 'unknown'}\n` +
        `Prop: ${propLabel} | Line: ${pred.line} | Opponent: ${pred.opponentName ?? pred.opponent ?? ''}\n\n` +
        `ENGINE OUTPUT (accept these numbers as ground truth):\n` +
        `• Projection: ${proj != null ? Number(proj).toFixed(1) : '?'} (${rec} ${pred.line})\n` +
        `• P(OVER)=${pO ?? '?'}% / P(UNDER)=${pU ?? '?'}% | Confidence: ${conf ?? '?'}% ${confLvl}\n` +
        `• Bayesian mechanics: ${bayesianSummary || 'n/a'}\n` +
        `• Last ${logs.length} game logs: ${logSummary}\n` +
        (hrSummary ? `• ${hrSummary}\n` : '') +
        (venueSummary ? `• ${venueSummary}\n` : '') +
        (h2hSummary ? `• ${h2hSummary}\n` : '') +
        (possSummary ? `• ${possSummary}\n` : '') +
        (keyFinding ? `• Game script: ${keyFinding}\n` : '') +
        (() => {
          const cp = (pred.bayesianMetrics as Record<string, unknown> | undefined)?.condPossAdj as Record<string, unknown> | null | undefined;
          if (!cp) return '';
          const delta = cp.deltaPP as number;
          const base  = cp.basePoss as number;
          const adj   = cp.adjustedPoss as number;
          const oppCede = cp.oppCede as number;
          const pTrail  = cp.pTrail as number;
          return `• POSSESSION ADJUSTMENT (key context): The engine raised expected possession ` +
            `from ${base?.toFixed(0)}% → ${adj?.toFixed(1)}% (${delta > 0 ? '+' : ''}${delta?.toFixed(1)}pp). ` +
            `Reason: opponent ${pred.opponentName ?? ''} cede-score=${oppCede?.toFixed(2)} ` +
            `(${oppCede >= 0.6 ? 'strongly cedes possession when leading' : oppCede >= 0.35 ? 'moderately cedes possession when leading' : 'maintains possession when leading'}), ` +
            `P(player team trails)=${(pTrail * 100)?.toFixed(0)}%. ` +
            `${cp.oppStyleNotes ? `Opponent style: ${cp.oppStyleNotes}. ` : ''}` +
            `Your analysis MUST reference this possession shift explicitly.\n`;
        })() +
        `\n` +
        `YOUR TASK — apply the CORE REASONING FRAMEWORK to explain and validate these numbers:\n` +
        `1) ROLE ANALYSIS: What is this player's exact tactical role and how does it mechanically produce this stat? ` +
        `Quote their per-90 average across competitions and explain whether the projection aligns with it.\n` +
        `2) MATCHUP INTELLIGENCE: How does ${pred.opponentName ?? pred.opponent ?? 'the opponent'}'s pressing intensity (PPDA), ` +
        `defensive shape, and formation affect this specific stat for this role?\n` +
        `3) SUBSTITUTION RISK: What is this player's typical minutes pattern? In which game states are they subbed early?\n` +
        `4) GAME FLOW: Walk through the base/trailing/leading/cagey scenarios and how each shifts this stat ` +
        `relative to the projection of ${proj != null ? Number(proj).toFixed(1) : '?'}.\n` +
        `5) VERDICT: Confirm or challenge the engine's ${rec} call. Quote at least one specific number that ` +
        `supports it and state ONE thing that would flip the call.\n\n` +
        `Be precise. Reference the numbers above. Do not hedge. Do not re-state the question.`;

      const chatStart = await startChat();
      if (signal?.aborted) return '';
      const resp = await sendChatMessage(chatStart.session_id, prompt);
      return resp.response || '';
    } catch {
      return '';
    }
  };

  const runPredict = async (data: ScanResult, inManual = false) => {
    if (!session?.email || !session?.token) {
      Alert.alert('Sign In Required', 'Please sign in to run predictions.');
      return;
    }
    if (!(await ensurePredictionAccess())) {
      if (Platform.OS === 'web') { router.push('/(tabs)/account'); } else { router.push('/paywall'); }
      return;
    }
    setPhase('analyzing');
    setAnalyzeError(null);
    setManualError(null);
    setTacticalAnalysis(null);
    cancelAbortRef.current?.abort();
    cancelAbortRef.current = new AbortController();
    const sig = cancelAbortRef.current.signal;
    try {
      const autoVenue = sport === 'soccer'
        && autoMatch?.found
        && typeof autoMatch.isHome === 'boolean'
        ? (autoMatch.isHome ? 'home' : 'away')
        : null;
      const req = {
        email: session.email,
        token: session.token,
        playerName: data.playerName,
        playerId: data.playerId || 0,
        teamId: data.teamId || 0,
        teamName: data.teamName || data.playerTeam || '',
        opponentId: data.opponentId || 0,
        opponentName: data.opponentName || '',
        // A verified next fixture is the single source of truth for soccer
        // venue. Do not let a stale/manual toggle override it.
        venue: autoVenue || venueOverride,
        leagueId: data.leagueId || leagueId,
        propType: data.propType || propType,
        line: data.line || 0,
        sport: sport,
      };

      // Return the mathematical prediction as soon as it is ready. Tactical
      // prose is optional enrichment and must never hold the result hostage to
      // a second AI request.
      const result = await predict(req, sig);

      if (result.error) {
        if (inManual) setManualError(result.error); else setAnalyzeError(result.error);
        setPhase(inManual ? 'idle' : 'detected');
        return;
      }
      setPrediction(result);
      setPredictionRequest(req);
      setTacticalAnalysis(result.tacticalBreakdown || result.reasoning || result.sharpSummary || null);
      setShowAltPlayers(false);
      setPhase('result');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      if (e instanceof Error && e.message === '__CANCELLED__') return;
      const msg = e instanceof Error ? e.message : 'Analysis failed — try again';
      if (inManual) setManualError(msg); else setAnalyzeError(msg);
      setPhase(inManual ? 'idle' : 'detected');
    } finally {
      cancelAbortRef.current = null;
    }
  };

  const handleManualAnalyze = async () => {
    if (!session?.email || !session?.token) {
      Alert.alert('Sign In Required', 'Please sign in to run predictions.');
      return;
    }
    if (!(await ensurePredictionAccess())) {
      if (Platform.OS === 'web') { router.push('/(tabs)/account'); } else { router.push('/paywall'); }
      return;
    }
    if (!playerQuery.trim()) { setManualError('Enter a player name to analyze.'); return; }
    const hasVerifiedClub = clubVerificationStatus === 'verified'
      && !!resolvedPlayer?.teamId
      && !!resolvedPlayer?.teamName;
    const hasExplicitNationalContext = !!selectedContext?.isNational
      && !!selectedContext.teamId
      && !!selectedContext.teamName;
    if (!hasVerifiedClub && !hasExplicitNationalContext) {
      setManualError(
        clubVerificationStatus === 'loading'
          ? 'Still verifying the player’s current club. Try again in a moment.'
          : clubVerificationStatus === 'last_known'
            ? 'Only a previous-season club is known. Select an explicit national-team context or retry; no old-team prediction was created.'
            : 'The player’s current club could not be verified. Select an explicit national-team context or retry; no old-team prediction was created.',
      );
      return;
    }
    if (!line.trim() || isNaN(parseFloat(line))) { setManualError('Enter a valid line value (e.g. 2.5).'); return; }
    setManualError(null);
    setMode('manual');
    const activeContext = selectedContext || (resolvedPlayer ? { teamId: resolvedPlayer.teamId, teamName: resolvedPlayer.teamName, leagueId: resolvedPlayer.leagueId, isNational: false } : null);
    const data: ScanResult = {
      playerName: playerQuery.trim(),
      propType,
      line: parseFloat(line),
      leagueId: autoMatch?.leagueId || activeContext?.leagueId || leagueId,
      playerId: resolvedPlayer?.playerId || 0,
      teamId: activeContext?.teamId || resolvedPlayer?.teamId || 0,
      teamName: activeContext?.teamName || resolvedPlayer?.teamName || '',
      opponentId: autoMatch?.opponent?.id || resolvedManualOpponent?.teamId || 0,
      opponentName: autoMatch?.opponent?.name || resolvedManualOpponent?.teamName || manualOpponentQuery.trim() || '',
    };
    setScanResult(data);
    await runPredict(data, true);
  };

  const handleCs2Analyze = async () => {
    if (!session?.email || !session?.token) {
      Alert.alert('Sign In Required', 'Please sign in to run predictions.');
      return;
    }
    if (!(await ensurePredictionAccess())) {
      if (Platform.OS === 'web') { router.push('/(tabs)/account'); } else { router.push('/paywall'); }
      return;
    }
    if (!cs2PlayerQuery.trim()) { setManualError('Enter a player nickname.'); return; }
    if (!line.trim() || isNaN(parseFloat(line))) { setManualError('Enter a valid line value (e.g. 21.5).'); return; }
    setManualError(null);
    setPhase('analyzing');
    cancelAbortRef.current?.abort();
    cancelAbortRef.current = new AbortController();
    try {
      const result = await cs2Predict({
        email:              session.email,
        token:              session.token,
        playerNickname:     cs2PlayerQuery.trim(),
        playerId:           cs2ResolvedPlayer?.id || null,
        teamName:           cs2ResolvedPlayer?.team?.name || '',
        teamId:             cs2ResolvedPlayer?.team?.id || null,
        propType:           cs2PropType,
        line:               parseFloat(line),
        opponentName:       cs2OpponentQuery.trim() || '',
        mapName:            cs2MapName.trim() || undefined,
        playerTeamStartsCt: cs2TeamStartsCt !== null ? cs2TeamStartsCt : undefined,
      }, cancelAbortRef.current.signal);
      if ((result as any).error) { setManualError((result as any).error); setPhase('idle'); return; }
      setScanResult({
        playerName:   result.playerName || cs2PlayerQuery.trim(),
        propType:     cs2PropType,
        line:         parseFloat(line),
        teamName:     result.teamName || '',
        opponentName: cs2OpponentQuery.trim() || '',
        leagueId:     0,
      });
      setPrediction(result);
      setPhase('result');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      if (e instanceof Error && e.message === '__CANCELLED__') return;
      setManualError(e instanceof Error ? e.message : 'CS2 analysis failed — try again');
      setPhase('idle');
    } finally {
      cancelAbortRef.current = null;
    }
  };

  // ── WTA handlers ─────────────────────────────────────────────────────────
  const handleWtaAnalyze = async () => {
    if (!session?.email || !session?.token) {
      Alert.alert('Sign In Required', 'Please sign in to run predictions.');
      return;
    }
    if (!(await ensurePredictionAccess())) {
      if (Platform.OS === 'web') { router.push('/(tabs)/account'); } else { router.push('/paywall'); }
      return;
    }
    if (!wtaPlayerQuery.trim()) { setManualError('Enter a player name.'); return; }
    if (!line.trim() || isNaN(parseFloat(line))) { setManualError('Enter a valid line value (e.g. 22.5).'); return; }
    setManualError(null);
    setPhase('analyzing');
    cancelAbortRef.current?.abort();
    cancelAbortRef.current = new AbortController();
    try {
      const result = await wtaPredict({
        email:        session.email,
        token:        session.token,
        playerName:   wtaPlayerQuery.trim(),
        playerId:     wtaResolvedPlayer?.id || null,
        opponentName: wtaResolvedOpponent?.fullName || wtaOpponentQuery.trim() || '',
        opponentId:   wtaResolvedOpponent?.id || null,
        propType:     wtaPropType,
        line:         parseFloat(line),
        surface:      wtaSurface,
        round:        wtaRound,
        subjectRank:  wtaResolvedPlayer?.currentRank ?? null,
        opponentRank: wtaResolvedOpponent?.currentRank ?? null,
      }, cancelAbortRef.current.signal);
      if ((result as any).error) { setManualError((result as any).error); setPhase('idle'); return; }
      setScanResult({
        playerName:   result.playerName || wtaPlayerQuery.trim(),
        propType:     wtaPropType,
        line:         parseFloat(line),
        teamName:     '',
        opponentName: wtaResolvedOpponent?.fullName || wtaOpponentQuery.trim() || '',
        leagueId:     0,
      });
      setPrediction(result);
      setPhase('result');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      if (e instanceof Error && e.message === '__CANCELLED__') return;
      setManualError(e instanceof Error ? e.message : 'WTA analysis failed — try again');
      setPhase('idle');
    } finally {
      cancelAbortRef.current = null;
    }
  };

  // ── NBA handlers ─────────────────────────────────────────────────────────
  const handleNbaAnalyze = async () => {
    if (!session?.email || !session?.token) { Alert.alert('Sign In Required', 'Please sign in to run predictions.'); return; }
    if (!(await ensurePredictionAccess())) { if (Platform.OS === 'web') { router.push('/(tabs)/account'); } else { router.push('/paywall'); } return; }
    if (!nbaPlayerQuery.trim()) { setManualError('Enter a player name.'); return; }
    if (!line.trim() || isNaN(parseFloat(line))) { setManualError('Enter a valid line value (e.g. 24.5).'); return; }
    setManualError(null);
    setPhase('analyzing');
    cancelAbortRef.current?.abort();
    cancelAbortRef.current = new AbortController();
    try {
      const playerName = nbaPlayerQuery.trim();
      const result = await nbaPredict({
        email:       session.email,
        token:       session.token,
        playerName,
        playerId:    nbaResolvedPlayer?.id || null,
        teamName:    nbaResolvedPlayer?.team?.full_name || '',
        teamId:      nbaResolvedPlayer?.team?.id || null,
        propType:    nbaPropType,
        line:        parseFloat(line),
        venue:       nbaNextMatch?.venue || nbaVenue,
        opponentName: nbaNextMatch?.opponent?.name || nbaOpponentQuery.trim() || '',
        opponentId:  nbaNextMatch?.opponent?.id || null,
      }, cancelAbortRef.current.signal);
      if ((result as any).error) { setManualError((result as any).error); setPhase('idle'); return; }
      setScanResult({ playerName, propType: nbaPropType, line: parseFloat(line), teamName: result.teamName || '', opponentName: nbaNextMatch?.opponent?.name || nbaOpponentQuery.trim() || '', leagueId: 0 });
      setPrediction(result);
      setPhase('result');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      if (e instanceof Error && e.message === '__CANCELLED__') return;
      setManualError(e instanceof Error ? e.message : 'NBA analysis failed — try again');
      setPhase('idle');
    } finally { cancelAbortRef.current = null; }
  };

  // ── NHL handlers ─────────────────────────────────────────────────────────
  const handleNhlAnalyze = async () => {
    if (!session?.email || !session?.token) { Alert.alert('Sign In Required', 'Please sign in to run predictions.'); return; }
    if (!(await ensurePredictionAccess())) { if (Platform.OS === 'web') { router.push('/(tabs)/account'); } else { router.push('/paywall'); } return; }
    if (!nhlPlayerQuery.trim()) { setManualError('Enter a player name.'); return; }
    if (!line.trim() || isNaN(parseFloat(line))) { setManualError('Enter a valid line value (e.g. 0.5).'); return; }
    setManualError(null);
    setPhase('analyzing');
    cancelAbortRef.current?.abort();
    cancelAbortRef.current = new AbortController();
    try {
      const playerName = nhlPlayerQuery.trim();
      const result = await nhlPredict({
        email:       session.email,
        token:       session.token,
        playerName,
        playerId:    nhlResolvedPlayer?.id || null,
        teamName:    nhlResolvedPlayer?.team?.full_name || '',
        teamId:      nhlResolvedPlayer?.team?.id || null,
        propType:    nhlPropType,
        line:        parseFloat(line),
        venue:       nhlNextMatch?.venue || nhlVenue,
        opponentName: nhlNextMatch?.opponent?.name || nhlOpponentQuery.trim() || '',
        opponentId:  nhlNextMatch?.opponent?.id || null,
      }, cancelAbortRef.current.signal);
      if ((result as any).error) { setManualError((result as any).error); setPhase('idle'); return; }
      setScanResult({ playerName, propType: nhlPropType, line: parseFloat(line), teamName: result.teamName || '', opponentName: nhlNextMatch?.opponent?.name || nhlOpponentQuery.trim() || '', leagueId: 0 });
      setPrediction(result);
      setPhase('result');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      if (e instanceof Error && e.message === '__CANCELLED__') return;
      setManualError(e instanceof Error ? e.message : 'NHL analysis failed — try again');
      setPhase('idle');
    } finally { cancelAbortRef.current = null; }
  };

  // ── NFL handlers ─────────────────────────────────────────────────────────
  const handleNflAnalyze = async () => {
    if (!session?.email || !session?.token) { Alert.alert('Sign In Required', 'Please sign in to run predictions.'); return; }
    if (!(await ensurePredictionAccess())) { if (Platform.OS === 'web') { router.push('/(tabs)/account'); } else { router.push('/paywall'); } return; }
    if (!nflPlayerQuery.trim()) { setManualError('Enter a player name.'); return; }
    if (!line.trim() || isNaN(parseFloat(line))) { setManualError('Enter a valid line value (e.g. 24.5).'); return; }
    setManualError(null);
    setPhase('analyzing');
    cancelAbortRef.current?.abort();
    cancelAbortRef.current = new AbortController();
    try {
      const playerName = nflPlayerQuery.trim();
      const result = await nflPredict({
        email:        session.email,
        token:        session.token,
        playerName,
        playerId:     nflResolvedPlayer?.id || null,
        teamName:     nflResolvedPlayer?.team?.full_name || '',
        teamId:       nflResolvedPlayer?.team?.id || null,
        propType:     nflPropType,
        line:         parseFloat(line),
        venue:        nflNextMatch?.venue || nflVenue,
        opponentName: nflNextMatch?.opponent?.name || nflOpponentQuery.trim() || '',
        opponentId:   nflNextMatch?.opponent?.id || null,
      }, cancelAbortRef.current.signal);
      if ((result as any).error) { setManualError((result as any).error); setPhase('idle'); return; }
      setScanResult({ playerName, propType: nflPropType, line: parseFloat(line), teamName: result.teamName || '', opponentName: nflNextMatch?.opponent?.name || nflOpponentQuery.trim() || '', leagueId: 0 });
      setPrediction(result);
      setPhase('result');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      if (e instanceof Error && e.message === '__CANCELLED__') return;
      setManualError(e instanceof Error ? e.message : 'NFL analysis failed — try again');
      setPhase('idle');
    } finally { cancelAbortRef.current = null; }
  };

  const confirmNflPlayer = async (player: NflPlayer) => {
    setNflPendingPlayer(null);
    setNflResolvedPlayer(player);
    setNflNextMatch(null);
    setNflOpponentQuery('');
    Haptics.selectionAsync();
    if (!player.id) return;
    setNflNextMatchLoading(true);
    try {
      const nm = await getNflNextMatch(player.id);
      setNflNextMatch(nm);
      if (nm.found) {
        if (nm.opponent?.name) setNflOpponentQuery(nm.opponent.name);
        if (nm.venue) setNflVenue(nm.venue);
      }
    } catch {
      setNflNextMatch({ found: false });
    } finally {
      setNflNextMatchLoading(false);
    }
  };

  // ── MLB handlers ─────────────────────────────────────────────────────────
  const handleMlbAnalyze = async () => {
    if (!session?.email || !session?.token) { Alert.alert('Sign In Required', 'Please sign in to run predictions.'); return; }
    if (!(await ensurePredictionAccess())) { if (Platform.OS === 'web') { router.push('/(tabs)/account'); } else { router.push('/paywall'); } return; }
    if (!mlbPlayerQuery.trim()) { setManualError('Enter a player name.'); return; }
    if (!line.trim() || isNaN(parseFloat(line))) { setManualError('Enter a valid line value (e.g. 1.5).'); return; }
    setManualError(null);
    setPhase('analyzing');
    cancelAbortRef.current?.abort();
    cancelAbortRef.current = new AbortController();
    try {
      const playerName = mlbPlayerQuery.trim();
      const result = await mlbPredict({
        email:       session.email,
        token:       session.token,
        playerName,
        playerId:    mlbResolvedPlayer?.id || null,
        teamName:    mlbResolvedPlayer?.team?.full_name || '',
        teamId:      mlbResolvedPlayer?.team?.id || null,
        propType:    mlbPropType,
        line:        parseFloat(line),
        venue:       mlbNextMatch?.venue || mlbVenue,
        opponentName: mlbNextMatch?.opponent?.name || mlbOpponentQuery.trim() || '',
        opponentId:  mlbNextMatch?.opponent?.id || null,
      }, cancelAbortRef.current.signal);
      if ((result as any).error) { setManualError((result as any).error); setPhase('idle'); return; }
      setScanResult({ playerName, propType: mlbPropType, line: parseFloat(line), teamName: result.teamName || '', opponentName: mlbNextMatch?.opponent?.name || mlbOpponentQuery.trim() || '', leagueId: 0 });
      setPrediction(result);
      setPhase('result');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      if (e instanceof Error && e.message === '__CANCELLED__') return;
      setManualError(e instanceof Error ? e.message : 'MLB analysis failed — try again');
      setPhase('idle');
    } finally { cancelAbortRef.current = null; }
  };

  const confirmMlbPlayer = async (player: MlbPlayer) => {
    setMlbPendingPlayer(null);
    setMlbResolvedPlayer(player);
    setMlbNextMatch(null);
    setMlbOpponentQuery('');
    Haptics.selectionAsync();
    if (!player.id) return;
    setMlbNextMatchLoading(true);
    try {
      const nm = await getMlbNextMatch(player.id);
      setMlbNextMatch(nm);
      if (nm.found) {
        if (nm.opponent?.name) setMlbOpponentQuery(nm.opponent.name);
        if (nm.venue) setMlbVenue(nm.venue);
      }
    } catch {
      setMlbNextMatch({ found: false });
    } finally {
      setMlbNextMatchLoading(false);
    }
  };

  const handleSavePick = async () => {
    if (!session || !prediction) return;
    setSaving(true);
    setSaveError(null);
    try {
      // Snapshot projected possession (model's pre-match guess) so the pick
      // can later be compared to actual settled possession to find an edge.
      const projHomePoss = prediction.expectedPossession?.home;
      const projAwayPoss = prediction.expectedPossession?.away;
      await savePick(session.email, session.token, {
        playerName: prediction.playerName || scanResult?.playerName || playerQuery,
        teamName: prediction.teamName || scanResult?.teamName || scanResult?.playerTeam,
        // Keep the verified soccer context on the save request.  The backend
        // needs these top-level fields as well as the original request when
        // validating and settling a pick; older clients only sent names.
        teamId: (prediction as any).fixtureTeamId
          || (prediction as any).teamId
          || scanResult?.teamId
          || predictionRequest?.teamId
          || 0,
        opponentId: (prediction as any).fixtureOpponentId
          || (prediction as any).opponentId
          || scanResult?.opponentId
          || predictionRequest?.opponentId
          || 0,
        opponentName: prediction.opponentName || scanResult?.opponentName,
        propType: prediction.propType || scanResult?.propType || propType,
        line: prediction.line ?? scanResult?.line ?? parseFloat(line),
        projection: prediction.projection ?? prediction.bayesianProjection,
        recommendation: prediction.recommendation,
        passLeaning: (prediction as any).passLeaning
          || (prediction as any).skipDetails?.direction?.toLowerCase()
          || undefined,
        passReason: (prediction as any).passReason || undefined,
        qualityConfidenceCapped: (prediction as any).qualityConfidenceCapped || undefined,
        isCalibrationOnly: prediction.recommendation === 'PASS',
        confidence: prediction.confidence,
        confidenceScore: prediction.confidenceScore ?? (typeof prediction.confidence === 'number' && prediction.confidence <= 1 ? Math.round(prediction.confidence * 100) : prediction.confidence),
        rawConfidence: prediction.rawConfidence ?? prediction.confidenceScore ?? (typeof prediction.confidence === 'number' && prediction.confidence <= 1 ? Math.round(prediction.confidence * 100) : prediction.confidence),
        confidenceLevel: prediction.confidenceLevel,
        confidenceInterval: prediction.confidenceInterval,
        position: prediction.playerPosition || undefined,
        role: prediction.playerRole || undefined,
        sport: sport,
        gameScript: prediction.gameScript || undefined,
        analysisFactors: prediction.analysisFactors || undefined,
        modelInputSnapshot: prediction.modelInputSnapshot || undefined,
         positionComparison: (prediction as any).positionComparison
           || (prediction as any).tacticalIntelligence?.positionCohort
           || undefined,
        moneyline: prediction.moneyline || undefined,
         homeTeam: (prediction as any).homeTeam || (prediction as any).matchupOverview?.homeTeam || undefined,
         awayTeam: (prediction as any).awayTeam || (prediction as any).matchupOverview?.awayTeam || undefined,
        projHomePoss: sport === 'soccer' && Number.isFinite(projHomePoss) ? projHomePoss : undefined,
        projAwayPoss: Number.isFinite(projAwayPoss) ? projAwayPoss : undefined,
        // Permanent fix: store the exact fixtureId so settlement never does
        // fuzzy fixture matching again.  This is the single source of truth
        // for which match this pick belongs to.
        fixtureId: (prediction as any).fixtureId || undefined,
        fixtureDate: (prediction as any).fixtureDate || undefined,
        leagueId: (prediction as any).leagueId || scanResult?.leagueId || leagueId || 0,
        venue: typeof (prediction as any).isHome === 'boolean'
          ? ((prediction as any).isHome ? 'home' : 'away')
          : ((prediction as any).venue
            || ((prediction as any).playerIsHome === false ? 'away' : (venueOverride || 'home'))),
        playerIsHome: typeof (prediction as any).isHome === 'boolean'
          ? (prediction as any).isHome
          : undefined,
        // Soccer: persist structured analysis on the pick so the analysis modal can show it
        ...(sport === 'soccer' ? {
          sharpSummary:      prediction.sharpSummary  || undefined,
          reasoning:         prediction.reasoning      || prediction.tacticalBreakdown || undefined,
          tacticalBreakdown: prediction.tacticalBreakdown || undefined,
           aiSource:          prediction.aiSource || undefined,
           playerGameLogs:    prediction.playerGameLogs || undefined,
          tacticalAlerts:    prediction.tacticalAlerts || undefined,
          bayesianMetrics:   (prediction as any).bayesianMetrics || undefined,
           tacticalContext:   (prediction as any).tacticalContext || undefined,
           tacticalIntelligence: (prediction as any).tacticalIntelligence || undefined,
           matchScript:      (prediction as any).matchScript || (prediction as any).tacticalIntelligence?.matchScript || undefined,
           positionalReality: (prediction as any).positionalReality || (prediction as any).tacticalIntelligence?.positionalReality || undefined,
           homeTeam:          (prediction as any).homeTeam || (prediction as any).matchupOverview?.homeTeam || undefined,
           awayTeam:          (prediction as any).awayTeam || (prediction as any).matchupOverview?.awayTeam || undefined,
        } : {}),
        // WTA: persist tennis-specific fields and structured analysis
        ...(sport === 'wta' ? {
          playerId:        prediction.playerId,
          opponentId:      (prediction as any).opponentId,
          surface:         (prediction as any).surface,
          round:           (prediction as any).round,
          tournament:      (prediction as any).tournament,
          subjectRank:     (prediction as any).subjectRank,
          opponentRank:    (prediction as any).opponentRank,
          sharpSummary:    prediction.sharpSummary  || undefined,
          reasoning:       prediction.reasoning      || undefined,
          tacticalMetrics: (prediction as any).bayesianMetrics?.tacticalMetrics || undefined,
          projectedValue:  prediction.projection,
          pOver:           prediction.pOver,
          pUnder:          prediction.pUnder,
          priorMean:       (prediction as any).bayesianMetrics?.priorMean,
          momentumMean:    (prediction as any).bayesianMetrics?.momentumMean,
          sampleSize:      (prediction as any).bayesianMetrics?.sampleSize,
        } : {}),
        // CS2: persist structured analysis directly on the pick so the analysis modal can show it
        ...(sport === 'cs2' ? {
          sharpSummary:    prediction.sharpSummary  || undefined,
          reasoning:       prediction.reasoning      || prediction.tacticalBreakdown || undefined,
          tacticalMetrics: (prediction as any).bayesianMetrics?.tacticalMetrics || undefined,
          projectedValue:  prediction.projection,
          pOver:           prediction.pOver,
          pUnder:          prediction.pUnder,
          priorMean:       (prediction as any).bayesianMetrics?.priorMean,
          momentumMean:    (prediction as any).bayesianMetrics?.momentumMean,
          sampleSize:      (prediction as any).bayesianMetrics?.sampleSize,
          streakFlag:      (prediction as any).streakFlag,
        } : {}),
        // NBA/NHL/MLB: persist Bayesian metrics + structured analysis
        ...(['nba', 'nhl', 'mlb'].includes(sport) ? {
          sharpSummary:    prediction.sharpSummary        || undefined,
          reasoning:       prediction.reasoning            || prediction.tacticalBreakdown || undefined,
          projectedValue:  prediction.projection,
          pOver:           prediction.pOver,
          pUnder:          prediction.pUnder,
          priorMean:       (prediction as any).bayesianMetrics?.priorMean,
          momentumMean:    (prediction as any).bayesianMetrics?.momentumMean,
          sampleSize:      (prediction as any).bayesianMetrics?.sampleSize,
          streakFlag:      (prediction as any).streakFlag,
          rawConfidence:   prediction.rawConfidence ?? prediction.confidenceScore,
        } : {}),
        // Manager context — persist so the badge shows on the saved pick card
        // without re-running the prediction.
        managerContext: (() => {
          const mc = (prediction as any).managerContext;
          if (!mc) return undefined;
          // Only persist the fields the badge and analysis modal need
          return {
            isRecent: mc.isRecent === true || mc.recentChange === true,
            coachName: mc.coachName || mc.newCoachName || undefined,
          };
        })(),
        player: {
          id: prediction.playerId || 0,
          name: prediction.playerName || scanResult?.playerName || playerQuery,
          team: prediction.teamName || scanResult?.teamName || scanResult?.playerTeam || '',
          position: prediction.playerPosition || undefined,
          role: prediction.playerRole || undefined,
        },
        _request: {
          teamId: (prediction as any).fixtureTeamId
            || (prediction as any).teamId
            || scanResult?.teamId
            || predictionRequest?.teamId
            || 0,
          opponentId: (prediction as any).fixtureOpponentId
            || (prediction as any).opponentId
            || scanResult?.opponentId
            || predictionRequest?.opponentId
            || 0,
          leagueId: (prediction as any).leagueId
            || scanResult?.leagueId
            || predictionRequest?.leagueId
            || leagueId
            || 0,
          venue: typeof (prediction as any).isHome === 'boolean'
            ? ((prediction as any).isHome ? 'home' : 'away')
            : (venueOverride || 'home'),
          fixtureId: (prediction as any).fixtureId || predictionRequest?.fixtureId,
        },
      });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      setPickSaved(true);
      qc.invalidateQueries({ queryKey: ['picks'] });
      router.push('/(tabs)/picks');
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Save failed — try again';
      setSaveError(msg);
    } finally {
      setSaving(false);
    }
  };

  const recColor = prediction?.recommendation === 'OVER' ? Colors.success
    : prediction?.recommendation === 'UNDER' ? Colors.error
    : Colors.textSecondary;

  const confPct = prediction?.confidence != null
    ? (prediction.confidence > 1 ? Math.round(prediction.confidence) : Math.round(prediction.confidence * 100))
    : null;
  const recommendationProbability = prediction?.recommendation === 'OVER'
    ? prediction.pOver
    : prediction?.recommendation === 'UNDER'
      ? prediction.pUnder
      : null;
  const modelProbPct = recommendationProbability != null
    ? Number(recommendationProbability)
    : null;

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: Colors.background }}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      keyboardVerticalOffset={Platform.OS === 'ios' ? 0 : 20}
    >
    <View style={[styles.root, { paddingTop: topPad }]}>


      {/* ─── Branded header ─── */}
      <View style={styles.scanHeader}>
        <View style={styles.scanHeaderBrand}>
          <Image source={require('../../assets/logo.png')} style={styles.scanHeaderLogo} resizeMode="contain" />
          <View>
            <Text style={styles.scanHeaderTitle}>REVERSE PICKS</Text>
            <Text style={styles.scanHeaderSub}>Structured Player Props</Text>
          </View>
        </View>
        <NotificationBell />
      </View>
      <View style={styles.detectedSportBanner}>
        <Ionicons
          name={sport === 'mlb' ? 'baseball-outline' : sport === 'nfl' ? 'american-football-outline' : 'football-outline'}
          size={15}
          color={Colors.primary}
        />
        <Text style={styles.detectedSportBannerText}>
          {sport === 'mlb' ? 'MLB' : sport === 'nfl' ? 'NFL' : 'Soccer'}
        </Text>
        <Text style={styles.detectedSportBannerHint}>AUTO-DETECTED</Text>
      </View>

      <ScrollView
        contentContainerStyle={styles.body}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {/* ─── PAYWALL OVERLAY (NoSubscription) ─── */}
        {isNoSub && (
          <View style={styles.paywallOverlay}>
            <Ionicons name="lock-closed" size={48} color={Colors.primary} style={{ marginBottom: 16 }} />
            <Text style={styles.paywallOverlayTitle}>Unlock Predictions</Text>
            <Text style={styles.paywallOverlayBody}>
              Get unlimited deterministic player prop projections, structured breakdowns, and sharp summaries.
            </Text>
            <TouchableOpacity
              style={styles.paywallOverlayBtn}
              onPress={() => {
                if (Platform.OS === 'web') {
                  router.push('/(tabs)/account');
                } else {
                  router.push('/paywall');
                }
              }}
              activeOpacity={0.85}
            >
              <Text style={styles.paywallOverlayBtnText}>Get Access</Text>
            </TouchableOpacity>
          </View>
        )}

        {/* ─── SCAN SECTION ─── */}
        <View
          pointerEvents={isNoSub ? 'none' : 'auto'}
          style={[
            mode === 'manual'
              ? { paddingTop: 22, paddingBottom: 40 }
              : { minHeight: SCREEN_H - 200 },
          ]}
        >
            {/* Idle: cartoon sports image only */}
            {phase === 'idle' && (
              <>
                {/* Eyebrow label — gives the centered form a visual anchor */}
                {mode === 'manual' && (
                  <Text style={styles.formEyebrow}>Analyze a Prop</Text>
                )}
                {analyzeError && (
                  <View style={styles.inlineError}>
                    <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                    <Text style={styles.inlineErrorText}>{analyzeError}</Text>
                  </View>
                )}
              </>
            )}

            {/* Scanning */}
            {phase === 'scanning' && (
              <View style={{ alignItems: 'center', paddingVertical: 60, gap: 16 }}>
                <ActivityIndicator size="large" color={Colors.primary} />
                <Text style={{ color: Colors.primary, fontSize: 13, fontWeight: '800', letterSpacing: 2 }}>SCANNING</Text>
                <Text style={{ color: Colors.textSecondary, fontSize: 11 }}>Reading prop slip…</Text>
              </View>
            )}

            {/* Detected: image-only scan result */}
            {(phase === 'detected' || (phase === 'analyzing' && mode === 'scan')) && scanResult && (
              <>
                {/* Full-width image only */}
                {scannedImageUri && (
                  <Image source={{ uri: scannedImageUri }} style={styles.scannedPreview} resizeMode="cover" />
                )}

                {/* Inline error */}
                {analyzeError && (
                  <View style={styles.inlineError}>
                    <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                    <Text style={styles.inlineErrorText}>{analyzeError}</Text>
                  </View>
                )}

                {phase === 'detected' && (
                  <TouchableOpacity
                    style={styles.predictBtn}
                    onPress={() => runPredict(scanResult)}
                    activeOpacity={0.85}
                  >
                    <Ionicons name="flash" size={16} color="#000" />
                    <Text style={styles.predictBtnText}>RUN PREDICTION</Text>
                  </TouchableOpacity>
                )}

                {phase === 'analyzing' && (
                  <View style={{ alignItems: 'center', paddingVertical: 12, gap: 8 }}>
                    <ActivityIndicator size="small" color={Colors.primary} />
                    <Text style={{ color: Colors.primary, fontSize: 12, fontWeight: '700' }}>Analyzing…</Text>
                    <TouchableOpacity onPress={reset} style={styles.cancelBtn}>
                      <Ionicons name="close-circle-outline" size={14} color={Colors.error} />
                      <Text style={styles.cancelBtnText}>Cancel</Text>
                    </TouchableOpacity>
                  </View>
                )}

                <TouchableOpacity onPress={reset} style={styles.rescanBtn}>
                  <Ionicons name="refresh-outline" size={14} color={Colors.textSecondary} />
                  <Text style={styles.rescanText}>Scan Different Slip</Text>
                </TouchableOpacity>
              </>
            )}


        {phase !== 'result' && phase !== 'saved' && (
          <View style={styles.universalPlayerSection}>
            <Text style={styles.fieldLabel}>PLAYER</Text>
            <FuzzySearchInput
              value={
                sport === 'mlb' ? mlbPlayerQuery
                  : sport === 'nfl' ? nflPlayerQuery
                    : playerQuery
              }
              onChangeText={(text) => {
                if (sport === 'mlb') {
                  setMlbPlayerQuery(text);
                  if (!text) {
                    setMlbPendingPlayer(null); setMlbResolvedPlayer(null);
                    setMlbNextMatch(null); setMlbOpponentQuery('');
                  }
                } else if (sport === 'nfl') {
                  setNflPlayerQuery(text);
                  if (!text) {
                    setNflPendingPlayer(null); setNflResolvedPlayer(null);
                    setNflNextMatch(null); setNflOpponentQuery('');
                  }
                } else {
                  setPlayerQuery(text);
                  if (!text) {
                    setResolvedPlayer(null); setAutoMatch(null);
                    setSelectedContext(null); setPlayerContexts([]);
                    setClubVerificationStatus('idle');
                    setNextMatchLoading(false);
                  }
                }
              }}
              searchType="all_players"
              placeholder="Search any player — soccer, MLB, or NFL"
               ownerSession={session?.email && session?.token ? { email: session.email, token: session.token } : undefined}
               confirmed={sport === 'mlb' ? !!mlbResolvedPlayer : sport === 'nfl' ? !!nflResolvedPlayer : (!!resolvedPlayer && (clubVerificationStatus === 'verified' || clubVerificationStatus === 'last_known'))}
              style={{ marginBottom: 2 }}
              onSelectAllPlayer={handleUniversalPlayerSelect}
            />
          </View>
        )}

        {/* ─── MANUAL FORM — Soccer ─── */}
        {sport === 'soccer' && phase !== 'result' && phase !== 'saved' && (
          <View style={styles.manualForm}>
            {scanFillHint && (
              <View style={styles.scanFillHint}>
                <Ionicons name={scanFillHint.startsWith('✓') ? 'checkmark-circle-outline' : 'warning-outline'} size={13} color={scanFillHint.startsWith('✓') ? Colors.primary : '#f0a500'} />
                <Text style={[styles.scanFillHintText, !scanFillHint.startsWith('✓') && { color: '#f0a500' }]}>{scanFillHint}</Text>
              </View>
            )}
            {resolvedPlayer && (
              <View style={{ marginBottom: 4, marginLeft: 2 }}>
                <Text style={{ color: clubVerificationStatus === 'verified' ? Colors.primary : '#f0a500', fontSize: 11 }}>
                  {clubVerificationStatus === 'loading'
                    ? '⟳ Verifying current club…'
                    : clubVerificationStatus === 'verified'
                      ? `✓ ${resolvedPlayer.teamName}`
                      : clubVerificationStatus === 'last_known'
                        ? `⚠ Last known club: ${playerContexts.find(c => c.lastKnown)?.teamName || 'previous club'} — current club not confirmed`
                        : '⚠ Current club unavailable — retry player selection'}
                  {resolvedRole?.position
                    ? ` · ${resolvedRole.position}`
                    : resolvedPlayer.position
                      ? ` · ${resolvedPlayer.position}`
                      : ''}
                  {resolvedRole?.role
                    ? `  ·  ${resolvedRole.role}${resolvedRole.roleIsInferred ? ' (inferred)' : ''}`
                    : resolvedRole?.position && resolvedRole.position.toUpperCase() === 'MID'
                      ? '  ·  exact role unavailable'
                      : ''}
                </Text>
                {roleLoading && (
                  <Text style={{ color: '#666', fontSize: 10, marginTop: 2 }}>
                    ⟳ Detecting tactical role...
                  </Text>
                )}
              </View>
            )}

            {/* ── Team context picker: current team + national teams only ── */}
            {resolvedPlayer && (() => {
              // Keep the current club context plus national-team alternatives.
              // The search result can identify the national side (e.g.
              // Cristiano → Portugal) even when the club is the useful
              // default for next-match lookup.
              const currentClub = playerContexts.find(c => !c.isNational && c.verified === true);
               const displayCtxs = Array.from(
                 new Map(
                   playerContexts
                     .filter(
                       c => c.isNational || (
                         currentClub
                         && c.teamId === currentClub.teamId
                         && c.verified === true
                       )
                     )
                     .map(c => [String(c.teamId), c] as const)
                 ).values()
               );
              // Show the national context even when the current club could not
              // be verified. Never hide the only safe, explicit alternative.
              if (displayCtxs.length === 0 || !displayCtxs.some(c => c.isNational)) return null;
              return (
                <View style={{ marginBottom: 12 }}>
                  <Text style={styles.fieldLabel}>PREDICT AS</Text>
                  <View style={{ flexDirection: 'row', gap: 8 }}>
                    {displayCtxs.map((ctx) => {
                      const active = selectedContext?.teamId === ctx.teamId;
                      return (
                        <TouchableOpacity
                          key={ctx.teamId}
                          style={[{
                            flex: 1, paddingVertical: 10, paddingHorizontal: 8,
                            borderRadius: 8, borderWidth: 1,
                            borderColor: active ? Colors.primary : '#2a2a2a',
                            backgroundColor: active ? 'rgba(57,255,20,0.08)' : '#111',
                            alignItems: 'center',
                          }]}
                          onPress={async () => {
                             if (selectedContext?.teamId === ctx.teamId) return;
                             // Last-known clubs are display-only context. Only
                             // a verified club or explicit national team can
                             // trigger a matchup lookup.
                             if (!ctx.isNational && ctx.verified !== true) return;
                            setSelectedContext(ctx);
                            setAutoMatch(null);
                            setManualOpponentQuery('');
                            setResolvedManualOpponent(null);
                            setNextMatchLoading(true);
                            Haptics.selectionAsync();
                            try {
                              const nm = await getTeamNextMatch(ctx.teamId);
                              if (nm?.found) {
                                setAutoMatch(nm);
                                setVenueOverride(nm.isHome ? 'home' : 'away');
                              }
                              if (nm?.leagueId) {
                                setLeagueId(nm.leagueId); setLeagueQuery(nm.leagueName || '');
                              } else {
                                const fallbackId = ctx.leagueId || 0;
                                if (fallbackId && fallbackId !== 667) {
                                  const lgInfo = await getLeagueById(fallbackId);
                                  setLeagueId(fallbackId);
                                  setLeagueQuery(lgInfo?.name || '');
                                }
                              }
                            } catch {}
                            setNextMatchLoading(false);
                          }}
                          activeOpacity={0.75}
                        >
                          <Ionicons
                            name={ctx.isNational ? 'flag-outline' : 'shirt-outline'}
                            size={14}
                            color={active ? Colors.primary : Colors.textSecondary}
                          />
                          <Text style={{ color: active ? Colors.primary : Colors.textSecondary, fontSize: 12, fontWeight: active ? '700' : '400', marginTop: 3, textAlign: 'center' }}>
                            {ctx.teamName}
                          </Text>
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                </View>
              );
            })()}
            {contextsLoading && (
              <ActivityIndicator size="small" color={Colors.primary} style={{ alignSelf: 'flex-start', marginBottom: 8 }} />
            )}

            {/* ── Auto-filled next match card ── */}
            {nextMatchLoading && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12, padding: 10, backgroundColor: '#111', borderRadius: 8, borderWidth: 1, borderColor: '#2a2a2a' }}>
                <ActivityIndicator size="small" color={Colors.primary} />
                <Text style={{ color: Colors.textSecondary, fontSize: 12 }}>Fetching next match…</Text>
              </View>
            )}
            {autoMatch?.found && !nextMatchLoading && (
              <View style={{ marginBottom: 12, padding: 10, backgroundColor: 'rgba(57,255,20,0.06)', borderRadius: 8, borderWidth: 1, borderColor: 'rgba(57,255,20,0.25)' }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <Ionicons name="flash" size={12} color={Colors.primary} />
                  <Text style={{ color: Colors.primary, fontSize: 11, fontWeight: '700', letterSpacing: 0.5 }}>NEXT MATCH AUTO-FILLED</Text>
                </View>
                <Text style={{ color: Colors.text, fontSize: 13, fontWeight: '600' }}>
                  {autoMatch.homeTeam?.name || (autoMatch.isHome ? selectedContext?.teamName : autoMatch.opponent?.name)}
                  {'  vs  '}
                  {autoMatch.awayTeam?.name || (!autoMatch.isHome ? selectedContext?.teamName : autoMatch.opponent?.name)}
                </Text>
                 <Text style={{ color: Colors.textSecondary, fontSize: 11, marginTop: 2 }}>
                   {(autoMatch.isHome ? 'HOME' : 'AWAY')}{autoMatch.leagueName ? ` · ${autoMatch.leagueName}` : ''}{autoMatch.date ? ` · ${new Date(autoMatch.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : ''}
                  {autoMatch.fixtureId ? ` · Match ${autoMatch.fixtureId}` : ''}
                </Text>
                <TouchableOpacity onPress={() => { setAutoMatch(null); setSelectedContext(null); }} style={{ marginTop: 6 }}>
                  <Text style={{ color: Colors.textSecondary, fontSize: 10 }}>✕ clear auto-fill</Text>
                </TouchableOpacity>
              </View>
            )}

            {/* ── League + Opponent — only shown when player is selected but auto-match didn't find a fixture ── */}
            {resolvedPlayer && !autoMatch?.found && !nextMatchLoading && (
              <View style={{
                marginTop: 4, padding: 12, borderRadius: 12,
                backgroundColor: 'rgba(255,255,255,0.03)',
                borderWidth: 1, borderColor: 'rgba(57,255,20,0.1)',
              }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                  <Ionicons name="search-outline" size={11} color={Colors.textTertiary} />
                  <Text style={{ fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1, textTransform: 'uppercase' }}>
                    Set Match Manually
                  </Text>
                </View>
                <FuzzySearchInput
                  value={leagueQuery}
                  onChangeText={(t) => setLeagueQuery(t)}
                  searchType="leagues"
                  placeholder="Search league…"
                  style={{ marginBottom: 8 }}
                  confirmed={!!leagueId}
                  onSelectLeague={(l: FuzzyLeagueResult) => {
                    setLeagueId(l.id);
                    setLeagueQuery(l.name);
                    Haptics.selectionAsync();
                  }}
                />

                <FuzzySearchInput
                  searchType="teams"
                  value={manualOpponentQuery}
                  onChangeText={(t) => {
                    setManualOpponentQuery(t);
                    if (!t) setResolvedManualOpponent(null);
                  }}
                  placeholder={leagueId === 1 ? 'e.g. France, Argentina, Spain…' : 'e.g. Arsenal, Real Madrid…'}
                  confirmed={!!resolvedManualOpponent}
                  staticItems={leagueId === 1 ? WC_NATIONS : undefined}
                  onSelectTeam={(t: FuzzyTeamResult) => {
                    setResolvedManualOpponent(t);
                    setManualOpponentQuery(t.teamName);
                    Haptics.selectionAsync();
                  }}
                  onSelectStaticItem={(_raw: any, primary: string) => {
                    setManualOpponentQuery(primary);
                    setResolvedManualOpponent(null);
                    Haptics.selectionAsync();
                  }}
                />
              </View>
            )}

            {resolvedPlayer && (<>
            <Text style={styles.fieldLabel}>PROP TYPE</Text>
            <TouchableOpacity style={styles.pickerBtn} onPress={() => setShowPropPicker(true)}>
              <Text style={styles.pickerBtnText}>{PROP_TYPES.find(p => p.value === propType)?.label || 'Select'}</Text>
              <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
            </TouchableOpacity>

            <Text style={styles.fieldLabel}>LINE VALUE</Text>
            <TextInput
              style={[styles.textInput, INPUT_STYLE]}
              placeholder="e.g. 50.5"
              placeholderTextColor={Colors.textTertiary}
              value={line}
              onChangeText={setLine}
              keyboardType="decimal-pad"
            />

            {manualError && (
              <View style={styles.inlineError}>
                <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                <Text style={styles.inlineErrorText}>{manualError}</Text>
              </View>
            )}

            {(() => {
              const lineReady = line.trim().length > 0 && !isNaN(parseFloat(line));
              const playerReady = !!resolvedPlayer || playerQuery.trim().length > 0;
              const isAnalyzing = phase === 'analyzing';
              // Show disabled state directly on the button when line is missing —
              // the normal error message is hidden behind the iOS keyboard on web.
              return (
                <TouchableOpacity
                  style={[
                    styles.predictBtn,
                    isAnalyzing && styles.predictBtnCancel,
                  ]}
                  onPress={isAnalyzing ? reset : handleManualAnalyze}
                  activeOpacity={0.85}
                >
                  {isAnalyzing ? (
                    <>
                      <Ionicons name="close-circle-outline" size={16} color="#fff" />
                      <Text style={[styles.predictBtnText, { color: '#fff' }]}>Cancel</Text>
                    </>
                  ) : (
                    <>
                      <Ionicons name="analytics-outline" size={16} color="#000" />
                      <Text style={styles.predictBtnText}>Analyze</Text>
                    </>
                  )}
                </TouchableOpacity>
              );
            })()}
            </>)}
          </View>
        )}

        {/* ─── MANUAL MODE — CS2 ─── */}
        {sport === 'cs2' && phase !== 'result' && phase !== 'saved' && (
          <View style={styles.manualForm}>
            {scanFillHint && (
              <View style={styles.scanFillHint}>
                <Ionicons name={scanFillHint.startsWith('✓') ? 'checkmark-circle-outline' : 'warning-outline'} size={13} color={scanFillHint.startsWith('✓') ? Colors.primary : '#f0a500'} />
                <Text style={[styles.scanFillHintText, !scanFillHint.startsWith('✓') && { color: '#f0a500' }]}>{scanFillHint}</Text>
              </View>
            )}
            <Text style={styles.fieldLabel}>Player Nickname</Text>
            <FuzzySearchInput
              searchType="cs2_players"
              value={cs2PlayerQuery}
              onChangeText={(t) => { setCs2PlayerQuery(t); if (!t) { setCs2ResolvedPlayer(null); setCs2NextMatch(null); setCs2OpponentQuery(''); setCs2ResolvedOpponent(null); } }}
              placeholder="e.g. ZywOo, s1mple, NiKo"
              confirmed={!!cs2ResolvedPlayer}
              autoCapitalize="none"
              onSelectCs2Player={async (p) => {
                setCs2ResolvedPlayer(p);
                setCs2PlayerQuery(p.nickname);
                setCs2NextMatch(null);
                setCs2OpponentQuery('');
                setCs2ResolvedOpponent(null);
                Haptics.selectionAsync();
                // Auto-fetch next match
                const teamId = p.team?.id ?? null;
                setCs2NextMatchLoading(true);
                try {
                  const nm = await getCs2NextMatch(p.id, teamId);
                  setCs2NextMatch(nm);
                  if (nm.found && nm.opponent?.name) {
                    setCs2OpponentQuery(nm.opponent.name);
                  }
                } catch { /* silent */ } finally {
                  setCs2NextMatchLoading(false);
                }
              }}
            />
            {cs2ResolvedPlayer && (
              <TouchableOpacity onPress={() => { setCs2PlayerQuery(''); setCs2ResolvedPlayer(null); setCs2NextMatch(null); setCs2OpponentQuery(''); setCs2ResolvedOpponent(null); Haptics.selectionAsync(); }} style={styles.changePlayerBtn}>
                <Ionicons name="arrow-undo-outline" size={12} color={Colors.primary} />
                <Text style={styles.changePlayerBtnText}>Change player</Text>
              </TouchableOpacity>
            )}

            {/* ── CS2 Next-match auto-fill banner ── */}
            {cs2NextMatchLoading && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 }}>
                <ActivityIndicator size="small" color={Colors.primary} />
                <Text style={{ color: Colors.textSecondary, fontSize: 12 }}>Fetching next match…</Text>
              </View>
            )}
            {cs2NextMatch?.found && !cs2NextMatchLoading && (
              <View style={[styles.autoFillBanner]}>
                <Ionicons name="flash" size={12} color={Colors.primary} />
                <View style={{ flex: 1 }}>
                  <Text style={{ color: Colors.primary, fontSize: 11, fontWeight: '700', letterSpacing: 0.5 }}>NEXT MATCH AUTO-FILLED</Text>
                  <Text style={{ color: Colors.text, fontSize: 13, fontWeight: '600', marginTop: 1 }}>
                    vs {cs2NextMatch.opponent?.name}
                  </Text>
                  <Text style={{ color: Colors.textSecondary, fontSize: 11, marginTop: 1 }}>
                    {cs2NextMatch.tournament}{cs2NextMatch.date ? ` · ${new Date(cs2NextMatch.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : ''}
                  </Text>
                </View>
              </View>
            )}

            {cs2ResolvedPlayer && !cs2NextMatch?.found && !cs2NextMatchLoading && (
              <>
                <Text style={styles.fieldLabel}>Opponent Team <Text style={styles.fieldLabelOpt}>(optional)</Text></Text>
                <FuzzySearchInput
                  searchType="cs2_teams"
                  value={cs2OpponentQuery}
                  onChangeText={(t) => { setCs2OpponentQuery(t); if (!t) setCs2ResolvedOpponent(null); }}
                  placeholder="e.g. NAVI, FaZe, Vitality…"
                  confirmed={!!cs2ResolvedOpponent}
                  autoCapitalize="none"
                  onSelectCs2Team={(t) => {
                    setCs2ResolvedOpponent(t);
                    setCs2OpponentQuery(t.name);
                    Haptics.selectionAsync();
                  }}
                />
              </>
            )}

            {cs2ResolvedPlayer && (
              <>
                <Text style={styles.fieldLabel}>Prop Type</Text>
                <TouchableOpacity style={styles.pickerBtn} onPress={() => setCs2ShowPropPicker(true)}>
                  <Text style={styles.pickerBtnText}>{CS2_PROP_TYPES.find(p => p.value === cs2PropType)?.label || 'Select'}</Text>
                  <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
                </TouchableOpacity>

                <Text style={styles.fieldLabel}>Line Value</Text>
                <TextInput
                  style={[styles.textInput, INPUT_STYLE]}
                  placeholder="e.g. 21.5"
                  placeholderTextColor={Colors.textTertiary}
                  value={line}
                  onChangeText={setLine}
                  keyboardType="decimal-pad"
                />

                {manualError && (
                  <View style={styles.inlineError}>
                    <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                    <Text style={styles.inlineErrorText}>{manualError}</Text>
                  </View>
                )}

                <TouchableOpacity
                  style={[styles.predictBtn, phase === 'analyzing' && styles.predictBtnCancel]}
                  onPress={phase === 'analyzing' ? reset : handleCs2Analyze}
                  activeOpacity={0.85}
                >
                  {phase === 'analyzing' ? (
                    <>
                      <Ionicons name="close-circle-outline" size={16} color="#fff" />
                      <Text style={[styles.predictBtnText, { color: '#fff' }]}>Cancel</Text>
                    </>
                  ) : (
                    <>
                      <Ionicons name="analytics-outline" size={16} color="#000" />
                      <Text style={styles.predictBtnText}>Analyze</Text>
                    </>
                  )}
                </TouchableOpacity>
              </>
            )}
          </View>
        )}

        {/* ─── WTA MANUAL FORM ─── */}
        {sport === 'wta' && phase !== 'result' && phase !== 'saved' && (
          <View style={styles.manualForm}>
            {scanFillHint && (
              <View style={styles.scanFillHint}>
                <Ionicons name={scanFillHint.startsWith('✓') ? 'checkmark-circle-outline' : 'warning-outline'} size={13} color={scanFillHint.startsWith('✓') ? Colors.primary : '#f0a500'} />
                <Text style={[styles.scanFillHintText, !scanFillHint.startsWith('✓') && { color: '#f0a500' }]}>{scanFillHint}</Text>
              </View>
            )}
            <Text style={styles.fieldLabel}>Player</Text>
            <FuzzySearchInput
              searchType="wta_players"
              value={wtaPlayerQuery}
              onChangeText={(t) => { setWtaPlayerQuery(t); if (!t) { setWtaResolvedPlayer(null); setWtaNextMatch(null); setWtaOpponentQuery(''); setWtaResolvedOpponent(null); } }}
              placeholder="e.g. Iga Swiatek"
              confirmed={!!wtaResolvedPlayer}
              autoCapitalize="words"
              onSelectWtaPlayer={async (p) => {
                setWtaResolvedPlayer(p);
                setWtaPlayerQuery(p.fullName);
                setWtaNextMatch(null);
                setWtaOpponentQuery('');
                setWtaResolvedOpponent(null);
                Haptics.selectionAsync();
                // Auto-fetch next match
                setWtaNextMatchLoading(true);
                try {
                  const nm = await getWtaNextMatch(p.id);
                  setWtaNextMatch(nm);
                  if (nm.found) {
                    if (nm.opponent?.name) setWtaOpponentQuery(nm.opponent.name);
                    if (nm.surface && WTA_SURFACES.includes(nm.surface)) setWtaSurface(nm.surface);
                    if (nm.round   && WTA_ROUNDS.includes(nm.round))     setWtaRound(nm.round);
                  }
                } catch { /* silent */ } finally {
                  setWtaNextMatchLoading(false);
                }
              }}
            />
            {wtaResolvedPlayer && (
              <TouchableOpacity onPress={() => { setWtaPlayerQuery(''); setWtaResolvedPlayer(null); setWtaNextMatch(null); setWtaOpponentQuery(''); setWtaResolvedOpponent(null); Haptics.selectionAsync(); }} style={styles.changePlayerBtn}>
                <Ionicons name="arrow-undo-outline" size={12} color={Colors.primary} />
                <Text style={styles.changePlayerBtnText}>Change player</Text>
              </TouchableOpacity>
            )}

            {/* ── WTA Next-match auto-fill banner ── */}
            {wtaNextMatchLoading && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 }}>
                <ActivityIndicator size="small" color={Colors.primary} />
                <Text style={{ color: Colors.textSecondary, fontSize: 12 }}>Fetching next match…</Text>
              </View>
            )}
            {wtaNextMatch?.found && !wtaNextMatchLoading && (
              <View style={[styles.autoFillBanner]}>
                <Ionicons name="flash" size={12} color={Colors.primary} />
                <View style={{ flex: 1 }}>
                  <Text style={{ color: Colors.primary, fontSize: 11, fontWeight: '700', letterSpacing: 0.5 }}>NEXT MATCH AUTO-FILLED</Text>
                  <Text style={{ color: Colors.text, fontSize: 13, fontWeight: '600', marginTop: 1 }}>
                    vs {wtaNextMatch.opponent?.name}
                  </Text>
                  <Text style={{ color: Colors.textSecondary, fontSize: 11, marginTop: 1 }}>
                    {wtaNextMatch.tournament}{wtaNextMatch.surface ? ` · ${wtaNextMatch.surface}` : ''}{wtaNextMatch.round ? ` · ${wtaNextMatch.round}` : ''}{wtaNextMatch.date ? ` · ${new Date(wtaNextMatch.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : ''}
                  </Text>
                </View>
              </View>
            )}

            {wtaResolvedPlayer && !wtaNextMatch?.found && !wtaNextMatchLoading && (
              <>
                {wtaNextMatch !== null && (
                  <View style={[styles.scanFillHint, { marginBottom: 8 }]}>
                    <Ionicons name="information-circle-outline" size={13} color={Colors.textTertiary} />
                    <Text style={[styles.scanFillHintText, { color: Colors.textTertiary }]}>No upcoming match found — enter opponent manually</Text>
                  </View>
                )}
                <Text style={styles.fieldLabel}>Opponent</Text>
                <FuzzySearchInput
                  searchType="wta_players"
                  value={wtaOpponentQuery}
                  onChangeText={(t) => { setWtaOpponentQuery(t); if (!t) setWtaResolvedOpponent(null); }}
                  placeholder="e.g. Aryna Sabalenka"
                  confirmed={!!wtaResolvedOpponent}
                  autoCapitalize="words"
                  onSelectWtaPlayer={(p) => {
                    setWtaResolvedOpponent(p);
                    setWtaOpponentQuery(p.fullName);
                    Haptics.selectionAsync();
                  }}
                />

                <Text style={styles.fieldLabel}>Surface</Text>
                <TouchableOpacity style={styles.pickerBtn} onPress={() => setWtaShowSurfacePicker(true)}>
                  <Text style={styles.pickerBtnText}>{wtaSurface}</Text>
                  <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
                </TouchableOpacity>

                <Text style={styles.fieldLabel}>Round</Text>
                <TouchableOpacity style={styles.pickerBtn} onPress={() => setWtaShowRoundPicker(true)}>
                  <Text style={styles.pickerBtnText}>{wtaRound}</Text>
                  <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
                </TouchableOpacity>
              </>
            )}

            {wtaResolvedPlayer && (
              <>
                <Text style={styles.fieldLabel}>Prop Type</Text>
                <TouchableOpacity style={styles.pickerBtn} onPress={() => setWtaShowPropPicker(true)}>
                  <Text style={styles.pickerBtnText}>{WTA_PROP_TYPES.find(p => p.value === wtaPropType)?.label || 'Select'}</Text>
                  <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
                </TouchableOpacity>

                <Text style={styles.fieldLabel}>Line Value</Text>
                <TextInput
                  style={[styles.textInput, INPUT_STYLE]}
                  placeholder="e.g. 22.5"
                  placeholderTextColor={Colors.textTertiary}
                  value={line}
                  onChangeText={setLine}
                  keyboardType="decimal-pad"
                />

                {manualError && (
                  <View style={styles.inlineError}>
                    <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                    <Text style={styles.inlineErrorText}>{manualError}</Text>
                  </View>
                )}

                <TouchableOpacity
                  style={[styles.predictBtn, phase === 'analyzing' && styles.predictBtnCancel]}
                  onPress={phase === 'analyzing' ? reset : handleWtaAnalyze}
                  activeOpacity={0.85}
                >
                  {phase === 'analyzing' ? (
                    <>
                      <Ionicons name="close-circle-outline" size={16} color="#fff" />
                      <Text style={[styles.predictBtnText, { color: '#fff' }]}>Cancel</Text>
                    </>
                  ) : (
                    <>
                      <Ionicons name="analytics-outline" size={16} color="#000" />
                      <Text style={styles.predictBtnText}>Analyze</Text>
                    </>
                  )}
                </TouchableOpacity>
              </>
            )}
          </View>
        )}

        {/* ─── NBA MANUAL FORM ─── */}
        {sport === 'nba' && phase !== 'result' && phase !== 'saved' && (
          <View style={styles.manualForm}>
            <>
                <Text style={styles.fieldLabel}>Player</Text>
                <FuzzySearchInput
                  searchType="nba_players"
                  value={nbaPlayerQuery}
                  onChangeText={(t) => { setNbaPlayerQuery(t); if (!t) { setNbaResolvedPlayer(null); setNbaNextMatch(null); setNbaOpponentQuery(''); setNbaVenue('home'); } }}
                  placeholder="e.g. LeBron James, Stephen Curry"
                  confirmed={!!nbaResolvedPlayer}
                  autoCapitalize="words"
                  onSelectNbaPlayer={async (p) => {
                    setNbaResolvedPlayer(p);
                    setNbaPlayerQuery(p.fullName || `${p.firstName || ''} ${p.lastName || ''}`.trim());
                    setNbaNextMatch(null);
                    setNbaOpponentQuery('');
                    Haptics.selectionAsync();
                    if (p.id) {
                      setNbaNextMatchLoading(true);
                      try {
                        const nm = await getNbaNextMatch(p.id);
                        setNbaNextMatch(nm);
                        if (nm.found) {
                          if (nm.opponent?.name) setNbaOpponentQuery(nm.opponent.name);
                          if (nm.venue) setNbaVenue(nm.venue);
                        }
                      } catch { /* silent */ } finally { setNbaNextMatchLoading(false); }
                    }
                  }}
                />
                {nbaResolvedPlayer && (
                  <TouchableOpacity onPress={() => { setNbaPlayerQuery(''); setNbaResolvedPlayer(null); setNbaNextMatch(null); setNbaOpponentQuery(''); setNbaVenue('home'); Haptics.selectionAsync(); }} style={styles.changePlayerBtn}>
                    <Ionicons name="arrow-undo-outline" size={12} color={Colors.primary} />
                    <Text style={styles.changePlayerBtnText}>Change player</Text>
                  </TouchableOpacity>
                )}
                {nbaNextMatchLoading && (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 }}>
                    <ActivityIndicator size="small" color={Colors.primary} />
                    <Text style={{ color: Colors.textSecondary, fontSize: 12 }}>Fetching next game…</Text>
                  </View>
                )}
                {nbaNextMatch?.found && !nbaNextMatchLoading && (
                  <View style={styles.autoFillBanner}>
                    <Ionicons name="flash" size={12} color={Colors.primary} />
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: Colors.primary, fontSize: 11, fontWeight: '700', letterSpacing: 0.5 }}>
                        NEXT GAME AUTO-FILLED
                      </Text>
                      <Text style={{ color: Colors.text, fontSize: 13, fontWeight: '600', marginTop: 1 }}>vs {nbaNextMatch.opponent?.name}</Text>
                      <Text style={{ color: Colors.textSecondary, fontSize: 11, marginTop: 1 }}>{nbaNextMatch.venue?.toUpperCase()}{nbaNextMatch.date ? ` · ${new Date(nbaNextMatch.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : ''}</Text>
                    </View>
                  </View>
                )}
                {nbaResolvedPlayer && !nbaNextMatch?.found && !nbaNextMatchLoading && (
                  <>
                    <Text style={styles.fieldLabel}>Opponent <Text style={styles.fieldLabelOpt}>(optional)</Text></Text>
                    <TextInput
                      style={[styles.textInput, INPUT_STYLE]}
                      placeholder="e.g. Los Angeles Lakers"
                      placeholderTextColor={Colors.textTertiary}
                      value={nbaOpponentQuery}
                      onChangeText={setNbaOpponentQuery}
                      autoCapitalize="words"
                    />
                    <Text style={styles.fieldLabel}>Venue</Text>
                    <View style={{ flexDirection: 'row', gap: 10 }}>
                      {(['home', 'away'] as const).map(v => (
                        <TouchableOpacity key={v} style={[styles.pickerBtn, { flex: 1, justifyContent: 'center' }, nbaVenue === v && { borderColor: Colors.primary }]} onPress={() => { setNbaVenue(v); Haptics.selectionAsync(); }}>
                          <Text style={[styles.pickerBtnText, nbaVenue === v && { color: Colors.primary }]}>{v.toUpperCase()}</Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                  </>
                )}
                {nbaResolvedPlayer && (
                  <>
                    <Text style={styles.fieldLabel}>Prop Type</Text>
                    <TouchableOpacity style={styles.pickerBtn} onPress={() => setNbaShowPropPicker(true)}>
                      <Text style={styles.pickerBtnText}>{NBA_PROP_TYPES.find(p => p.value === nbaPropType)?.label || 'Select'}</Text>
                      <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
                    </TouchableOpacity>
                    <Text style={styles.fieldLabel}>Line Value</Text>
                    <TextInput
                      style={[styles.textInput, INPUT_STYLE]}
                      placeholder="e.g. 24.5"
                      placeholderTextColor={Colors.textTertiary}
                      value={line}
                      onChangeText={setLine}
                      keyboardType="decimal-pad"
                    />
                    {manualError && (
                      <View style={styles.inlineError}>
                        <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                        <Text style={styles.inlineErrorText}>{manualError}</Text>
                      </View>
                    )}
                    <TouchableOpacity style={[styles.predictBtn, phase === 'analyzing' && styles.predictBtnCancel]} onPress={phase === 'analyzing' ? reset : handleNbaAnalyze} activeOpacity={0.85}>
                      {phase === 'analyzing' ? (
                        <><Ionicons name="close-circle-outline" size={16} color="#fff" /><Text style={[styles.predictBtnText, { color: '#fff' }]}>Cancel</Text></>
                      ) : (
                        <><Ionicons name="analytics-outline" size={16} color="#000" /><Text style={styles.predictBtnText}>Analyze</Text></>
                      )}
                    </TouchableOpacity>
                  </>
                )}
              </>
          </View>
        )}

        {/* ─── NHL MANUAL FORM ─── */}
        {sport === 'nhl' && phase !== 'result' && phase !== 'saved' && (
          <View style={styles.manualForm}>
            <>
                <Text style={styles.fieldLabel}>Player</Text>
                <FuzzySearchInput
                  searchType="nhl_players"
                  value={nhlPlayerQuery}
                  onChangeText={(t) => { setNhlPlayerQuery(t); if (!t) { setNhlResolvedPlayer(null); setNhlNextMatch(null); setNhlOpponentQuery(''); setNhlVenue('home'); } }}
                  placeholder="e.g. Connor McDavid, Nathan MacKinnon"
                  confirmed={!!nhlResolvedPlayer}
                  autoCapitalize="words"
                  onSelectNhlPlayer={async (p) => {
                    setNhlResolvedPlayer(p);
                    setNhlPlayerQuery(p.fullName || `${p.firstName || ''} ${p.lastName || ''}`.trim());
                    setNhlNextMatch(null);
                    setNhlOpponentQuery('');
                    Haptics.selectionAsync();
                    if (p.id) {
                      setNhlNextMatchLoading(true);
                      try {
                        const nm = await getNhlNextMatch(p.id);
                        setNhlNextMatch(nm);
                        if (nm.found) {
                          if (nm.opponent?.name) setNhlOpponentQuery(nm.opponent.name);
                          if (nm.venue) setNhlVenue(nm.venue);
                        }
                      } catch { /* silent */ } finally { setNhlNextMatchLoading(false); }
                    }
                  }}
                />
                {nhlResolvedPlayer && (
                  <TouchableOpacity onPress={() => { setNhlPlayerQuery(''); setNhlResolvedPlayer(null); setNhlNextMatch(null); setNhlOpponentQuery(''); setNhlVenue('home'); Haptics.selectionAsync(); }} style={styles.changePlayerBtn}>
                    <Ionicons name="arrow-undo-outline" size={12} color={Colors.primary} />
                    <Text style={styles.changePlayerBtnText}>Change player</Text>
                  </TouchableOpacity>
                )}
                {nhlNextMatchLoading && (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 }}>
                    <ActivityIndicator size="small" color={Colors.primary} />
                    <Text style={{ color: Colors.textSecondary, fontSize: 12 }}>Fetching next game…</Text>
                  </View>
                )}
                {nhlNextMatch?.found && !nhlNextMatchLoading && (
                  <View style={styles.autoFillBanner}>
                    <Ionicons name="flash" size={12} color={Colors.primary} />
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: Colors.primary, fontSize: 11, fontWeight: '700', letterSpacing: 0.5 }}>
                        NEXT GAME AUTO-FILLED
                      </Text>
                      <Text style={{ color: Colors.text, fontSize: 13, fontWeight: '600', marginTop: 1 }}>vs {nhlNextMatch.opponent?.name}</Text>
                      <Text style={{ color: Colors.textSecondary, fontSize: 11, marginTop: 1 }}>{nhlNextMatch.venue?.toUpperCase()}{nhlNextMatch.date ? ` · ${new Date(nhlNextMatch.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : ''}</Text>
                    </View>
                  </View>
                )}
                {nhlResolvedPlayer && !nhlNextMatch?.found && !nhlNextMatchLoading && (
                  <>
                    <Text style={styles.fieldLabel}>Opponent <Text style={styles.fieldLabelOpt}>(optional)</Text></Text>
                    <TextInput
                      style={[styles.textInput, INPUT_STYLE]}
                      placeholder="e.g. Toronto Maple Leafs"
                      placeholderTextColor={Colors.textTertiary}
                      value={nhlOpponentQuery}
                      onChangeText={setNhlOpponentQuery}
                      autoCapitalize="words"
                    />
                    <Text style={styles.fieldLabel}>Venue</Text>
                    <View style={{ flexDirection: 'row', gap: 10 }}>
                      {(['home', 'away'] as const).map(v => (
                        <TouchableOpacity key={v} style={[styles.pickerBtn, { flex: 1, justifyContent: 'center' }, nhlVenue === v && { borderColor: Colors.primary }]} onPress={() => { setNhlVenue(v); Haptics.selectionAsync(); }}>
                          <Text style={[styles.pickerBtnText, nhlVenue === v && { color: Colors.primary }]}>{v.toUpperCase()}</Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                  </>
                )}
                {nhlResolvedPlayer && (
                  <>
                    <Text style={styles.fieldLabel}>Prop Type</Text>
                    <TouchableOpacity style={styles.pickerBtn} onPress={() => setNhlShowPropPicker(true)}>
                      <Text style={styles.pickerBtnText}>{NHL_PROP_TYPES.find(p => p.value === nhlPropType)?.label || 'Select'}</Text>
                      <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
                    </TouchableOpacity>
                    <Text style={styles.fieldLabel}>Line Value</Text>
                    <TextInput
                      style={[styles.textInput, INPUT_STYLE]}
                      placeholder="e.g. 0.5"
                      placeholderTextColor={Colors.textTertiary}
                      value={line}
                      onChangeText={setLine}
                      keyboardType="decimal-pad"
                    />
                    {manualError && (
                      <View style={styles.inlineError}>
                        <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                        <Text style={styles.inlineErrorText}>{manualError}</Text>
                      </View>
                    )}
                    <TouchableOpacity style={[styles.predictBtn, phase === 'analyzing' && styles.predictBtnCancel]} onPress={phase === 'analyzing' ? reset : handleNhlAnalyze} activeOpacity={0.85}>
                      {phase === 'analyzing' ? (
                        <><Ionicons name="close-circle-outline" size={16} color="#fff" /><Text style={[styles.predictBtnText, { color: '#fff' }]}>Cancel</Text></>
                      ) : (
                        <><Ionicons name="analytics-outline" size={16} color="#000" /><Text style={styles.predictBtnText}>Analyze</Text></>
                      )}
                    </TouchableOpacity>
                  </>
                )}
              </>
          </View>
        )}

        {/* ─── NFL MANUAL FORM ─── */}
        {sport === 'nfl' && phase !== 'result' && phase !== 'saved' && (
          <View style={styles.manualForm}>
            <>
                {nflPendingPlayer && !nflResolvedPlayer && (
                  <View style={styles.playerConfirmCard}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.playerConfirmEyebrow}>CONFIRM PLAYER IDENTITY</Text>
                      <Text style={styles.playerConfirmName} numberOfLines={1}>
                        {nflPendingPlayer.fullName || `${nflPendingPlayer.firstName} ${nflPendingPlayer.lastName}`.trim()}
                      </Text>
                      <Text style={styles.playerConfirmTeam} numberOfLines={1}>
                        {nflPendingPlayer.team?.full_name || 'Team not listed'}{nflPendingPlayer.position ? ` · ${nflPendingPlayer.position}` : ''}
                      </Text>
                    </View>
                    <TouchableOpacity style={styles.playerConfirmBtn} onPress={() => confirmNflPlayer(nflPendingPlayer)} activeOpacity={0.8}>
                      <Ionicons name="checkmark" size={15} color="#000" />
                      <Text style={styles.playerConfirmBtnText}>Confirm</Text>
                    </TouchableOpacity>
                  </View>
                )}
                {nflResolvedPlayer && (
                  <View style={styles.sportTeamContext}>
                    <Ionicons name="checkmark-circle" size={14} color={Colors.primary} />
                    <Text style={styles.sportTeamContextText} numberOfLines={1}>
                      PLAYER CONFIRMED · {nflResolvedPlayer.team?.full_name || 'Team not listed'}
                      {nflResolvedPlayer.position ? ` · ${nflResolvedPlayer.position}` : ''}
                    </Text>
                  </View>
                )}
                {nflResolvedPlayer && (
                  <TouchableOpacity onPress={() => { setNflPlayerQuery(''); setNflPendingPlayer(null); setNflResolvedPlayer(null); setNflNextMatch(null); setNflOpponentQuery(''); setNflVenue('home'); Haptics.selectionAsync(); }} style={styles.changePlayerBtn}>
                    <Ionicons name="arrow-undo-outline" size={12} color={Colors.primary} />
                    <Text style={styles.changePlayerBtnText}>Change player</Text>
                  </TouchableOpacity>
                )}
                {nflNextMatchLoading && (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 }}>
                    <ActivityIndicator size="small" color={Colors.primary} />
                    <Text style={{ color: Colors.textSecondary, fontSize: 12 }}>Fetching next game…</Text>
                  </View>
                )}
                {nflNextMatch?.found && !nflNextMatchLoading && (
                  <View style={styles.autoFillBanner}>
                    <Ionicons name="flash" size={12} color={Colors.primary} />
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: Colors.primary, fontSize: 11, fontWeight: '700', letterSpacing: 0.5 }}>
                        NEXT GAME AUTO-FILLED{nflNextMatch.seasonType === 'preseason' ? ' · PRESEASON' : ''}
                      </Text>
                      <Text style={{ color: Colors.text, fontSize: 13, fontWeight: '600', marginTop: 1 }}>vs {nflNextMatch.opponent?.name}</Text>
                      <Text style={{ color: Colors.textSecondary, fontSize: 11, marginTop: 1 }}>{nflNextMatch.venue?.toUpperCase()}{nflNextMatch.date ? ` · ${new Date(nflNextMatch.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : ''}</Text>
                    </View>
                  </View>
                )}
                {nflResolvedPlayer && !nflNextMatch?.found && !nflNextMatchLoading && (
                  <>
                    <Text style={styles.fieldLabel}>Opponent <Text style={styles.fieldLabelOpt}>(optional)</Text></Text>
                    <TextInput
                      style={[styles.textInput, INPUT_STYLE]}
                      placeholder="e.g. Kansas City Chiefs"
                      placeholderTextColor={Colors.textTertiary}
                      value={nflOpponentQuery}
                      onChangeText={setNflOpponentQuery}
                      autoCapitalize="words"
                    />
                    <Text style={styles.fieldLabel}>Venue</Text>
                    <View style={{ flexDirection: 'row', gap: 10 }}>
                      {(['home', 'away'] as const).map(v => (
                        <TouchableOpacity key={v} style={[styles.pickerBtn, { flex: 1, justifyContent: 'center' }, nflVenue === v && { borderColor: Colors.primary }]} onPress={() => { setNflVenue(v); Haptics.selectionAsync(); }}>
                          <Text style={[styles.pickerBtnText, nflVenue === v && { color: Colors.primary }]}>{v.toUpperCase()}</Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                  </>
                )}
                {nflResolvedPlayer && (
                  <>
                    <Text style={styles.fieldLabel}>Prop Type</Text>
                    <TouchableOpacity style={styles.pickerBtn} onPress={() => setNflShowPropPicker(true)}>
                      <Text style={styles.pickerBtnText}>{NFL_PROP_TYPES.find(p => p.value === nflPropType)?.label || 'Select'}</Text>
                      <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
                    </TouchableOpacity>
                    <Text style={styles.fieldLabel}>Line Value</Text>
                    <TextInput
                      style={[styles.textInput, INPUT_STYLE]}
                      placeholder="e.g. 249.5"
                      placeholderTextColor={Colors.textTertiary}
                      value={line}
                      onChangeText={setLine}
                      keyboardType="decimal-pad"
                    />
                    {manualError && (
                      <View style={styles.inlineError}>
                        <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                        <Text style={styles.inlineErrorText}>{manualError}</Text>
                      </View>
                    )}
                    <TouchableOpacity style={[styles.predictBtn, phase === 'analyzing' && styles.predictBtnCancel]} onPress={phase === 'analyzing' ? reset : handleNflAnalyze} activeOpacity={0.85}>
                      {phase === 'analyzing' ? (
                        <><Ionicons name="close-circle-outline" size={16} color="#fff" /><Text style={[styles.predictBtnText, { color: '#fff' }]}>Cancel</Text></>
                      ) : (
                        <><Ionicons name="analytics-outline" size={16} color="#000" /><Text style={styles.predictBtnText}>Analyze</Text></>
                      )}
                    </TouchableOpacity>
                  </>
                )}
              </>
          </View>
        )}

        {/* ─── MLB MANUAL FORM ─── */}
        {sport === 'mlb' && phase !== 'result' && phase !== 'saved' && (
          <View style={styles.manualForm}>
            <>
                {mlbPendingPlayer && !mlbResolvedPlayer && (
                  <View style={styles.playerConfirmCard}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.playerConfirmEyebrow}>CONFIRM PLAYER IDENTITY</Text>
                      <Text style={styles.playerConfirmName} numberOfLines={1}>
                        {mlbPendingPlayer.fullName || `${mlbPendingPlayer.firstName} ${mlbPendingPlayer.lastName}`.trim()}
                      </Text>
                      <Text style={styles.playerConfirmTeam} numberOfLines={1}>
                        {mlbPendingPlayer.team?.full_name || 'Team not listed'}{mlbPendingPlayer.position ? ` · ${mlbPendingPlayer.position}` : ''}
                      </Text>
                    </View>
                    <TouchableOpacity style={styles.playerConfirmBtn} onPress={() => confirmMlbPlayer(mlbPendingPlayer)} activeOpacity={0.8}>
                      <Ionicons name="checkmark" size={15} color="#000" />
                      <Text style={styles.playerConfirmBtnText}>Confirm</Text>
                    </TouchableOpacity>
                  </View>
                )}
                {mlbResolvedPlayer && (
                  <View style={styles.sportTeamContext}>
                    <Ionicons name="checkmark-circle" size={14} color={Colors.primary} />
                    <Text style={styles.sportTeamContextText} numberOfLines={1}>
                      PLAYER CONFIRMED · {mlbResolvedPlayer.team?.full_name || 'Team not listed'}
                      {mlbResolvedPlayer.position ? ` · ${mlbResolvedPlayer.position}` : ''}
                    </Text>
                  </View>
                )}
                {mlbResolvedPlayer && (
                  <TouchableOpacity onPress={() => { setMlbPlayerQuery(''); setMlbPendingPlayer(null); setMlbResolvedPlayer(null); setMlbNextMatch(null); setMlbOpponentQuery(''); setMlbVenue('home'); Haptics.selectionAsync(); }} style={styles.changePlayerBtn}>
                    <Ionicons name="arrow-undo-outline" size={12} color={Colors.primary} />
                    <Text style={styles.changePlayerBtnText}>Change player</Text>
                  </TouchableOpacity>
                )}
                {mlbNextMatchLoading && (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 }}>
                    <ActivityIndicator size="small" color={Colors.primary} />
                    <Text style={{ color: Colors.textSecondary, fontSize: 12 }}>Fetching next game…</Text>
                  </View>
                )}
                {mlbNextMatch?.found && !mlbNextMatchLoading && (
                  <View style={styles.autoFillBanner}>
                    <Ionicons name="flash" size={12} color={Colors.primary} />
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: Colors.primary, fontSize: 11, fontWeight: '700', letterSpacing: 0.5 }}>NEXT GAME AUTO-FILLED</Text>
                      <Text style={{ color: Colors.text, fontSize: 13, fontWeight: '600', marginTop: 1 }}>vs {mlbNextMatch.opponent?.name}</Text>
                      <Text style={{ color: Colors.textSecondary, fontSize: 11, marginTop: 1 }}>{mlbNextMatch.venue?.toUpperCase()}{mlbNextMatch.date ? ` · ${new Date(mlbNextMatch.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : ''}</Text>
                    </View>
                  </View>
                )}
                {mlbResolvedPlayer && !mlbNextMatch?.found && !mlbNextMatchLoading && (
                  <>
                    <Text style={styles.fieldLabel}>Opponent <Text style={styles.fieldLabelOpt}>(optional)</Text></Text>
                    <TextInput
                      style={[styles.textInput, INPUT_STYLE]}
                      placeholder="e.g. New York Yankees"
                      placeholderTextColor={Colors.textTertiary}
                      value={mlbOpponentQuery}
                      onChangeText={setMlbOpponentQuery}
                      autoCapitalize="words"
                    />
                    <Text style={styles.fieldLabel}>Venue</Text>
                    <View style={{ flexDirection: 'row', gap: 10 }}>
                      {(['home', 'away'] as const).map(v => (
                        <TouchableOpacity key={v} style={[styles.pickerBtn, { flex: 1, justifyContent: 'center' }, mlbVenue === v && { borderColor: Colors.primary }]} onPress={() => { setMlbVenue(v); Haptics.selectionAsync(); }}>
                          <Text style={[styles.pickerBtnText, mlbVenue === v && { color: Colors.primary }]}>{v.toUpperCase()}</Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                  </>
                )}
                {mlbResolvedPlayer && (
                  <>
                    <Text style={styles.fieldLabel}>Prop Type</Text>
                    <TouchableOpacity style={styles.pickerBtn} onPress={() => setMlbShowPropPicker(true)}>
                      <Text style={styles.pickerBtnText}>{MLB_PROP_TYPES.find(p => p.value === mlbPropType)?.label || 'Select'}</Text>
                      <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
                    </TouchableOpacity>
                    <Text style={styles.fieldLabel}>Line Value</Text>
                    <TextInput
                      style={[styles.textInput, INPUT_STYLE]}
                      placeholder="e.g. 1.5"
                      placeholderTextColor={Colors.textTertiary}
                      value={line}
                      onChangeText={setLine}
                      keyboardType="decimal-pad"
                    />
                    {manualError && (
                      <View style={styles.inlineError}>
                        <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                        <Text style={styles.inlineErrorText}>{manualError}</Text>
                      </View>
                    )}
                    <TouchableOpacity style={[styles.predictBtn, phase === 'analyzing' && styles.predictBtnCancel]} onPress={phase === 'analyzing' ? reset : handleMlbAnalyze} activeOpacity={0.85}>
                      {phase === 'analyzing' ? (
                        <><Ionicons name="close-circle-outline" size={16} color="#fff" /><Text style={[styles.predictBtnText, { color: '#fff' }]}>Cancel</Text></>
                      ) : (
                        <><Ionicons name="analytics-outline" size={16} color="#000" /><Text style={styles.predictBtnText}>Analyze</Text></>
                      )}
                    </TouchableOpacity>
                  </>
                )}
              </>
          </View>
        )}

        {/* ─── RESULT: Full Analysis ─── */}
        {phase === 'result' && prediction && (
          <>
            <Reanimated.View entering={Platform.OS !== 'web' ? FadeInDown.springify().damping(14).stiffness(100).delay(50) : undefined}>
            <View ref={analysisRef} collapsable={false} style={styles.captureContainer}>
            <View style={styles.analysisCard}>
              <LinearGradient
                colors={prediction.recommendation === 'OVER'
                  ? ['rgba(57,255,20,0.14)', 'rgba(57,255,20,0.02)', 'rgba(0,0,0,0)']
                  : ['rgba(255,59,48,0.14)', 'rgba(255,59,48,0.02)', 'rgba(0,0,0,0)']}
                start={{ x: 0.15, y: 0 }}
                end={{ x: 0.85, y: 0.7 }}
                style={styles.glassSheenTop}
                pointerEvents="none"
              />
              <View style={[styles.glassHairline, {
                backgroundColor: prediction.recommendation === 'OVER' ? Colors.primary : Colors.error,
              }]} pointerEvents="none" />
              {/* Top accent stripe — color signals OVER/UNDER at a glance */}
              <View style={[styles.analysisAccentStripe, {
                backgroundColor: prediction.recommendation === 'OVER' ? Colors.primary : Colors.error,
              }]} />
              {/* Header */}
              <View style={styles.analysisHeader}>
                 {(prediction.ownerPlayerPhoto || prediction.ownerTeamLogo || prediction.ownerOpponentLogo) && (
                   <View style={styles.ownerMediaStrip}>
                     {prediction.ownerPlayerPhoto ? (
                       <Image
                         source={{ uri: prediction.ownerPlayerPhoto }}
                         style={styles.analysisPlayerPhoto}
                       />
                     ) : null}
                     {prediction.ownerTeamLogo ? (
                       <Image
                         source={{ uri: prediction.ownerTeamLogo }}
                         style={styles.analysisTeamLogo}
                       />
                     ) : null}
                     {prediction.ownerOpponentLogo ? (
                       <Image
                         source={{ uri: prediction.ownerOpponentLogo }}
                         style={styles.analysisTeamLogo}
                       />
                     ) : null}
                   </View>
                 )}
                <View style={styles.analysisPlayerInfo}>
                  <Text style={styles.analysisPlayer} numberOfLines={1}>
                    {prediction.playerName}
                  </Text>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                    <Text style={styles.analysisTeam} numberOfLines={1}>
                      {[prediction.teamName, prediction.opponentName ? `vs ${prediction.opponentName}` : ''].filter(Boolean).join('  ·  ')}
                    </Text>
                    {prediction.currentOppTier && (() => {
                      const tierCfg: Record<string, { color: string; icon: string }> = {
                        ELITE:  { color: '#FF4444', icon: '⬛' },
                        STRONG: { color: '#FF8C00', icon: '⬛' },
                        MID:    { color: '#FFCC00', icon: '⬛' },
                        WEAK:   { color: '#39FF14', icon: '⬛' },
                      };
                      const cfg = tierCfg[prediction.currentOppTier] || { color: '#888', icon: '⬛' };
                      return (
                        <View style={{
                          backgroundColor: cfg.color + '22',
                          borderRadius: 4,
                          paddingHorizontal: 6,
                          paddingVertical: 2,
                          borderWidth: 1,
                          borderColor: cfg.color + '55',
                        }}>
                          <Text style={{ fontSize: 8, color: cfg.color, fontWeight: '800', letterSpacing: 0.8 }}>
                            {prediction.currentOppTier}
                          </Text>
                        </View>
                      );
                    })()}
                  </View>
                  <Text style={styles.analysisVenue}>
                    {venueOverride.toUpperCase()} · {PROP_LABELS[prediction.propType || ''] || prediction.propType}
                    {prediction.playerPosition ? `  ·  ${prediction.playerPosition}` : ''}
                    {prediction.playerRole ? ` (${prediction.playerRole})` : ''}
                  </Text>
                  {prediction.matchContext && (prediction.matchContext.league || prediction.matchContext.round) && (
                    <Text style={styles.matchContextText} numberOfLines={1}>
                      {[prediction.matchContext.league, prediction.matchContext.round, prediction.matchContext.date].filter(Boolean).join('  ·  ')}
                    </Text>
                  )}
                  {/* Player disambiguation warning — shown when multiple players share the same abbreviated name */}
                  {prediction.playerCandidates && prediction.playerCandidates.length > 1 &&
                    prediction.playerCandidates.some(c => c.playerId !== prediction.playerId) && (
                    <View>
                      <TouchableOpacity
                        onPress={() => setShowAltPlayers(v => !v)}
                        style={{ flexDirection: 'row', alignItems: 'center', marginTop: 6, gap: 4 }}
                        activeOpacity={0.7}
                      >
                        <Ionicons name="warning-outline" size={12} color="#f0a500" />
                        <Text style={{ color: '#f0a500', fontSize: 11 }}>
                          {prediction.playerCandidates.length} players share this name — tap to verify
                        </Text>
                        <Ionicons name={showAltPlayers ? 'chevron-up' : 'chevron-down'} size={11} color="#f0a500" />
                      </TouchableOpacity>
                      {showAltPlayers && (
                        <View style={{ marginTop: 4, backgroundColor: '#1a1a00', borderRadius: 6, padding: 8, gap: 4 }}>
                          {prediction.playerCandidates.map((c, i) => {
                            const isCurrent = c.playerId === prediction.playerId;
                            return (
                              <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                                {isCurrent
                                  ? <Ionicons name="checkmark-circle" size={12} color={Colors.primary} />
                                  : <Ionicons name="ellipse-outline" size={12} color="#555" />}
                                <Text style={{ color: isCurrent ? Colors.primary : '#888', fontSize: 11, flex: 1 }}>
                                  {c.teamName}{c.position ? ` · ${c.position}` : ''}
                                </Text>
                                {!isCurrent && (
                                  <TouchableOpacity
                                    onPress={async () => {
                                      if (!predictionRequest) return;
                                      setPhase('analyzing');
                                      setShowAltPlayers(false);
                                      try {
                                        const req = { ...predictionRequest, playerId: c.playerId, teamName: c.teamName, teamId: undefined, sport: sport };
                                        const result = await predict(req);
                                        if (!result.error) {
                                          setPrediction(result);
                                          setPredictionRequest(req);
                                        }
                                        setPhase('result');
                                      } catch { setPhase('result'); }
                                    }}
                                    style={{ backgroundColor: '#333', borderRadius: 4, paddingHorizontal: 8, paddingVertical: 3 }}
                                  >
                                    <Text style={{ color: '#ccc', fontSize: 10 }}>Use this</Text>
                                  </TouchableOpacity>
                                )}
                              </View>
                            );
                          })}
                        </View>
                      )}
                    </View>
                  )}
                </View>
                {prediction.recommendation && (
                  <LinearGradient
                    colors={prediction.recommendation === 'OVER'
                      ? ['rgba(57,255,20,0.28)', 'rgba(57,255,20,0.08)']
                      : ['rgba(255,59,48,0.28)', 'rgba(255,59,48,0.08)']}
                    start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                    style={[styles.recBadge, { borderWidth: 1, borderColor: recColor + '55' }]}
                  >
                    <Text style={[styles.recText, { color: recColor }]}>{prediction.recommendation}</Text>
                  </LinearGradient>
                )}
              </View>

              <View style={styles.analysisDivider} />

              {/* Verified fixture context belongs at the top of the card, before
                  the model evidence and history. */}
              {(prediction.moneyline || prediction.expectedGameType || prediction.gameScript?.dominant) && (
                <View style={{ paddingHorizontal: 14, paddingVertical: 9, borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7, marginBottom: 6 }}>
                    <Ionicons name="globe-outline" size={12} color={Colors.textSecondary} />
                    <Text style={{ fontSize: 9, color: Colors.textSecondary, fontWeight: '800', letterSpacing: 1 }}>MATCH CONTEXT</Text>
                    {prediction.expectedGameType ? (
                      <View style={{ marginLeft: 'auto', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: Colors.primary + '18', borderWidth: 1, borderColor: Colors.primary + '33' }}>
                        <Text style={{ fontSize: 8, color: Colors.primary, fontWeight: '800', letterSpacing: 0.6 }}>
                          {prediction.expectedGameType.toUpperCase()}
                        </Text>
                      </View>
                    ) : null}
                  </View>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    {prediction.moneyline ? (
                      <>
                        <Text style={{ fontSize: 10, color: Colors.textTertiary }}>ML</Text>
                        <Text style={{ fontSize: 10, color: Colors.text, fontWeight: '700' }}>
                          {String(prediction.homeTeam || 'HOME').split(' ').pop()} {prediction.moneyline.home}
                        </Text>
                        {prediction.moneyline.draw ? <Text style={{ fontSize: 10, color: Colors.textTertiary }}>D {prediction.moneyline.draw}</Text> : null}
                        <Text style={{ fontSize: 10, color: Colors.text, fontWeight: '700' }}>
                          {String(prediction.awayTeam || 'AWAY').split(' ').pop()} {prediction.moneyline.away}
                        </Text>
                      </>
                    ) : null}
                    {prediction.gameScript?.dominant ? (
                      <Text style={{ fontSize: 10, color: Colors.textSecondary, marginLeft: prediction.moneyline ? 3 : 0 }}>
                        SCRIPT · {String(prediction.gameScript.dominant).replace(/_/g, ' ').toUpperCase()}
                      </Text>
                    ) : null}
                  </View>
                </View>
              )}

              {/* Edge & Safety Rating Banner */}
              {prediction.edgeRating && prediction.recommendation !== 'PASS' && (() => {
                const EDGE_CFG: Record<string, { color: string; icon: string; bg: string }> = {
                  'SHARP EDGE': { color: '#39FF14', icon: 'flash',                 bg: 'rgba(57,255,20,0.10)' },
                  'EDGE':       { color: '#7CFF50', icon: 'trending-up',           bg: 'rgba(124,255,80,0.08)' },
                  'MARGINAL':   { color: '#FFA500', icon: 'remove-circle-outline', bg: 'rgba(255,165,0,0.08)' },
                  'NO EDGE':    { color: '#666666', icon: 'remove',                bg: 'rgba(102,102,102,0.08)' },
                };
                const SAFETY_CFG: Record<string, { color: string }> = {
                  'SAFE':     { color: '#39FF14' },
                  'MODERATE': { color: '#FFA500' },
                  'RISKY':    { color: '#FF6B35' },
                  'AVOID':    { color: '#FF3B30' },
                };
                const ec  = EDGE_CFG[prediction.edgeRating] ?? EDGE_CFG['NO EDGE'];
                const sr  = prediction.safetyRating ?? 'RISKY';
                const sc  = SAFETY_CFG[sr] ?? { color: '#888' };
                const er  = prediction.edgeRating ?? 'NO EDGE';
                const rate = prediction.propHistoricalRate;
                const n    = prediction.propHistoricalN ?? 0;
                const dir  = (prediction.recommendation ?? '').toUpperCase();
                const propLabel = PROP_LABELS[prediction.propType ?? ''] ?? prediction.propType ?? 'prop';
                const proj = prediction.projection ?? prediction.bayesianProjection ?? prediction.line ?? 0;
                const line = prediction.line ?? 0;
                const margin = Math.abs((proj as number) - (line as number));

                // Build a specific, plain-English reason for each combination
                let whyText = '';
                const histClause = rate != null && n > 0
                  ? `${propLabel} ${dir} hits ${rate}% from ${n} picks`
                  : null;
                 const linePct = line !== 0 ? (margin / Math.abs(line)) * 100 : null;
                 const marginClause = `${margin.toFixed(1)}-point projection gap`;
                 const edgeSnapshot = `Line ${line.toFixed(1)} · Projection ${Number(proj).toFixed(1)} · Gap ${margin.toFixed(1)}${linePct != null ? ` (${linePct.toFixed(1)}%)` : ''}`;

                if (er === 'SHARP EDGE') {
                  whyText = histClause
                    ? `${marginClause} · ${histClause} — strong, data-backed signal`
                    : `${marginClause} — model has strong conviction here`;
                } else if (er === 'EDGE') {
                  whyText = histClause
                    ? `${marginClause} with ${histClause} — solid edge`
                    : `${marginClause} — meaningful gap vs the book's line`;
                } else if (er === 'MARGINAL') {
                  whyText = histClause
                    ? `Thin ${marginClause} · ${histClause} — lean only`
                    : `${marginClause} — not enough separation for a confident play`;
                } else {
                  // NO EDGE — be specific about which reason killed it
                  if (sr === 'AVOID') {
                    whyText = histClause
                      ? `${propLabel} ${dir} wins only ${rate}% from ${n} picks — book has the edge on this prop`
                      : 'This prop+direction has a losing historical record — skip it';
                  } else if (sr === 'RISKY' && margin < 2) {
                    whyText = histClause
                      ? `Only ${marginClause} AND ${histClause} — two strikes against this pick`
                      : `Only ${marginClause} between projection and line — no real gap`;
                  } else if (sr === 'RISKY') {
                    whyText = histClause
                      ? `${histClause} — near coin flip, the margin alone can't save it`
                      : 'Historically unreliable prop+direction — not enough data edge';
                  } else if (margin < 2) {
                    whyText = `Only ${marginClause} between projection and line — book and model are basically agreeing`;
                  } else {
                    whyText = histClause
                      ? `Projection gap exists but ${histClause} — history limits conviction`
                      : `No clear structural advantage over the book here`;
                  }
                }

                // Accent color for the why-box: use safety color if no edge, edge color otherwise
                const whyColor = er === 'NO EDGE' || er === 'MARGINAL' ? sc.color : ec.color;

                return (
                  <View style={styles.edgeSafetyWrapper}>
                    {/* Pills row */}
                     <View style={styles.edgeSafetyBanner}>
                      <View style={[styles.edgeSafetyPill, { backgroundColor: ec.bg, borderColor: ec.color + '55' }]}>
                        <Ionicons name={ec.icon as any} size={11} color={ec.color} />
                        <Text style={[styles.edgeSafetyPillLabel, { color: Colors.textTertiary }]}>EDGE  </Text>
                        <Text style={[styles.edgeSafetyPillValue, { color: ec.color }]}>{er}</Text>
                      </View>
                      <View style={[styles.edgeSafetyPill, { backgroundColor: sc.color + '11', borderColor: sc.color + '55' }]}>
                        <Ionicons name="shield-outline" size={11} color={sc.color} />
                        <Text style={[styles.edgeSafetyPillLabel, { color: Colors.textTertiary }]}>HIST  </Text>
                        <Text style={[styles.edgeSafetyPillValue, { color: sc.color }]}>
                          {rate != null ? `${rate}%` : sr}{n > 0 ? ` (${n})` : ''}
                        </Text>
                      </View>
                    </View>
                     <Text style={{ fontSize: 10, color: whyColor, lineHeight: 15, marginTop: 7 }}>
                       {edgeSnapshot}
                     </Text>
                     <Text style={{ fontSize: 10, color: Colors.textSecondary, lineHeight: 15, marginTop: 2 }}>
                       {whyText}
                     </Text>
                  </View>
                );
              })()}

              {/* Stats Row */}
              <View style={styles.analysisStats}>
                <View style={styles.analysisStat}>
                  <Text style={styles.analysisStatLabel}>Line</Text>
                  <Text style={styles.analysisStatVal}>{prediction.line ?? '—'}</Text>
                  <Text style={styles.analysisStatSub}>SET</Text>
                </View>
                <View style={styles.analysisStatDivider} />
                <View style={styles.analysisStat}>
                  <Text style={styles.analysisStatLabel}>Projection</Text>
                  <Text style={[styles.analysisStatVal, { color: Colors.primary }]}>
                    {prediction.projection?.toFixed(1) ?? prediction.bayesianProjection?.toFixed(1) ?? '—'}
                  </Text>
                  <Text style={styles.analysisStatSub}>REVERSE FORMULA</Text>
                </View>
                <View style={styles.analysisStatDivider} />
                <View style={styles.analysisStat}>
                  <Text style={styles.analysisStatLabel}>
                    {modelProbPct != null ? 'Calibrated probability' : 'Evidence confidence'}
                  </Text>
                  <Text style={[styles.analysisStatVal, { color: recColor }]}>
                    {modelProbPct != null
                      ? `${modelProbPct.toFixed(1)}%`
                      : confPct != null ? `${confPct}%` : '—'}
                  </Text>
                  <Text style={styles.analysisStatSub}>
                    {modelProbPct != null ? 'FINAL MATH' : prediction.confidenceLevel?.toUpperCase() || 'SCORE'}
                  </Text>
                </View>
              </View>
              {modelProbPct != null && (
                <Text style={styles.probabilityExplainer}>
                  CALIBRATED PROBABILITY = the final chance of clearing the line after player history, matchup context, projection spread, and settled calibration. EVIDENCE confidence measures data quality, not the chance itself.
                </Text>
              )}

              {/* Confidence Gauge — visual meter 50%→100% */}
              {confPct != null && (
                <View style={styles.confGaugeWrap}>
                  <View style={styles.confGaugeTrack}>
                    <LinearGradient
                      colors={[recColor + '55', recColor]}
                      start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }}
                      style={[styles.confGaugeFill, {
                        width: `${Math.min(100, Math.max(0, (confPct - 50) * 2))}%` as any,
                        shadowColor: recColor,
                      }]}
                    />
                    <View style={styles.confGaugeMidMark} />
                  </View>
                  <View style={styles.confGaugeLabels}>
                    <Text style={styles.confGaugeLabelEdge}>50%</Text>
                    <Text style={[styles.confGaugeLabelCenter, { color: recColor }]}>
                      {confPct}% · EVIDENCE {prediction.confidenceLevel?.toUpperCase() || 'SCORE'}
                    </Text>
                    <Text style={styles.confGaugeLabelEdge}>100%</Text>
                  </View>
                </View>
              )}

              {/* Evidence Quality Callout — shown when confidence was capped due to limited data */}
              {(prediction as any).qualityConfidenceCapped && (() => {
                const eq = (prediction as any).evidenceQuality as Record<string, unknown> | undefined;
                const logCount = (eq?.realPlayerLogCount as number | undefined) ?? null;
                const capReasons = (eq?.capReasons as string[] | undefined) ?? [];
                const firstReason = capReasons[0] ?? 'Limited match data available.';
                return (
                  <View style={{
                    marginTop: 10, marginBottom: 4,
                    paddingHorizontal: 12, paddingVertical: 9,
                    backgroundColor: 'rgba(245,158,11,0.08)',
                    borderRadius: 10, borderWidth: 1,
                    borderColor: 'rgba(245,158,11,0.3)',
                    flexDirection: 'row', alignItems: 'center', gap: 8,
                  }}>
                    <Ionicons name="warning-outline" size={14} color="#F59E0B" />
                    <View style={{ flex: 1 }}>
                      <Text style={{ fontSize: 11, fontWeight: '800', color: '#F59E0B', letterSpacing: 0.4 }}>
                        EVIDENCE LIMITED{logCount != null ? ` · ${logCount} game log${logCount !== 1 ? 's' : ''}` : ''}
                      </Text>
                      <Text style={{ fontSize: 11, color: '#9CA3AF', marginTop: 2, lineHeight: 15 }}>
                        {firstReason} Confidence capped accordingly.
                      </Text>
                    </View>
                  </View>
                );
              })()}

               {/* Compact evidence ledger — the visible model context is numeric,
                   not a second prose explanation. */}
               <View style={{ paddingHorizontal: 14, paddingVertical: 10, borderTopWidth: 1, borderTopColor: Colors.borderSubtle }}>
                 <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                   {prediction.priorMean != null && (
                     <View style={styles.compactMetricChip}>
                       <Text style={styles.compactMetricLabel}>BASE</Text>
                       <Text style={styles.compactMetricValue}>{prediction.priorMean.toFixed(1)}</Text>
                     </View>
                   )}
                   {(prediction as any).opponentProfile?.allowedAvg != null && (
                     <View style={styles.compactMetricChip}>
                       <Text style={styles.compactMetricLabel}>OPP ALLOWS</Text>
                       <Text style={styles.compactMetricValue}>{Number((prediction as any).opponentProfile.allowedAvg).toFixed(1)}</Text>
                     </View>
                   )}
                   {prediction.h2hPlayerStats?.avgVsOpponent != null && (
                     <View style={styles.compactMetricChip}>
                       <Text style={styles.compactMetricLabel}>H2H AVG</Text>
                       <Text style={styles.compactMetricValue}>{Number(prediction.h2hPlayerStats.avgVsOpponent).toFixed(1)}</Text>
                     </View>
                   )}
                   {prediction.expectedPossession && Number.isFinite(prediction.expectedPossession.home) && Number.isFinite(prediction.expectedPossession.away) && (
                     <View style={styles.compactMetricChip}>
                       <Text style={styles.compactMetricLabel}>POSS</Text>
                       <Text style={styles.compactMetricValue}>
                         {Math.round(venueOverride === 'home' ? prediction.expectedPossession.home : prediction.expectedPossession.away)}%
                       </Text>
                     </View>
                   )}
                 </View>
                 <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
                   {prediction.priorSamples != null && (
                     <Text style={styles.compactInputText}>SAMPLE {prediction.priorSamples}</Text>
                   )}
                   {prediction.momentumLabel && (
                     <Text style={styles.compactInputText}>MOMENTUM {prediction.momentumLabel}</Text>
                   )}
                   {prediction.playerPosition && (
                     <Text style={styles.compactInputText}>{prediction.playerPosition.toUpperCase()}</Text>
                   )}
                   {prediction.currentOppTier && (
                     <Text style={styles.compactInputText}>OPP {prediction.currentOppTier}</Text>
                   )}
                 </View>
               </View>

               {/* The detailed script, formula, and tactical prose stay in the
                   deterministic payload but are not repeated in the customer UI. */}
               {false && (<>
              {/* Line vs Season Average + Edge Explanation — tactical verdict replaces
                  generic soccer prose so the customer sees one coherent read. */}
              {prediction.sport !== 'soccer' && prediction.priorMean != null && prediction.line != null && (() => {
                const pct = ((prediction.line - prediction.priorMean) / prediction.priorMean) * 100;
                const lineBelow = pct < 0;
                const absPct = Math.abs(pct).toFixed(1);
                const deltaColor = lineBelow ? Colors.success : Colors.error;
                const arrow = lineBelow ? '↓' : '↑';
                const band = prediction.lineDeviationBand;
                const bandAccent = band ? (BAND_ACCENT[band] ?? '#888') : null;
                const hitRate = prediction.lineDeviationHitRate;
                const rec = (prediction.recommendation ?? '').toUpperCase();
                const playerFirst = (prediction.playerName ?? 'This player').split(' ')[0];

                const PROP_AVG_LABEL: Record<string, string> = {
                  pass_attempts: 'passes per game', shots: 'shots per game',
                  shots_on_target: 'shots on target per game', saves: 'saves per game',
                  goals: 'goals per game', assists: 'assists per game',
                  key_passes: 'key passes per game', tackles: 'tackles per game',
                  crosses: 'crosses per game', dribbles: 'dribbles per game',
                  pitcher_strikeouts: 'strikeouts per start', innings_pitched: 'IP per start',
                  hits_allowed: 'hits allowed per start', earned_runs: 'ER per start',
                  walks_allowed: 'walks allowed per start', pitches_thrown: 'pitches per start',
                  batters_faced: 'batters faced per start',
                  hits: 'hits per game', home_runs: 'HR per game', rbi: 'RBI per game',
                  walks: 'walks per game', strikeouts: 'Ks per game', runs: 'runs per game',
                  total_bases: 'total bases per game', stolen_bases: 'SB per game',
                  doubles: 'doubles per game', plate_appearances: 'PA per game',
                  hitter_fantasy_points: 'DK pts per game',
                  hits_runs_rbis: 'H+R+RBI per game',
                  pitcher_fantasy_score: 'DK pts per start',
                  pitching_outs: 'outs per start',
                  maps_1_3_kills: 'kills across all 3 maps',
                  maps_1_3_headshots: 'headshots across all 3 maps',
                };
                const propAvgLabel = PROP_AVG_LABEL[prediction.propType ?? ''] ?? 'per game';

                const BAND_STRENGTH: Record<string, string> = {
                  mild: 'mild', moderate: 'significant', elevated: 'strong',
                  extreme: 'extreme', aligned: 'slight',
                };
                const bandStrength = band ? (BAND_STRENGTH[band] ?? '') : '';

                let edgeParagraph = '';
                if (lineBelow && rec === 'OVER') {
                  edgeParagraph = `The book set this line ${absPct}% below ${playerFirst}'s season average of ${prediction.priorMean.toFixed(1)} ${propAvgLabel}. That's a ${bandStrength} market underpricing — the sportsbook is essentially betting ${playerFirst} underperforms their own baseline. In ${band ?? ''} deviation cases like this where our model took the OVER, it has hit ${hitRate != null ? hitRate.toFixed(0) + '%' : 'at a strong rate'} historically. The edge is structural: any time a book sets the line meaningfully below a player's proven average, the math favors the over.`;
                } else if (!lineBelow && rec === 'UNDER') {
                  edgeParagraph = `The book set this line ${absPct}% above ${playerFirst}'s season average of ${prediction.priorMean.toFixed(1)} ${propAvgLabel}. When sportsbooks overprice a line like this — betting the player outperforms their own baseline — the data says that rarely holds. In ${band ?? ''} deviation UNDER cases, our model has hit ${hitRate != null ? hitRate.toFixed(0) + '%' : 'at a strong rate'} historically. The book is overreacting to recent form while ignoring the season trend.`;
                } else if (lineBelow && rec === 'UNDER') {
                  edgeParagraph = `The line is ${absPct}% below season average, yet our model still leans UNDER. This reflects momentum evidence — ${playerFirst}'s recent games have tracked below their season baseline, and the projection expects that trend to continue. The deviation means the book is already pricing in some weakness, but not enough.`;
                } else if (!lineBelow && rec === 'OVER') {
                  edgeParagraph = `The line is ${absPct}% above season average, yet our model sees OVER value. This is a high-conviction play — ${playerFirst}'s recent form and match context (possession, opponent profile) project production that justifies beating even an elevated line. ${hitRate != null ? `In similar cases, this has hit ${hitRate.toFixed(0)}%.` : ''}`;
                }

                return (
                  <>
                    <View style={styles.analysisDivider} />
                    <View style={styles.lineVsAvgRow}>
                      <View style={styles.lineVsAvgLeft}>
                        <Text style={styles.lineVsAvgLabel}>SEASON AVG</Text>
                        <Text style={styles.lineVsAvgVal}>{prediction.priorMean.toFixed(1)}</Text>
                      </View>
                      <View style={styles.lineVsAvgMid}>
                        <Text style={[styles.lineVsAvgDelta, { color: deltaColor }]}>
                          {arrow} {absPct}%
                        </Text>
                        <Text style={styles.lineVsAvgNote}>
                          line {absPct}% {lineBelow ? 'below' : 'above'} season avg
                        </Text>
                      </View>
                      {bandAccent && (
                        <View style={[styles.devBandPill, { borderColor: bandAccent + '66', backgroundColor: bandAccent + '15' }]}>
                          <Text style={[styles.devBandPillText, { color: bandAccent }]}>
                            {BAND_LABEL[band!] ?? (band ?? '').toUpperCase()}
                          </Text>
                          {hitRate != null && (
                            <Text style={[styles.devBandPillHit, { color: bandAccent }]}>
                              {hitRate.toFixed(0)}% hit
                            </Text>
                          )}
                        </View>
                      )}
                    </View>
                    {edgeParagraph.length > 0 && (
                      <View style={styles.edgeExplainBox}>
                        <View style={styles.edgeExplainHeader}>
                          <Ionicons name="trending-up" size={11} color={deltaColor} />
                          <Text style={[styles.edgeExplainTitle, { color: deltaColor }]}>WHY THIS MATTERS</Text>
                        </View>
                        <Text style={styles.edgeExplainBody}>{edgeParagraph}</Text>
                      </View>
                    )}
                  </>
                );
              })()}

              {/* ─── GAME SCRIPT BANNER — kept in the payload, hidden in the compact UI ─── */}
              {(() => {
                const gs = prediction.gameScript;
                if (!gs || !gs.dominant) return null;
                const color = gs.color || '#60A5FA';
                const iconMap: Record<string, string> = {
                  'low_scoring': 'shield', 'high_scoring': 'flame',
                  'open_close': 'analytics', 'home_blowout': 'trending-up',
                  'away_blowout': 'trending-down',
                };
                const icon = (iconMap[gs.dominant] || 'analytics') as any;
                return (
                  <Animated.View style={[styles.gsBanner, { borderColor: color + '55', display: 'none' }]}>
                    {/* Glow accent stripe */}
                    <View style={[styles.gsBannerStripe, { backgroundColor: color }]} />
                    <View style={styles.gsBannerBody}>
                      {/* Top row: label + icon + probability */}
                      <View style={styles.gsBannerHeader}>
                        <View style={[styles.gsBannerIconWrap, { backgroundColor: color + '22' }]}>
                          <Ionicons name={icon} size={16} color={color} />
                        </View>
                        <Text style={[styles.gsBannerLabel, { color }]}>GAME SCRIPT</Text>
                        <View style={[styles.gsBannerProbBadge, { backgroundColor: color + '22', borderColor: color + '44' }]}>
                          <Text style={[styles.gsBannerProb, { color }]}>{Math.round((gs.dominant_probability || 0) * 100)}%</Text>
                        </View>
                      </View>
                      {/* Big title — the script prediction */}
                      <Text style={[styles.gsBannerTitle, { color }]}>{gs.key_finding}</Text>
                      {/* Scenario chips — top 3 other scenarios */}
                      {gs.scenarios && gs.scenarios.length > 1 && (
                        <View style={styles.gsBannerScenarios}>
                          {gs.scenarios.slice(0, 3).map((s: any, i: number) => (
                            <View key={i} style={[styles.gsBannerChip, { borderColor: color + '33' }]}>
                              <Text style={styles.gsBannerChipName}>{s.name}</Text>
                              <Text style={[styles.gsBannerChipPct, { color }]}>{Math.round(s.probability * 100)}%</Text>
                            </View>
                          ))}
                        </View>
                      )}
                      {/* Bottom line: expected goals + implied win probs */}
                      {gs.expected_total_goals != null && (
                        <View style={styles.gsBannerBottom}>
                          <Text style={styles.gsBannerSub}>
                            Expected {gs.expected_total_goals} total goals
                          </Text>
                          <View style={styles.gsBannerImplied}>
                            <Text style={[styles.gsBannerImpliedText, { color }]}>{gs.implied_home ? `${Math.round((gs.implied_home as number) * 100)}%` : ''} home</Text>
                            <Text style={styles.gsBannerImpliedDivider}> / </Text>
                            <Text style={[styles.gsBannerImpliedText, { color }]}>{gs.implied_away ? `${Math.round((gs.implied_away as number) * 100)}%` : ''} away</Text>
                          </View>
                        </View>
                      )}
                    </View>
                  </Animated.View>
                );
              })()}

              {/* Data Quality Warning */}
              {prediction.dataQuality && prediction.dataQuality.level !== 'good' && prediction.dataQuality.message && (
                <View style={styles.dataQualityBanner}>
                  <Ionicons name="warning-outline" size={12} color="#F59E0B" />
                  <Text style={styles.dataQualityText}>{prediction.dataQuality.message}</Text>
                </View>
              )}

              {/* Confidence Interval */}
              {(() => {
                const range60 = prediction.range60 || prediction.distribution?.range60;
                const range80 = prediction.range80 || prediction.distribution?.range80 || prediction.confidenceInterval;
                const has60 = !!range60 && range60[1] > range60[0];
                const has80 = !!range80 && range80[1] > range80[0];
                if (!has60 && !has80) return null;
                return (
                <>
                  <View style={styles.analysisDivider} />
                  {has60 && (
                    <View style={styles.ciRow}>
                      <Text style={styles.ciLabel}>60% RANGE</Text>
                      <Text style={styles.ciVal}>
                        {range60![0].toFixed(1)} — {range60![1].toFixed(1)}
                      </Text>
                    </View>
                  )}
                  {has80 && (
                    <View style={styles.ciRow}>
                      <Text style={styles.ciLabel}>80% RANGE</Text>
                      <Text style={styles.ciVal}>
                        {range80![0].toFixed(1)} — {range80![1].toFixed(1)}
                      </Text>
                    </View>
                  )}
                </>
                );
              })()}

              {/* ── ALGORITHM BREAKDOWN CARD ──────────────────────────────── */}
              {(() => {
                const mf = (prediction as any).matchFactors;
                if (!mf) return null;
                const bm       = mf.bayesian ?? {};
                const ms       = mf.matchStakes ?? {};
                const stMod    = bm.matchStakes ?? {};
                const cdmInv   = bm.cdmInversion ?? {};
                const hcdb     = bm.homeCdmDeepBlock ?? {};
                const pos      = prediction.playerPosition ?? '';
                const priorM   = bm.priorMean as number | undefined;
                const postM    = bm.posteriorMean as number | undefined;
                const n        = bm.priorSamples as number | undefined;
                const pOver    = bm.pOver as number | undefined;
                const pUnder   = bm.pUnder as number | undefined;
                const expPoss  = mf.expectedPoss as number | undefined;
                const h2hPoss  = mf.h2hPossAvg as number | undefined;
                const h2hCnt   = mf.h2hPossCount as number | undefined;
                const possMulti = mf.possMultiplier as number | undefined;
                const stakeLabel = (ms.label ?? ms.teamStakeLevel) as string | undefined;
                const lcInfo   = bm.leagueCalib ?? {};
                const spInfo   = bm.scenarioPriors ?? {};
                const oppAvg   = bm.oppAllowedAvg as number | undefined;
                const oppWt    = bm.oppAllowedWeight as number | undefined;
                const rawOppAvg   = bm.rawOppAllowedAvg as number | undefined;
                const pairShare   = bm.pairShare as number | undefined;
                const compSeasAvg = bm.compSeasonAvg as number | undefined;
                const momLabel    = bm.momentumLabel as string | undefined;
                const momEff     = bm.momentumEffect as number | undefined;
                const rotRisk    = bm.rotationRisk as string | undefined;
                const rotAdjPct  = bm.rotationAdjPct as number | undefined;
                const expMins    = bm.expectedMinutes as number | undefined;
                const isOver     = pOver != null && pUnder != null && pOver >= pUnder;
                const stakeColor = stakeLabel?.includes('RELEGATION') ? '#FF6B35'
                                 : stakeLabel?.includes('DEAD')        ? '#666'
                                 : stakeLabel?.includes('TITLE')       ? '#F59E0B'
                                 : Colors.primary;

                // Build signal chain
                type ChainStep = { label: string; pct: number; color: string; n?: number };
                const chain: ChainStep[] = [];
                if (possMulti != null && Math.abs(possMulti - 1) > 0.01)
                  chain.push({ label: 'POSS', pct: (possMulti - 1) * 100, color: '#4DA6FF', n: h2hCnt });
                if (stMod?.mult != null && Math.abs(stMod.mult - 1) > 0.005)
                  chain.push({ label: 'STAKES', pct: (stMod.mult - 1) * 100, color: stMod.mult < 1 ? '#FF6B35' : Colors.primary });
                if (lcInfo?.multiplier != null && Math.abs(lcInfo.multiplier - 1) > 0.005)
                  chain.push({ label: 'LEAGUE', pct: (lcInfo.multiplier - 1) * 100, color: lcInfo.multiplier < 1 ? '#F59E0B' : Colors.primary, n: lcInfo.n });
                if (spInfo?.multiplier != null && Math.abs(spInfo.multiplier - 1) > 0.005)
                  chain.push({ label: 'SCEN', pct: (spInfo.multiplier - 1) * 100, color: spInfo.multiplier < 1 ? '#F59E0B' : Colors.primary, n: spInfo.n });
                if (cdmInv.applied) chain.push({ label: 'CDM INV', pct: (cdmInv.mult - 1) * 100, color: '#A084E8' });
                if (hcdb.applied)   chain.push({ label: 'DEEP BLK', pct: (hcdb.mult - 1) * 100, color: '#A084E8' });
                if (rotRisk && rotRisk !== 'stable' && rotAdjPct != null && rotAdjPct !== 0)
                  chain.push({ label: rotRisk === 'declining' ? 'ROTATION↓' : 'ROTATION↑', pct: rotAdjPct, color: rotRisk === 'declining' ? '#FF6B35' : Colors.primary });
                if (rawOppAvg != null && oppAvg != null && Math.abs(rawOppAvg - oppAvg) >= 0.5)
                  chain.push({ label: 'PAIR CAL', pct: ((oppAvg - rawOppAvg) / rawOppAvg) * 100, color: '#F59E0B' });

                // Extra modifiers
                const extraMods: string[] = [];
                if (stMod.applied && stMod.mult != null && !chain.find(c => c.label === 'STAKES'))
                  extraMods.push(`Stakes ×${stMod.mult}`);

                return (
                  <View>
                    <View style={styles.analysisDivider} />
                    <View style={[styles.mfCard, { display: 'none' }]}>

                      {/* ── Header ── */}
                      <View style={styles.mfHeader}>
                        <Ionicons name="analytics" size={13} color={Colors.primary} />
                        <Text style={styles.mfTitle}>ALGORITHM BREAKDOWN</Text>
                        {n != null && (
                          <View style={styles.mfSamplesBadge}>
                            <Text style={styles.mfSamplesText}>{n} GAMES</Text>
                          </View>
                        )}
                      </View>

                      {/* ── Probability Meter ── */}
                      {pOver != null && pUnder != null && (
                        <View style={styles.mfProbSection}>
                          <View style={styles.mfProbTrack}>
                            <View style={[styles.mfProbOverFill, { flex: pOver }]} />
                            <View style={styles.mfProbDivider} />
                            <View style={[styles.mfProbUnderFill, { flex: pUnder }]} />
                          </View>
                          <View style={styles.mfProbLabels}>
                            <View style={styles.mfProbLabelLeft}>
                              <Text style={[styles.mfProbPct, { color: Colors.primary }]}>{pOver.toFixed(1)}%</Text>
                              <Text style={styles.mfProbDir}>OVER</Text>
                            </View>
                            <Text style={styles.mfProbVs}>P( )</Text>
                            <View style={styles.mfProbLabelRight}>
                              <Text style={[styles.mfProbPct, { color: '#FF6B35' }]}>{pUnder.toFixed(1)}%</Text>
                              <Text style={styles.mfProbDir}>UNDER</Text>
                            </View>
                          </View>
                        </View>
                      )}

                      {/* ── Metrics Grid ── */}
                      <View style={styles.mfMetricsGrid}>
                        {priorM != null && (
                          <View style={styles.mfMetric}>
                            <Text style={styles.mfMetricLabel}>PRIOR</Text>
                            <Text style={styles.mfMetricVal}>{priorM.toFixed(1)}</Text>
                            <Text style={styles.mfMetricSub}>{pos || 'BASE'}</Text>
                          </View>
                        )}
                        {postM != null && (
                          <View style={[styles.mfMetric, styles.mfMetricHighlight, {
                            borderColor: (isOver ? Colors.primary : '#FF6B35') + '66',
                            backgroundColor: (isOver ? Colors.primary : '#FF6B35') + '0C',
                          }]}>
                            <Text style={styles.mfMetricLabel}>POSTERIOR</Text>
                            <Text style={[styles.mfMetricVal, { color: isOver ? Colors.primary : '#FF6B35' }]}>
                              {postM.toFixed(1)}
                            </Text>
                            {priorM != null && (
                              <Text style={[styles.mfMetricSub, {
                                color: postM > priorM ? Colors.primary : '#FF6B35',
                              }]}>
                                {postM > priorM ? '▲' : '▼'}{Math.abs(postM - priorM).toFixed(1)}
                              </Text>
                            )}
                          </View>
                        )}
                        {expPoss != null && (
                          <View style={styles.mfMetric}>
                            <Text style={styles.mfMetricLabel}>EXP POSS</Text>
                            <Text style={styles.mfMetricVal}>{Math.round(expPoss)}%</Text>
                            {h2hPoss != null && (
                              <Text style={styles.mfMetricSub}>H2H {Math.round(h2hPoss)}%</Text>
                            )}
                          </View>
                        )}
                        {oppAvg != null && (
                          <View style={styles.mfMetric}>
                            <Text style={styles.mfMetricLabel}>OPP ALLOWS</Text>
                            <Text style={[styles.mfMetricVal, { color: '#A084E8' }]}>{oppAvg.toFixed(1)}</Text>
                            {rawOppAvg != null && Math.abs(rawOppAvg - oppAvg) >= 0.5 ? (
                              <Text style={styles.mfMetricSub}>raw {rawOppAvg.toFixed(1)}</Text>
                            ) : oppWt != null ? (
                              <Text style={styles.mfMetricSub}>{oppWt}% wt</Text>
                            ) : null}
                          </View>
                        )}
                        {pairShare != null && compSeasAvg != null && (
                          <View style={styles.mfMetric}>
                            <Text style={styles.mfMetricLabel}>PAIR RANK</Text>
                            <Text style={[styles.mfMetricVal, { fontSize: 11,
                              color: pairShare < 0.82 ? '#F59E0B'
                                   : pairShare > 1.18 ? Colors.primary
                                   : Colors.textSecondary,
                            }]}>
                              {(pairShare * 100).toFixed(0)}%
                            </Text>
                            <Text style={styles.mfMetricSub}>of {compSeasAvg.toFixed(0)} avg</Text>
                          </View>
                        )}
                        {momLabel && (
                          <View style={styles.mfMetric}>
                            <Text style={styles.mfMetricLabel}>MOMENTUM</Text>
                            <Text style={[styles.mfMetricVal, { fontSize: 11,
                              color: momLabel === 'HOT' ? '#FF8C42' : momLabel === 'COLD' ? '#60A5FA' : Colors.textSecondary,
                            }]}>{momLabel}</Text>
                            {momEff != null && momEff !== 0 && (
                              <Text style={[styles.mfMetricSub, { color: momEff > 0 ? Colors.primary : '#FF6B35' }]}>
                                {momEff > 0 ? '+' : ''}{momEff.toFixed(1)}
                              </Text>
                            )}
                          </View>
                        )}
                        {rotRisk != null && rotRisk !== 'stable' && (
                          <View style={styles.mfMetric}>
                            <Text style={styles.mfMetricLabel}>ROTATION</Text>
                            <Text style={[styles.mfMetricVal, { fontSize: 10,
                              color: rotRisk === 'declining' ? '#FF6B35' : Colors.primary,
                            }]}>{rotRisk === 'declining' ? 'DECLINING' : 'RETURNING'}</Text>
                            {expMins != null && (
                              <Text style={styles.mfMetricSub}>{expMins.toFixed(0)} min</Text>
                            )}
                          </View>
                        )}
                        {lcInfo?.n != null && (
                          <View style={styles.mfMetric}>
                            <Text style={styles.mfMetricLabel}>LEAGUE CAL</Text>
                            <Text style={[styles.mfMetricVal, { fontSize: 11,
                              color: lcInfo.multiplier < 1 ? '#F59E0B' : Colors.primary,
                            }]}>
                              {lcInfo.multiplier >= 1 ? '+' : ''}{((lcInfo.multiplier - 1) * 100).toFixed(1)}%
                            </Text>
                            <Text style={styles.mfMetricSub}>n={lcInfo.n}</Text>
                          </View>
                        )}
                      </View>

                      {/* ── Signal Chain ── */}
                      {priorM != null && postM != null && chain.length > 0 && (
                        <View style={styles.mfChainSection}>
                          <Text style={styles.mfChainTitle}>SIGNAL CHAIN</Text>
                          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                            <View style={styles.mfChainRow}>
                              <View style={styles.mfChainNode}>
                                <Text style={styles.mfChainNodeNum}>{priorM.toFixed(1)}</Text>
                                <Text style={styles.mfChainNodeSub}>PRIOR</Text>
                              </View>
                              {chain.map((s, i) => (
                                <React.Fragment key={i}>
                                  <Text style={styles.mfChainArrow}>›</Text>
                                  <View style={[styles.mfChainStep, {
                                    borderColor: s.color + '66',
                                    backgroundColor: s.color + '14',
                                  }]}>
                                    <Text style={[styles.mfChainStepLabel, { color: s.color }]}>{s.label}</Text>
                                    <Text style={[styles.mfChainStepPct, { color: s.color }]}>
                                      {s.pct >= 0 ? '+' : ''}{s.pct.toFixed(1)}%
                                    </Text>
                                    {s.n != null && <Text style={styles.mfChainStepN}>n={s.n}</Text>}
                                  </View>
                                </React.Fragment>
                              ))}
                              <Text style={styles.mfChainArrow}>›</Text>
                              <View style={[styles.mfChainNode, {
                                borderColor: (isOver ? Colors.primary : '#FF6B35') + '66',
                                backgroundColor: (isOver ? Colors.primary : '#FF6B35') + '10',
                              }]}>
                                <Text style={[styles.mfChainNodeNum, { color: isOver ? Colors.primary : '#FF6B35' }]}>
                                  {postM.toFixed(1)}
                                </Text>
                                <Text style={styles.mfChainNodeSub}>FINAL</Text>
                              </View>
                            </View>
                          </ScrollView>
                        </View>
                      )}

                      {/* ── Match Stakes Banner ── */}
                      {stakeLabel && stakeLabel !== 'NORMAL' && stakeLabel !== 'NORMAL_STAKES' && (
                        <View style={[styles.mfStakeBanner, {
                          borderColor: stakeColor + '55',
                          backgroundColor: stakeColor + '0E',
                        }]}>
                          <Ionicons name="warning-outline" size={11} color={stakeColor} />
                          <View style={{ flex: 1 }}>
                            <Text style={[styles.mfStakeBannerLabel, { color: stakeColor }]}>
                              {stakeLabel.replace(/_/g, ' ')}
                            </Text>
                            {stMod.reason ? (
                              <Text style={styles.mfStakeBannerReason}>{stMod.reason}</Text>
                            ) : null}
                          </View>
                          {stMod.mult != null && Math.abs(stMod.mult - 1) > 0.005 && (
                            <Text style={[styles.mfStakeBannerMult, { color: stakeColor }]}>
                              {stMod.mult >= 1 ? '+' : ''}{((stMod.mult - 1) * 100).toFixed(0)}%
                            </Text>
                          )}
                        </View>
                      )}

                      {/* ── Rotation Risk Banner ── */}
                      {rotRisk && rotRisk !== 'stable' && rotAdjPct != null && rotAdjPct !== 0 && (
                        <View style={[styles.mfStakeBanner, {
                          borderColor: (rotRisk === 'declining' ? '#FF6B35' : Colors.primary) + '55',
                          backgroundColor: (rotRisk === 'declining' ? '#FF6B35' : Colors.primary) + '0E',
                          marginTop: 8,
                        }]}>
                          <Ionicons
                            name={rotRisk === 'declining' ? 'time-outline' : 'trending-up-outline'}
                            size={11}
                            color={rotRisk === 'declining' ? '#FF6B35' : Colors.primary}
                          />
                          <View style={{ flex: 1 }}>
                            <Text style={[styles.mfStakeBannerLabel, {
                              color: rotRisk === 'declining' ? '#FF6B35' : Colors.primary,
                            }]}>
                              {rotRisk === 'declining' ? 'ROTATION RISK' : 'RETURNING TO FULL DUTY'}
                            </Text>
                            <Text style={styles.mfStakeBannerReason}>
                              {rotRisk === 'declining'
                                ? `Minutes trending down — projected ${expMins != null ? expMins.toFixed(0) + ' min' : 'adjusted'} this match`
                                : `Minutes trending up — projected ${expMins != null ? expMins.toFixed(0) + ' min' : 'adjusted'} this match`}
                            </Text>
                          </View>
                          <Text style={[styles.mfStakeBannerMult, {
                            color: rotRisk === 'declining' ? '#FF6B35' : Colors.primary,
                          }]}>
                            {rotAdjPct > 0 ? '+' : ''}{rotAdjPct.toFixed(1)}%
                          </Text>
                        </View>
                      )}

                      {/* ── Opponent Defense Profile ── */}
                      {(() => {
                        const op = (prediction as any).opponentProfile as {
                          propType: string; allowedAvg: number; playerBaseline: number; diffPct: number;
                          tier: string; sampleSize: number; description: string;
                        } | undefined;
                        if (!op) {
                          // Fallback: plain allowed-avg note (old style)
                          if (oppAvg == null) return null;
                          return (
                            <View style={styles.mfOppRow}>
                              <Ionicons name="people-outline" size={10} color={Colors.textTertiary} />
                              <Text style={styles.mfOppLabel} numberOfLines={1}>
                                {pos ? `${pos}s` : 'Players'} vs {prediction.opponentName || 'opp'}: {oppAvg.toFixed(1)} avg allowed
                                {oppWt != null ? ` (${oppWt}% influence)` : ''}
                              </Text>
                            </View>
                          );
                        }
                        const isSuppressor = op.diffPct < -5;
                        const isLeak       = op.diffPct > 5;
                        const tierColor    = isSuppressor ? '#FF6B35' : isLeak ? Colors.primary : Colors.textSecondary;
                        const tierIcon     = isSuppressor ? 'shield-checkmark-outline' : isLeak ? 'trending-up-outline' : 'remove-outline';
                        const tierLabel    = op.tier.toUpperCase();
                        const absPct       = Math.abs(op.diffPct);
                        const direction    = op.diffPct < 0 ? 'fewer' : 'more';
                        const propLabel    = op.propType.replace(/_/g, ' ');
                        const oppShort     = (prediction.opponentName || 'Opp').split(' ').slice(-1)[0];
                        return (
                          <View style={[styles.mfOppCard, { borderColor: tierColor + '33', backgroundColor: tierColor + '0A' }]}>
                            <View style={styles.mfOppCardHeader}>
                              <Ionicons name={tierIcon as any} size={11} color={tierColor} />
                              <Text style={[styles.mfOppCardTitle, { color: tierColor }]}>OPP DEFENSE</Text>
                              <View style={[styles.mfOppTierPill, { backgroundColor: tierColor + '22', borderColor: tierColor + '55' }]}>
                                <Text style={[styles.mfOppTierText, { color: tierColor }]}>{tierLabel}</Text>
                              </View>
                            </View>
                            <View style={styles.mfOppCardBody}>
                              <View style={styles.mfOppStat}>
                                <Text style={styles.mfOppStatVal}>{op.allowedAvg.toFixed(1)}</Text>
                                <Text style={styles.mfOppStatSub}>allowed avg</Text>
                              </View>
                              <View style={styles.mfOppDivLine} />
                              <View style={styles.mfOppStat}>
                                <Text style={[styles.mfOppStatVal, { color: tierColor }]}>
                                  {op.diffPct > 0 ? '+' : ''}{op.diffPct.toFixed(0)}%
                                </Text>
                                <Text style={styles.mfOppStatSub}>vs baseline</Text>
                              </View>
                              <View style={styles.mfOppDivLine} />
                              <View style={styles.mfOppStat}>
                                <Text style={styles.mfOppStatVal}>{op.sampleSize}</Text>
                                <Text style={styles.mfOppStatSub}>games</Text>
                              </View>
                            </View>
                            <Text style={styles.mfOppCardDesc} numberOfLines={2}>
                              Comparable {propLabel} observations against {oppShort} are {absPct.toFixed(0)}% {direction} this player's {op.playerBaseline.toFixed(1)} avg
                            </Text>
                          </View>
                        );
                      })()}

                    </View>
                  </View>
                );
              })()}

              {/* Moneyline & Game Type — always shown; "Not available" when no odds data */}
              <>
                <View style={styles.analysisDivider} />
                <View style={styles.matchOddsRow}>
                  {(() => {
                    const formatOdds = (val: string) => {
                      if (!val || val === 'N/A') return '';
                      const n = parseFloat(val);
                      if (isNaN(n)) return val;
                      if (n > 1 && n < 50) {
                        if (n >= 2) return `+${Math.round((n - 1) * 100)}`;
                        return `${Math.round(-100 / (n - 1))}`;
                      }
                      return n > 0 ? `+${Math.round(n)}` : `${Math.round(n)}`;
                    };
                    if (prediction?.moneyline) {
                      const h = formatOdds(prediction.moneyline.home);
                      const d = formatOdds(prediction.moneyline.draw);
                      const a = formatOdds(prediction.moneyline.away);
                      if (h || d || a) {
                        // The odds object is fixture-oriented: moneyline.home belongs
                        // to homeTeam and moneyline.away belongs to awayTeam. Do not
                        // relabel the odds from the user's original venue selection;
                        // the backend may have corrected that selection to the
                        // verified fixture assignment.
                        const homeTeamShort = (prediction.homeTeam || prediction.teamName || 'HOME').split(' ').pop()?.slice(0, 5).toUpperCase() || 'HOME';
                        const awayTeamShort = (prediction.awayTeam || prediction.opponentName || 'AWAY').split(' ').pop()?.slice(0, 5).toUpperCase() || 'AWAY';
                        return (
                    <View style={[styles.moneylineWrap, { display: 'none' }]}>
                            <View style={styles.moneylineHeader}>
                              <Ionicons name="cash-outline" size={12} color={Colors.textSecondary} />
                              <Text style={styles.moneylineLabel}>MONEYLINE</Text>
                            </View>
                            <View style={styles.moneylinePills}>
                              <View style={styles.mlPill}>
                                <Text style={styles.mlPillTeam}>{homeTeamShort}</Text>
                                <Text style={styles.mlPillOdds}>{h}</Text>
                              </View>
                              {d ? (
                                <View style={styles.mlPill}>
                                  <Text style={styles.mlPillTeam}>DRAW</Text>
                                  <Text style={styles.mlPillOdds}>{d}</Text>
                                </View>
                              ) : null}
                              <View style={styles.mlPill}>
                                <Text style={styles.mlPillTeam}>{awayTeamShort}</Text>
                                <Text style={styles.mlPillOdds}>{a}</Text>
                              </View>
                            </View>
                            <Text style={styles.mlDisclaimer}>Indicative · verify with your sportsbook</Text>
                          </View>
                        );
                      }
                    }
                    return (
                      <View style={styles.moneylineWrap}>
                        <View style={styles.moneylineHeader}>
                          <Ionicons name="cash-outline" size={12} color={Colors.textSecondary} />
                          <Text style={styles.moneylineLabel}>MONEYLINE</Text>
                        </View>
                        <Text style={styles.mlDisclaimer}>Not available for this market</Text>
                      </View>
                    );
                  })()}

                  {prediction.expectedGameType && (
                      <View style={[styles.gameTypeWrap, { display: 'none' }]}>
                      <Text style={styles.gameTypeLabel}>GAME TYPE</Text>
                      <Text style={styles.gameTypeValue}>{
                        (['open','cagey','one-sided','high-tempo'].includes(prediction.expectedGameType?.toLowerCase())
                          ? prediction.expectedGameType.toUpperCase()
                          : 'OPEN')
                      }</Text>
                      {prediction.keyMatchupFactor && (
                        <Text style={styles.gameTypeSub}>{prediction.keyMatchupFactor}</Text>
                      )}
                    </View>
                  )}
                </View>
              </>

              {/* 2nd Leg Aggregate Banner */}
              {prediction.gameSituation && (prediction.gameSituation as any).isSecondLeg && (() => {
                const gs = prediction.gameSituation as any;
                const agg = gs.aggregate || {};
                const homeTeam = prediction.homeTeam || prediction.teamName || 'HOME';
                const awayTeam = prediction.awayTeam || prediction.opponentName || 'AWAY';
                const tied = agg.goalDeficit === 0;
                const homeName = homeTeam.split(' ').pop()?.slice(0, 8).toUpperCase() || 'HOME';
                const awayName = awayTeam.split(' ').pop()?.slice(0, 8).toUpperCase() || 'AWAY';
                const aggColor = tied ? '#F59E0B' : agg.homeTeamTrailing ? Colors.error : Colors.success;
                const aggLabel = tied
                  ? '⚡ AGGREGATE TIED — Both teams must score'
                  : agg.homeTeamTrailing
                    ? `⚠ ${homeName} TRAILS — Must score ${agg.mustWinByGoals} to advance`
                    : `✓ ${homeName} LEADS — Can manage the game`;
                return (
                  <View style={styles.secondLegBanner}>
                    <View style={styles.secondLegHeader}>
                      <View style={styles.secondLegBadge}>
                        <Text style={styles.secondLegBadgeText}>2ND LEG</Text>
                      </View>
                      {gs.isKnockout && (
                        <View style={[styles.secondLegBadge, { backgroundColor: '#1a1a2e' }]}>
                          <Text style={[styles.secondLegBadgeText, { color: '#818cf8' }]}>KNOCKOUT</Text>
                        </View>
                      )}
                    </View>
                    {agg.firstLegFound && agg.firstLegScore ? (
                      <>
                        <Text style={styles.secondLegFirstLeg}>1st leg: {agg.firstLegScore}</Text>
                        <View style={styles.secondLegAggRow}>
                          <Text style={styles.secondLegAggTeam}>{homeName}</Text>
                          <Text style={[styles.secondLegAggScore, { color: aggColor }]}>
                            {agg.homeTeamAggregate} – {agg.awayTeamAggregate}
                          </Text>
                          <Text style={styles.secondLegAggTeam}>{awayName}</Text>
                        </View>
                        <Text style={[styles.secondLegStatus, { color: aggColor }]}>{aggLabel}</Text>
                      </>
                    ) : (
                      <Text style={styles.secondLegFirstLeg}>Knockout 2nd leg — elevated intensity expected</Text>
                    )}
                  </View>
                );
              })()}

              {/* Expected Possession */}
              {prediction.expectedPossession
                && Number.isFinite(prediction.expectedPossession.home)
                && Number.isFinite(prediction.expectedPossession.away)
                && (() => {
                const homePoss = prediction.expectedPossession!.home;
                const awayPoss = prediction.expectedPossession!.away;
                const isPlayerHome = venueOverride === 'home';
                const playerTeamName = prediction.teamName || '';
                const opponentTeamName = prediction.opponentName || '';
                const homeShort = (prediction.homeTeam || (isPlayerHome ? playerTeamName : opponentTeamName) || 'HOME')
                  .split(' ').pop()?.slice(0, 6).toUpperCase() || 'HOME';
                const awayShort = (prediction.awayTeam || (isPlayerHome ? opponentTeamName : playerTeamName) || 'AWAY')
                  .split(' ').pop()?.slice(0, 6).toUpperCase() || 'AWAY';
                return (
                  <>
                    <View style={styles.analysisDivider} />
                    <View style={[styles.possRow, { display: 'none' }]}>
                      <View style={styles.possHeader}>
                        <Ionicons name="football-outline" size={12} color={Colors.textSecondary} />
                        <Text style={styles.possLabel}>EXPECTED POSSESSION</Text>
                      </View>
                      <View style={styles.possBarWrap}>
                        <View style={[styles.possBarHome, { flex: homePoss }]} />
                        <View style={[styles.possBarAway, { flex: awayPoss }]} />
                      </View>
                      <View style={styles.possNumbers}>
                        <Text style={styles.possHomeText}>{homeShort}  {Math.round(homePoss)}%</Text>
                        <Text style={styles.possAwayText}>{Math.round(awayPoss)}%  {awayShort}</Text>
                      </View>
                      {(prediction.possessionTeamAvg != null || prediction.possessionOppAvg != null) && (
                        <Text style={styles.possSub}>
                          Season avg: {prediction.possessionTeamAvg ?? '—'}% vs {prediction.possessionOppAvg ?? '—'}%
                        </Text>
                      )}
                    </View>
                  </>
                );
              })()}

              {/* ─── PRESSURE DYNAMICS ─── */}
              {prediction.expectedPossession
                && Number.isFinite(prediction.expectedPossession.home)
                && Number.isFinite(prediction.expectedPossession.away)
                && (() => {
                const homePoss = prediction.expectedPossession!.home;
                const awayPoss = prediction.expectedPossession!.away;
                const isPlayerHome = venueOverride === 'home';
                const playerTeamName = prediction.teamName || '';
                const opponentTeamName = prediction.opponentName || '';
                const homeName = prediction.homeTeam || (isPlayerHome ? playerTeamName : opponentTeamName) || 'Home';
                const awayName = prediction.awayTeam || (isPlayerHome ? opponentTeamName : playerTeamName) || 'Away';

                const homeIsAggressor = homePoss >= awayPoss;
                const aggressorName = homeIsAggressor ? homeName : awayName;
                const defenderName = homeIsAggressor ? awayName : homeName;
                const aggressorPoss = homeIsAggressor ? homePoss : awayPoss;
                const defenderPoss = homeIsAggressor ? awayPoss : homePoss;
                const gap = Math.round(aggressorPoss - defenderPoss);

                const pressureText = gap >= 15
                  ? `${aggressorName} are projected to dominate possession by ${gap} percentage points — a significant tactical edge. Expect ${aggressorName} to dictate the tempo, pin ${defenderName} deep, and create chances through sustained pressure. ${defenderName} will likely look to absorb and counter.`
                  : gap >= 8
                  ? `${aggressorName} hold a meaningful possession edge (~${gap}pp). They set the pace and play in the opponent's half more often. ${defenderName} will be reactive, defending in a mid-block and waiting for chances to transition.`
                  : `Possession is projected to be closely contested. Both teams are expected to have spells of control — game flow will depend on which midfield wins the second balls and sets the tempo.`;

                return (
                  <>
                    <View style={styles.analysisDivider} />
                    <View style={[styles.pressureCard, { display: 'none' }]}>
                      <View style={styles.pressureHeaderRow}>
                        <Ionicons name="shield-half-outline" size={12} color={Colors.primary} />
                        <Text style={styles.pressureTitle}>PRESSURE DYNAMICS</Text>
                      </View>
                      <Text style={styles.pressureBody}>{pressureText}</Text>
                      <View style={styles.pressureTeamsRow}>
                        <View style={styles.pressureTeamBlock}>
                          <Text style={styles.pressureTeamName} numberOfLines={1}>{aggressorName}</Text>
                          <View style={[styles.pressureLabel, styles.pressureLabelAggressor]}>
                            <Text style={styles.pressureLabelText}>⚔ THE AGGRESSORS</Text>
                          </View>
                          <Text style={styles.pressurePossText}>{Math.round(aggressorPoss)}% poss</Text>
                        </View>
                        <View style={styles.pressureVsDivider} />
                        <View style={styles.pressureTeamBlock}>
                          <Text style={styles.pressureTeamName} numberOfLines={1}>{defenderName}</Text>
                          <View style={[styles.pressureLabel, styles.pressureLabelDefender]}>
                            <Text style={styles.pressureLabelText}>🛡 THE DEFENDERS</Text>
                          </View>
                          <Text style={styles.pressurePossText}>{Math.round(defenderPoss)}% poss</Text>
                        </View>
                      </View>
                    </View>
                  </>
                );
              })()}


              {/* Analysis Summary */}
              {prediction.analysisSummary && (() => {
                const s = prediction.analysisSummary!;
                return (
                  <>
                    <View style={styles.analysisDivider} />
                    <View style={[styles.summarySection, { display: 'none' }]}>
                      <View style={styles.summaryHeader}>
                        <Ionicons name="analytics-outline" size={13} color={Colors.primary} />
                        <Text style={styles.summaryTitle}>ANALYSIS BREAKDOWN</Text>
                      </View>
                      <View style={styles.summaryGrid}>
                        <View style={styles.summaryItem}>
                          <Text style={styles.summaryLabel}>{s.statLabel || 'STAT'}</Text>
                          <Text style={styles.summaryValue}>{s.venueAverage != null ? s.venueAverage.toFixed(1) : '—'}</Text>
                          <Text style={styles.summarySub}>{(s.venue || 'venue').toUpperCase()} AVG</Text>
                        </View>
                        <View style={styles.summaryItem}>
                          <Text style={styles.summaryLabel}>Venue Samples</Text>
                          <Text style={styles.summaryValue}>{s.venueSampleSize ?? '—'}</Text>
                          <Text style={styles.summarySub}>{(s.venue || 'venue').toUpperCase()}</Text>
                        </View>
                        <View style={styles.summaryItem}>
                          <Text style={styles.summaryLabel}>Opponent Profile</Text>
                          <Text style={styles.summaryValue}>{s.opponentAllowedAverage != null ? s.opponentAllowedAverage.toFixed(1) : '—'}</Text>
                          <Text style={styles.summarySub}>OPP AVG</Text>
                        </View>
                        {prediction.propType === 'saves' && (
                          <>
                            <View style={styles.summaryItem}>
                              <Text style={styles.summaryLabel}>GK Save Rate</Text>
                              <Text style={styles.summaryValue}>{s.goalkeeperSaveRate != null ? `${s.goalkeeperSaveRate.toFixed(1)}%` : '—'}</Text>
                              <Text style={styles.summarySub}>{s.goalkeeperSaveSample ?? 0} GAMES</Text>
                            </View>
                             <View style={styles.summaryItem}>
                               <Text style={styles.summaryLabel}>Expected Opponent SOT</Text>
                              <Text style={styles.summaryValue}>{s.opponentShotsOnTarget != null ? s.opponentShotsOnTarget.toFixed(1) : '—'}</Text>
                               <Text style={styles.summarySub}>MATCH AVG</Text>
                            </View>
                          </>
                        )}
                      </View>
                    </View>
                  </>
                );
              })()}
               </>)}
            </View>
            <CompactAnalysisBars prediction={prediction} />
            <EventEvidenceCard
              data={(prediction as any).tacticalContext?.positionPassesReceived}
            />
             <SameRoleEvidenceCard
               data={(prediction as any).positionComparison
                 || (prediction as any).tacticalIntelligence?.positionCohort}
               recommendation={prediction.recommendation}
               line={prediction.line}
             />
            {/* ─── MATCHUP OVERVIEW (non-soccer sports) ─── */}
            {false && (<>
            {((prediction as any).matchupOverview) && prediction.sport !== 'soccer' && (() => {
              const mo = (prediction as any).matchupOverview;
              const homeTeam = mo.homeTeam || '';
              const awayTeam = mo.awayTeam || '';
              const ml = mo.moneyline as { home?: number; away?: number } | undefined;
              return (
                <View style={{ backgroundColor: '#111', borderRadius: 12, padding: 14, marginBottom: 10, borderWidth: 1, borderColor: '#222' }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                    <Ionicons name="stats-chart-outline" size={11} color={Colors.primary} />
                    <Text style={{ fontSize: 10, fontWeight: '800', color: Colors.textSecondary, letterSpacing: 1 }}>MATCHUP</Text>
                    {mo.expectedGameType ? (
                      <View style={{ marginLeft: 'auto', backgroundColor: Colors.primary + '18', borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2, borderWidth: 1, borderColor: Colors.primary + '33' }}>
                        <Text style={{ fontSize: 9, color: Colors.primary, fontWeight: '700', letterSpacing: 0.4 }}>
                          {(mo.expectedGameType as string).toUpperCase()}
                        </Text>
                      </View>
                    ) : null}
                  </View>
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <View style={{ alignItems: 'center', flex: 1 }}>
                      <Text style={{ fontSize: 13, fontWeight: '800', color: Colors.primary }} numberOfLines={1}>{homeTeam}</Text>
                      <Text style={{ fontSize: 9, color: Colors.textTertiary, marginTop: 2 }}>HOME</Text>
                      {ml?.home != null && (
                        <Text style={{ fontSize: 11, color: (ml.home as number) < 0 ? Colors.success : Colors.textSecondary, fontWeight: '700', marginTop: 2 }}>
                          {(ml.home as number) > 0 ? `+${ml.home}` : ml.home}
                        </Text>
                      )}
                    </View>
                    <View style={{ alignItems: 'center', paddingHorizontal: 8 }}>
                      <Text style={{ fontSize: 12, color: Colors.textTertiary, fontWeight: '700' }}>VS</Text>
                    </View>
                    <View style={{ alignItems: 'center', flex: 1 }}>
                      <Text style={{ fontSize: 13, fontWeight: '800', color: Colors.text }} numberOfLines={1}>{awayTeam}</Text>
                      <Text style={{ fontSize: 9, color: Colors.textTertiary, marginTop: 2 }}>AWAY</Text>
                      {ml?.away != null && (
                        <Text style={{ fontSize: 11, color: (ml.away as number) < 0 ? Colors.success : Colors.textSecondary, fontWeight: '700', marginTop: 2 }}>
                          {(ml.away as number) > 0 ? `+${ml.away}` : ml.away}
                        </Text>
                      )}
                    </View>
                  </View>
                  {mo.keyMatchupFactor ? (
                    <View style={{ marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: '#222' }}>
                      <Text style={{ fontSize: 11, color: Colors.textSecondary, lineHeight: 16 }}>{mo.keyMatchupFactor as string}</Text>
                    </View>
                  ) : null}
                </View>
              );
            })()}

            {/* ─── GAME LOG GRID ─── */}
            {prediction.gameLogs && prediction.gameLogs.length > 0 && (() => {
              const realLogs = prediction.gameLogs!.filter(g => !g.synthetic);
              const allSynthetic = realLogs.length === 0;
              const displayLogs = allSynthetic ? [] : realLogs;
              const effectiveLine = adjustedLine ?? prediction.line;
              const COLS = 6;
              const tileW = (SCREEN_W - 40 - 16 - (COLS - 1) * 4) / COLS;
              const oppPoss = prediction.possessionOppAvg;
              const propT = prediction.propType || '';
              const h2hMatches = prediction.h2hPlayerStats?.matches ?? [];
              const hasH2H = h2hMatches.length > 0;
              const isH2H = gameLogFilter === 'h2h';

              const filteredLogs = displayLogs.filter(g => {
                if (gameLogFilter === 'opp') {
                  const norm = (s: string) => (s || '').toLowerCase().replace(/[\s.\-]/g, '').slice(0, 6);
                  return norm(g.opponent || '') === norm(prediction.opponent || '');
                }
                return gameLogFilter === 'all' ? true : g.venue === gameLogFilter;
              });

              // Map each filtered log to its index in displayLogs (for deselect tracking)
              const filteredWithIdx = filteredLogs.map(g => ({ log: g, origIdx: displayLogs.indexOf(g) }));

              // Selected = not deselected
              const selectedLogs = filteredWithIdx.filter(({ origIdx }) => !deselectedLogIndices.has(origIdx)).map(({ log }) => log);
              const selectedVals = selectedLogs.map(g => g.value).filter((v): v is number => v != null);
              const selOver = selectedVals.filter(v => effectiveLine != null && v > effectiveLine).length;
              const selTotal = selectedVals.length;
              const selHitPct = selTotal > 0 ? Math.round(selOver / selTotal * 100) : 0;
              const selectedMean = selectedVals.length > 0 ? selectedVals.reduce((a, b) => a + b, 0) / selectedVals.length : null;
              const allVals = displayLogs.map(g => g.value).filter((v): v is number => v != null);
              const allMean = allVals.length > 0 ? allVals.reduce((a, b) => a + b, 0) / allVals.length : null;
              const hasDeselected = deselectedLogIndices.size > 0;
              const hasLowMinGames = displayLogs.some(g => (g.minutes || 0) > 0 && (g.minutes || 0) < 60);

              const tierColor = (tier: string | null | undefined): string | null => {
                if (!tier) return null;
                if (tier === 'ELITE') return '#FF453A';
                if (tier === 'STRONG') return '#FF9F0A';
                if (tier === 'MID') return '#0A84FF';
                return '#34C759';
              };
              const minsColor = (mins: number): string => {
                if (mins >= 80) return '#39FF14';
                if (mins >= 60) return '#FFB347';
                if (mins >= 45) return '#FF8C00';
                return '#FF453A';
              };

              return (
                <View style={styles.gameLogsCard}>

                  {/* ── Header ── */}
                  <View style={styles.gameLogsHeader}>
                    <View style={styles.glHeaderLeft}>
                      <Ionicons name="pulse" size={10} color={Colors.primary} />
                      <View>
                        <Text style={styles.gameLogsTitle}>
                          {allSynthetic ? 'GAME LOG' : `GAME LOG  ·  ${displayLogs.length} GAMES`}
                        </Text>
                        {prediction.historyGameCount && prediction.historyGameCount > displayLogs.length && (
                          <Text style={styles.gameLogsHistoryMeta}>
                            {prediction.historyRange
                              ? `${prediction.historyRange.min}–${prediction.historyRange.max} · `
                              : ''}
                            {prediction.historyGameCount} TOTAL EVIDENCE GAMES
                          </Text>
                        )}
                        {prediction.opponentName && prediction.currentOppTier && (() => {
                          const tierColor: Record<string, string> = {
                            ELITE: '#FF4444', STRONG: '#FF8C00', MID: '#FFCC00', WEAK: '#39FF14',
                          };
                          const c = tierColor[prediction.currentOppTier] || '#888';
                          return (
                            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 2 }}>
                              <Text style={{ fontSize: 9, color: '#666', letterSpacing: 0.3 }}>
                                vs {prediction.opponentName}
                              </Text>
                              <View style={{
                                backgroundColor: c + '22', borderRadius: 3,
                                paddingHorizontal: 4, paddingVertical: 1,
                                borderWidth: 1, borderColor: c + '55',
                              }}>
                                <Text style={{ fontSize: 8, color: c, fontWeight: '800', letterSpacing: 0.6 }}>
                                  {prediction.currentOppTier}
                                </Text>
                              </View>
                              <Text style={{ fontSize: 8, color: '#555', letterSpacing: 0.2 }}>
                                · auto-filtered
                              </Text>
                            </View>
                          );
                        })()}
                      </View>
                    </View>
                    <View style={styles.glHeaderRight}>
                      {!allSynthetic && oppPoss != null && (
                        <View style={styles.glOppPossBadge}>
                          <Text style={styles.glOppPossLabel}>OPP POSS</Text>
                          <Text style={styles.glOppPossVal}>{oppPoss}%</Text>
                        </View>
                      )}
                      {hasDeselected && (
                        <TouchableOpacity
                          onPress={() => { setDeselectedLogIndices(new Set()); Haptics.selectionAsync(); }}
                          style={styles.glResetBtn}
                        >
                          <Text style={styles.glResetBtnText}>RESET ALL</Text>
                        </TouchableOpacity>
                      )}
                    </View>
                  </View>

                  {/* ── Live Hit Rate Bar ── */}
                  {!allSynthetic && selTotal > 0 && !isH2H && (
                    <View style={styles.glHitRateRow}>
                      <View style={styles.glHitRateLeft}>
                        <Text style={styles.glHitRateCount}>
                          <Text style={{ color: selHitPct >= 55 ? Colors.success : Colors.error, fontWeight: '900' }}>{selOver}</Text>
                          <Text style={{ color: Colors.textSecondary, fontWeight: '600' }}>/{selTotal}</Text>
                        </Text>
                        <Text style={styles.glHitRateLabel}>{hasDeselected ? 'SELECTED HIT' : 'OVER HIT RATE'}</Text>
                      </View>
                      <View style={styles.glHitRateBarWrap}>
                        <View style={[styles.glHitRateFill, {
                          width: `${selHitPct}%` as any,
                          backgroundColor: selHitPct >= 60 ? Colors.success : selHitPct >= 50 ? '#FFB347' : Colors.error,
                        }]} />
                      </View>
                      <Text style={[styles.glHitRatePct, { color: selHitPct >= 60 ? Colors.success : selHitPct >= 50 ? '#FFB347' : Colors.error }]}>
                        {selHitPct}%
                      </Text>
                      {hasDeselected && selectedMean != null && allMean != null && (
                        <Text style={styles.glSelectedMean}>
                          {selectedMean.toFixed(1)}<Text style={{ color: Colors.textTertiary }}> vs {allMean.toFixed(1)}</Text>
                        </Text>
                      )}
                    </View>
                  )}

                  {/* ── Line Adjuster ── */}
                  {!allSynthetic && effectiveLine != null && !isH2H && (
                    <View style={styles.glLineAdjuster}>
                      <TouchableOpacity
                        onPress={() => { setAdjustedLine(+Math.max(0, effectiveLine - 0.5).toFixed(1)); Haptics.selectionAsync(); }}
                        style={styles.glLineBtn}
                      >
                        <Text style={styles.glLineBtnText}>−</Text>
                      </TouchableOpacity>
                      <View style={{ alignItems: 'center', minWidth: 40 }}>
                        <Text style={styles.glLineValue}>{Number.isInteger(effectiveLine) ? effectiveLine.toFixed(1) : effectiveLine}</Text>
                        <Text style={styles.glLineLabel}>LINE</Text>
                      </View>
                      <TouchableOpacity
                        onPress={() => { setAdjustedLine(+(effectiveLine + 0.5).toFixed(1)); Haptics.selectionAsync(); }}
                        style={styles.glLineBtn}
                      >
                        <Text style={styles.glLineBtnText}>+</Text>
                      </TouchableOpacity>
                      {adjustedLine !== null && adjustedLine !== prediction.line && (
                        <TouchableOpacity
                          onPress={() => { setAdjustedLine(null); Haptics.selectionAsync(); }}
                          style={styles.glLineResetBtn}
                        >
                          <Text style={styles.glLineResetText}>RESET LINE</Text>
                        </TouchableOpacity>
                      )}
                    </View>
                  )}

                  {/* ── Synthetic fallback ── */}
                  {allSynthetic && (
                    <View style={styles.syntheticNotice}>
                      <Ionicons name="information-circle-outline" size={14} color={Colors.textSecondary} />
                      <Text style={styles.syntheticNoticeText}>
                        Per-game data unavailable for this player. Analysis is based on season averages only.
                      </Text>
                    </View>
                  )}

                  {/* ── Filter Tabs ── */}
                  {!allSynthetic && displayLogs.some(g => g.venue === 'home' || g.venue === 'away') && (
                    <View style={styles.glTabRow}>
                      {(['all', 'home', 'away'] as const).map(f => (
                        <TouchableOpacity
                          key={f}
                          style={[styles.glTab, gameLogFilter === f && styles.glTabActive]}
                          onPress={() => { setGameLogFilter(f); Haptics.selectionAsync(); }}
                        >
                          <Text style={[styles.glTabText, gameLogFilter === f && styles.glTabTextActive]}>
                            {f.toUpperCase()}
                          </Text>
                        </TouchableOpacity>
                      ))}
                      {(() => {
                        const norm = (s: string) => (s || '').toLowerCase().replace(/[\s.\-]/g, '').slice(0, 6);
                        const oppGames = prediction.opponent
                          ? displayLogs.filter(g => norm(g.opponent || '') === norm(prediction.opponent || ''))
                          : [];
                        if (oppGames.length === 0) return null;
                        const oppLabel = (prediction.opponent || '')
                          .replace(/^(al-?|fc |cf |rc |sc |cd |ud |sd |rcd |as |ss |ac |us |sp |ca |cp |ue |ce |cm |se |sk )/i, '')
                          .slice(0, 4).toUpperCase();
                        return (
                          <TouchableOpacity
                            style={[styles.glTab, gameLogFilter === 'opp' && styles.glTabActive]}
                            onPress={() => { setGameLogFilter('opp'); Haptics.selectionAsync(); }}
                          >
                            <Text style={[styles.glTabText, gameLogFilter === 'opp' && styles.glTabTextActive]}>
                              {oppLabel || 'OPP'}
                            </Text>
                          </TouchableOpacity>
                        );
                      })()}
                      <TouchableOpacity
                        style={[styles.glTab, gameLogFilter === 'h2h' && styles.glTabActive]}
                        onPress={() => { setGameLogFilter('h2h'); Haptics.selectionAsync(); }}
                      >
                        <Text style={[styles.glTabText, gameLogFilter === 'h2h' && styles.glTabTextActive]}>
                          H2H
                        </Text>
                      </TouchableOpacity>
                      {hasLowMinGames && (
                        <TouchableOpacity
                          style={styles.glTabQuality}
                          onPress={() => {
                            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                            const toDeselect = new Set<number>();
                            displayLogs.forEach((g, idx) => {
                              if ((g.minutes || 0) > 0 && (g.minutes || 0) < 60) toDeselect.add(idx);
                            });
                            setDeselectedLogIndices(toDeselect);
                          }}
                        >
                          <Text style={styles.glTabQualityText}>QUALITY</Text>
                        </TouchableOpacity>
                      )}
                    </View>
                  )}

                  {/* ── OVER RATE strip — updates with ALL / HOME / AWAY filter ── */}
                  {!allSynthetic && effectiveLine != null && !isH2H && (() => {
                    // Use filteredLogs so numbers react to the venue filter tabs
                    const baseVals = filteredLogs.map(g => g.value).filter((v): v is number => v != null);
                    const chips = [
                      { label: 'L5',  slice: baseVals.slice(-5) },
                      { label: 'L10', slice: baseVals.slice(-10) },
                      { label: 'L15', slice: baseVals.slice(-15) },
                      { label: 'SZN', slice: baseVals },
                    ].map(({ label, slice }) => {
                      const over = slice.filter(v => v > effectiveLine!).length;
                      const pct = slice.length >= 2 ? Math.round(over / slice.length * 100) : null;
                      return { label, pct, n: slice.length };
                    }).filter(c => c.n >= 2);
                    if (chips.length === 0) return null;
                    // Badge shown when a venue filter is active
                    const filterBadge = gameLogFilter !== 'all'
                      ? (gameLogFilter === 'home' ? 'HOME' : gameLogFilter === 'away' ? 'AWAY' : 'OPP')
                      : null;
                    return (
                      <View style={{
                        borderTopWidth: 1, borderTopColor: '#181818',
                        backgroundColor: '#0A0A0A',
                        flexDirection: 'row', overflow: 'hidden',
                      }}>
                        {/* Venue label rotated on the left when a filter is active */}
                        {filterBadge && (
                          <View style={{
                            width: 22, alignItems: 'center', justifyContent: 'center',
                            borderRightWidth: 1, borderRightColor: '#181818',
                            backgroundColor: '#111',
                          }}>
                            <Text style={{
                              fontSize: 6, color: Colors.primary, fontWeight: '800',
                              letterSpacing: 0.6, fontFamily: 'JetBrainsMono_700Bold',
                              transform: [{ rotate: '-90deg' }],
                              width: 38, textAlign: 'center',
                            }}>{filterBadge}</Text>
                          </View>
                        )}
                        {chips.map((chip, i) => {
                          const pct = chip.pct;
                          const color = pct != null && pct >= 60 ? Colors.success
                            : pct != null && pct < 42 ? Colors.error : '#555';
                          const isLast = i === chips.length - 1;
                          return (
                            <View key={chip.label} style={{
                              flex: 1, alignItems: 'center', paddingVertical: 9,
                              borderRightWidth: isLast ? 0 : 1, borderRightColor: '#181818',
                              borderTopWidth: 2,
                              borderTopColor: pct != null && pct >= 60 ? Colors.success + '70'
                                : pct != null && pct < 42 ? Colors.error + '70' : '#181818',
                            }}>
                              <Text style={{ fontSize: 7, color: '#3a3a3a', fontWeight: '700', letterSpacing: 1.2 }}>{chip.label}</Text>
                              <Text style={{ fontSize: 17, fontWeight: '900', color, lineHeight: 21, fontFamily: 'JetBrainsMono_700Bold' }}>
                                {pct != null ? `${pct}` : '—'}<Text style={{ fontSize: 10 }}>%</Text>
                              </Text>
                              <Text style={{ fontSize: 7, color: '#2a2a2a', fontFamily: 'JetBrainsMono_400Regular' }}>{chip.n}g</Text>
                            </View>
                          );
                        })}
                      </View>
                    );
                  })()}

                  {/* ── Bar Chart ── */}
                  {!allSynthetic && !isH2H && (() => {
                    if (filteredWithIdx.length === 0) return null;
                    const CHART_H = 112;
                    const BAR_W = 34;
                    const BAR_GAP = 5;
                    const vals = filteredWithIdx.map(({ log: g }) => g.value).filter((v): v is number => v != null);
                    if (vals.length === 0) return null;
                    const maxVal = Math.max(...vals, effectiveLine ?? 0) * 1.18;
                    const lineTopPx = effectiveLine != null && maxVal > 0
                      ? CHART_H * (1 - effectiveLine / maxVal) : null;
                    const totalW = filteredWithIdx.length * (BAR_W + BAR_GAP) + 32;

                    return (
                      <ScrollView horizontal showsHorizontalScrollIndicator={false}
                        contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 4 }}>
                        <View style={{ width: totalW }}>
                          {/* Chart area */}
                          <View style={{ height: CHART_H, position: 'relative' }}>
                            {/* Reference line */}
                            {lineTopPx != null && (
                              <View style={{
                                position: 'absolute', top: lineTopPx, left: 0, right: 0,
                                height: 1.5, backgroundColor: 'rgba(255,255,255,0.18)', zIndex: 5,
                              }}>
                                <Text style={{
                                  position: 'absolute', right: 0, top: -8,
                                  fontSize: 7, color: 'rgba(255,255,255,0.35)', fontWeight: '800',
                                  letterSpacing: 0.5,
                                }}>{effectiveLine}</Text>
                              </View>
                            )}

                            {/* Bars */}
                            <View style={{ flexDirection: 'row', alignItems: 'flex-end', height: CHART_H, gap: BAR_GAP }}>
                              {filteredWithIdx.map(({ log: g, origIdx }, i) => {
                                const val = g.value;
                                const isOver = val != null && effectiveLine != null && val > effectiveLine;
                                const isDesel = deselectedLogIndices.has(origIdx);
                                const barH = val != null && maxVal > 0 ? Math.max(5, (val / maxVal) * CHART_H) : 5;
                                const barColor = isDesel
                                  ? 'rgba(45,45,45,0.7)'
                                  : isOver ? 'rgba(57,255,20,0.72)' : 'rgba(255,59,48,0.62)';
                                const glowColor = isDesel ? undefined
                                  : isOver ? Colors.success : Colors.error;
                                const tc = tierColor(g.oppTier);

                                return (
                                  <TouchableOpacity
                                    key={i}
                                    activeOpacity={0.7}
                                    onPress={() => {
                                      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                                      setDeselectedLogIndices(prev => {
                                        const nxt = new Set(prev);
                                        if (nxt.has(origIdx)) nxt.delete(origIdx); else nxt.add(origIdx);
                                        return nxt;
                                      });
                                    }}
                                    style={{ alignItems: 'center', justifyContent: 'flex-end', width: BAR_W, height: CHART_H }}
                                  >
                                    {/* Tier dot above bar */}
                                    {tc && !isDesel && (
                                      <View style={{ width: 4, height: 4, borderRadius: 2, backgroundColor: tc, marginBottom: 2 }} />
                                    )}
                                    {/* Value label */}
                                    {val != null && (
                                      <Text style={{
                                        fontSize: 9, fontWeight: '800', lineHeight: 11, marginBottom: 2,
                                        color: isDesel ? '#333' : isOver ? Colors.success : Colors.error,
                                      }}>{val}</Text>
                                    )}
                                    {['saves', 'goalie_saves'].includes(propT) && (
                                      <Text style={{
                                        fontSize: 7, lineHeight: 9, marginBottom: 1,
                                        color: isDesel ? '#333' : '#60A5FA',
                                        fontWeight: '700',
                                      }}>
                                        SOT {g.opponentShotsOnTarget != null ? Number(g.opponentShotsOnTarget).toFixed(0) : '—'}
                                      </Text>
                                    )}
                                    {/* Bar */}
                                    <View style={{
                                      width: BAR_W - 6, height: barH, backgroundColor: barColor,
                                      borderRadius: 3, borderTopLeftRadius: 4, borderTopRightRadius: 4,
                                      shadowColor: glowColor, shadowOffset: { width: 0, height: 0 },
                                      shadowOpacity: isDesel ? 0 : 0.45, shadowRadius: 5,
                                    }} />
                                  </TouchableOpacity>
                                );
                              })}
                            </View>
                          </View>

                          {/* Labels: date + opponent */}
                          <View style={{ flexDirection: 'row', gap: BAR_GAP, marginTop: 5 }}>
                            {filteredWithIdx.map(({ log: g }, i) => {
                              const isSoc = (g.sport || 'soccer') === 'soccer';
                              const dateStr = g.date ? (() => {
                                const d = new Date((g.date as string).slice(0, 10) + 'T12:00:00');
                                return isNaN(d.getTime()) ? '' : `${d.getMonth() + 1}/${d.getDate()}`;
                              })() : '';
                              const oppRaw = g.opponent || '?';
                              const oppShort = isSoc
                                ? oppRaw.replace(/^(al-?|fc |cf |rc |sc |cd |ud |sd |rcd |as |ss |ac |us |sp |ca |cp |ue |ce |cm |se |sk )/i, '').slice(0, 3).toUpperCase()
                                : oppRaw.toUpperCase().slice(0, 3);
                              const venueC = g.venue === 'home' ? Colors.success : g.venue === 'away' ? '#60A5FA' : '#555';
                              return (
                                <View key={i} style={{ width: BAR_W, alignItems: 'center' }}>
                                  <Text style={{ fontSize: 7, color: '#555', fontWeight: '600', lineHeight: 10 }}>{dateStr}</Text>
                                  <Text style={{ fontSize: 7, color: venueC, fontWeight: '700', lineHeight: 10 }}>{oppShort}</Text>
                                </View>
                              );
                            })}
                          </View>

                          {/* Tap-to-toggle hint */}
                          <Text style={{ fontSize: 7, color: '#333', textAlign: 'right', marginTop: 3, fontStyle: 'italic' }}>
                            tap bar to toggle
                          </Text>
                        </View>
                      </ScrollView>
                    );
                  })()}

                  {/* ── Compact OPP Strength Legend — soccer only ── */}
                  {prediction.sport === 'soccer' && !allSynthetic && !isH2H && (
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16, paddingBottom: 6, flexWrap: 'wrap' }}>
                      <Text style={{ fontSize: 7, color: '#444', fontWeight: '700', letterSpacing: 0.6 }}>OPP TIER</Text>
                      {([
                        { color: '#FF453A', label: 'Elite' },
                        { color: '#FF9F0A', label: 'Strong' },
                        { color: '#0A84FF', label: 'Mid' },
                        { color: '#34C759', label: 'Weak' },
                      ] as const).map(({ color, label }) => (
                        <View key={label} style={{ flexDirection: 'row', alignItems: 'center', gap: 3 }}>
                          <View style={{ width: 5, height: 5, borderRadius: 3, backgroundColor: color }} />
                          <Text style={{ fontSize: 7, color: '#555' }}>{label}</Text>
                        </View>
                      ))}
                    </View>
                  )}

                  {/* ── Home/Away Averages ── */}
                  {!allSynthetic && !isH2H && (prediction.homeAvg != null || prediction.awayAvg != null) && (
                    <View style={styles.avgRow}>
                      {prediction.homeAvg != null && (
                        <Text style={styles.avgText}>HOME AVG  {prediction.homeAvg.toFixed(1)}</Text>
                      )}
                      {prediction.awayAvg != null && (
                        <Text style={styles.avgText}>AWAY AVG  {prediction.awayAvg.toFixed(1)}</Text>
                      )}
                    </View>
                  )}

                  {/* ── Defensive stats ── */}
                  {!allSynthetic && !isH2H && (() => {
                    const logsWithDef = displayLogs.filter(
                      g => g.blocks != null || g.interceptions != null || g.tackles != null || g.clearances != null
                    );
                    if (logsWithDef.length === 0) return null;
                    const avg = (key: keyof typeof logsWithDef[0]) => {
                      const vals = logsWithDef.map(g => g[key] as number | null).filter(v => v != null) as number[];
                      return vals.length > 0 ? (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1) : null;
                    };
                    const avgBlocks = avg('blocks');
                    const avgInt = avg('interceptions');
                    const avgTkl = avg('tackles');
                    const avgClr = avg('clearances');
                    const items = [
                      avgTkl && { label: 'TKL', val: avgTkl },
                      avgBlocks && { label: 'BLK', val: avgBlocks },
                      avgInt && { label: 'INT', val: avgInt },
                      avgClr && { label: 'CLR', val: avgClr },
                    ].filter(Boolean) as { label: string; val: string }[];
                    if (items.length === 0) return null;
                    return (
                      <View style={styles.defStatsRow}>
                        <Text style={styles.defStatsLabel}>DEF AVG</Text>
                        {items.map((item, idx) => (
                          <View key={idx} style={styles.defStatChip}>
                            <Text style={styles.defStatChipLabel}>{item.label}</Text>
                            <Text style={styles.defStatChipVal}>{item.val}</Text>
                          </View>
                        ))}
                      </View>
                    );
                  })()}

                  {/* ── H2H — compact text view, not a data block ── */}
                  {isH2H && !hasH2H && (
                    <View style={styles.h2hEmptyState}>
                      <Ionicons name="swap-horizontal-outline" size={13} color={Colors.textTertiary} />
                      <Text style={styles.h2hEmptyText}>
                        No H2H history available for this opponent
                      </Text>
                    </View>
                  )}
                  {isH2H && hasH2H && (() => {
                    const matches = h2hMatches.slice(0, 8);
                    const values = matches.map((m: any) => m.targetStat).filter((v: any) => v != null);
                    const avg = values.length > 0
                      ? values.reduce((a: number, b: number) => a + b, 0) / values.length
                      : null;
                    const over = prediction.line != null
                      ? values.filter((v: number) => v > prediction.line!).length
                      : 0;
                    const opponent = prediction.opponentName || prediction.opponent || 'OPPONENT';
                    return (
                      <View style={styles.h2hTextView}>
                        <Text style={styles.h2hTextSummary}>
                          H2H vs {opponent}  ·  {prediction.h2hPlayerStats?.sampleSize ?? matches.length} apps
                          {avg != null ? `  ·  avg ${avg.toFixed(1)}` : ''}
                          {values.length > 0 && prediction.line != null ? `  ·  ${over}/${values.length} over` : ''}
                        </Text>
                        <View style={styles.h2hTextRows}>
                          {matches.map((m: any, i: number) => {
                            const value = m.targetStat;
                            const isOver = value != null && prediction.line != null && value > prediction.line;
                            const venue = m.venue === 'home' ? 'H' : m.venue === 'away' ? 'A' : '–';
                            const date = m.date ? m.date.slice(5, 10) : '—';
                            return (
                              <Text key={`${m.date || 'h2h'}-${i}`} style={styles.h2hTextRow}>
                                {date}  {venue}  {m.opponent || opponent}  <Text style={{
                                  color: value == null ? Colors.textTertiary : isOver ? Colors.success : Colors.error,
                                  fontWeight: '800',
                                }}>{value != null ? value : '—'}</Text>
                              </Text>
                            );
                          })}
                        </View>
                        {h2hMatches.length > matches.length && (
                          <Text style={styles.h2hTextMore}>+{h2hMatches.length - matches.length} more meetings</Text>
                        )}
                      </View>
                    );
                  })()}
                </View>
              );
            })()}

            {/* ─── MARKET LINE ─── */}
            {prediction.sport === 'soccer' && (() => {
              const pp = (prediction as any).prizePicksContext as {
                marketLine: number; marketTier: string; lineMovement: number;
                tierSignal: string; tierColor: string; ppPlayer: string;
                ppTeam: string; ppOpponent: string; flashLine?: number;
              } | undefined;
              if (!pp || pp.marketLine == null) return null;
              const tier      = (pp.marketTier || 'standard').toLowerCase();
              const tierColor = pp.tierColor || (tier === 'demon' ? '#FF6B35' : tier === 'goblin' ? '#39FF14' : '#60A5FA');
              const tierLabel = tier.toUpperCase();
              const diff      = pp.lineMovement ?? 0;
              const absDiff   = Math.abs(diff);
              const diffLabel = diff === 0
                ? 'Matches market'
                : diff > 0
                  ? `▼ ${absDiff.toFixed(1)} below market (line moved down)`
                  : `▲ ${absDiff.toFixed(1)} above market (line moved up)`;
              const diffColor = diff === 0 ? Colors.textSecondary
                : absDiff >= 2 ? '#FF6B35' : '#60A5FA';
              return (
                <View style={{ marginTop: 8,
                  backgroundColor: '#0D0D0D', borderRadius: 10, padding: 12,
                  borderWidth: 1, borderColor: tierColor + '33' }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                    <Ionicons name="analytics-outline" size={11} color={tierColor} />
                    <Text style={{ fontSize: 10, color: Colors.textSecondary, fontWeight: '700', letterSpacing: 1 }}>
                      MARKET LINE
                    </Text>
                    <View style={{ marginLeft: 'auto', flexDirection: 'row', alignItems: 'center', gap: 5 }}>
                      <Text style={{ fontSize: 14, color: Colors.text, fontWeight: '800' }}>{pp.marketLine}</Text>
                      {pp.flashLine != null && pp.flashLine !== pp.marketLine && (
                        <Text style={{ fontSize: 10, color: '#F59E0B', fontWeight: '700' }}>⚡{pp.flashLine}</Text>
                      )}
                      <View style={{ backgroundColor: tierColor + '22', borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2, borderWidth: 1, borderColor: tierColor + '55' }}>
                        <Text style={{ fontSize: 9, color: tierColor, fontWeight: '800', letterSpacing: 0.5 }}>{tierLabel}</Text>
                      </View>
                    </View>
                  </View>
                  <Text style={{ fontSize: 11, color: diffColor, fontWeight: '600' }}>{diffLabel}</Text>
                  {!!pp.tierSignal && tier !== 'standard' && (
                    <Text style={{ fontSize: 10, color: Colors.textSecondary, marginTop: 4 }}>{pp.tierSignal}</Text>
                  )}
                  {!!pp.ppOpponent && (
                    <Text style={{ fontSize: 10, color: Colors.textTertiary, marginTop: 2 }}>
                      {pp.ppTeam}{pp.ppOpponent ? ` vs ${pp.ppOpponent}` : ''}
                    </Text>
                  )}
                </View>
              );
            })()}

            {/* ─── SPORTSGAMEODDS MARKET REFERENCE ─── */}
            {prediction.sport === 'soccer' && (() => {
              const sgo = (prediction as any).sportsGameOddsContext as {
                source?: string; bookmaker?: string; marketLine?: number;
                lineDifference?: number; marketName?: string; matchedPlayer?: string;
                homeTeam?: string; awayTeam?: string; available?: boolean;
                providerCoverage?: string[]; bookmakers?: Record<string, any>;
              } | undefined;
              if (!sgo || sgo.marketLine == null) return null;
              const diff = Number(sgo.lineDifference ?? 0);
              const absDiff = Math.abs(diff);
              const diffLabel = diff === 0
                ? 'Matches market'
                : diff > 0
                  ? `Market is ${absDiff.toFixed(1)} higher`
                  : `Market is ${absDiff.toFixed(1)} lower`;
              const coverage = (sgo.providerCoverage || [])
                .map((book) => book === 'prizepicks' ? 'PrizePicks' : book === 'underdog' ? 'Underdog' : book)
                .join(' + ');
              return (
                <View style={{ marginTop: 8, backgroundColor: '#10151A', borderRadius: 10, padding: 12,
                  borderWidth: 1, borderColor: '#38BDF833' }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                    <Ionicons name="analytics-outline" size={11} color="#38BDF8" />
                    <Text style={{ fontSize: 10, color: Colors.textSecondary, fontWeight: '700', letterSpacing: 1 }}>
                      {sgo.source || 'SPORTSGAMEODDS'} REFERENCE
                    </Text>
                    <Text style={{ marginLeft: 'auto', fontSize: 14, color: Colors.text, fontWeight: '800' }}>
                      {sgo.marketLine}
                    </Text>
                  </View>
                  <Text style={{ fontSize: 11, color: absDiff >= 2 ? '#F59E0B' : Colors.textSecondary, fontWeight: '600' }}>
                    {diffLabel}{sgo.available === false ? ' · historical/unavailable' : ''}
                  </Text>
                  {!!coverage && (
                    <Text style={{ fontSize: 10, color: Colors.textTertiary, marginTop: 3 }}>
                      {coverage}{sgo.marketName ? ` · ${sgo.marketName}` : ''}
                    </Text>
                  )}
                </View>
              );
            })()}

            {/* ─── REVERSE FORMULA CARD ─── */}
            {prediction.priorSamples != null && prediction.priorSamples >= 3 && (
              <View style={styles.rfCard}>
                <View style={styles.rfHeader}>
                  <View style={styles.rfTitleRow}>
                    <Ionicons name="pulse" size={13} color={Colors.primary} />
                    <Text style={styles.rfTitle}>REVERSE FORMULA</Text>
                  </View>
                  <Text style={styles.rfGamesAnalyzed}>{prediction.priorSamples} GAMES ANALYZED</Text>
                </View>

                {/* SEASON row */}
                {(() => {
                  const _wP = prediction.priorWeight ?? 45;
                  const _wM = prediction.momentumWeight ?? 30;
                  const _wC = prediction.covariateWeight ?? 25;
                  const _wT = (_wP + _wM + _wC) || 100;
                  const rfPriorPct  = Math.round(_wP / _wT * 100);
                  const rfMomPct    = Math.round(_wM / _wT * 100);
                  const rfCovPct    = Math.round(_wC / _wT * 100);
                  return (
                    <>
                      <View style={styles.rfRow}>
                        <Text style={styles.rfRowLabel}>SEASON</Text>
                        <View style={styles.rfBarTrack}>
                          <View style={[styles.rfBarFill, { width: `${rfPriorPct}%`, backgroundColor: '#4DA6FF' }]} />
                        </View>
                        <Text style={[styles.rfPct, { color: '#4DA6FF' }]}>{rfPriorPct}%</Text>
                        <Text style={[styles.rfVal, { color: '#4DA6FF' }]}>
                          {prediction.priorMean != null ? prediction.priorMean.toFixed(1) : '—'}
                        </Text>
                      </View>

                      {/* MOMENTUM row */}
                      <View style={styles.rfRow}>
                        <Text style={styles.rfRowLabel}>MOMENTUM</Text>
                        <View style={styles.rfBarTrack}>
                          <View style={[styles.rfBarFill, { width: `${rfMomPct}%`, backgroundColor: '#FF8C42' }]} />
                        </View>
                        <Text style={[styles.rfPct, { color: '#FF8C42' }]}>{rfMomPct}%</Text>
                        <Text style={[styles.rfVal, { color: '#FF8C42' }]}>
                          {prediction.momentumMean != null ? prediction.momentumMean.toFixed(1) : '—'}
                        </Text>
                      </View>

                      {/* CONTEXT row */}
                      <View style={styles.rfRow}>
                        <Text style={styles.rfRowLabel}>CONTEXT</Text>
                        <View style={styles.rfBarTrack}>
                          <View style={[styles.rfBarFill, { width: `${rfCovPct}%`, backgroundColor: '#A084E8' }]} />
                        </View>
                        <Text style={[styles.rfPct, { color: '#A084E8' }]}>{rfCovPct}%</Text>
                        <Text style={[styles.rfVal, { color: '#A084E8' }]}>
                          {prediction.covariateAdjustment != null
                            ? (prediction.covariateAdjustment >= 0 ? '+' : '') + prediction.covariateAdjustment.toFixed(2)
                            : '—'}
                        </Text>
                      </View>
                    </>
                  );
                })()}

                {/* Badges */}
                <View style={styles.rfBadgeRow}>
                  {prediction.momentumLabel && (
                    <View style={[styles.rfBadge, styles.rfBadgeMomentum]}>
                      <Text style={styles.rfBadgeMomentumText}>
                        {prediction.momentumLabel}
                        {prediction.momentumEffect != null && prediction.momentumEffect !== 0
                          ? ` ${prediction.momentumEffect > 0 ? '+' : ''}${prediction.momentumEffect.toFixed(2)}`
                          : ''}
                      </Text>
                    </View>
                  )}
                  {prediction.recommendation && prediction.recommendation !== 'PASS' && (
                    <View style={[styles.rfBadge, {
                      backgroundColor: prediction.recommendation === 'OVER' ? 'rgba(57,255,20,0.12)' : 'rgba(255,59,48,0.12)',
                      borderColor: prediction.recommendation === 'OVER' ? Colors.success : Colors.error,
                    }]}>
                      <Text style={[styles.rfBadgeText, {
                        color: prediction.recommendation === 'OVER' ? Colors.success : Colors.error,
                      }]}>
                        {prediction.recommendation} {prediction.line}
                      </Text>
                    </View>
                  )}
                </View>

                {/* Math Projection */}
                <View style={styles.rfProjectionRow}>
                  <Text style={styles.rfProjectionLabel}>Math Projection</Text>
                  <View style={styles.rfProjectionRight}>
                    <Text style={styles.rfProjectionVal}>
                      {(prediction.projection ?? prediction.bayesianProjection)?.toFixed(1) ?? '—'}
                    </Text>
                    {(prediction.pOver != null || prediction.pUnder != null) && (() => {
                      const pO = prediction.pOver ?? 0;
                      const pU = prediction.pUnder ?? 0;
                      const showUnder = prediction.recommendation === 'UNDER' || pU > pO;
                      return (
                        <Text style={styles.rfProjectionProb}>
                          {showUnder
                            ? `P(UNDER) ${pU.toFixed(1)}%`
                            : `P(OVER) ${pO.toFixed(1)}%`}
                        </Text>
                      );
                    })()}
                  </View>
                </View>
              </View>
            )}

            {/* ─── CS2 ALGORITHM BREAKDOWN CARD ─── */}
            {prediction.sport === 'cs2' && (() => {
              const bm2  = (prediction as any).bayesianMetrics || {};
              const tm2  = bm2.tacticalMetrics || {};
              const pO2  = (prediction as any).pOver  as number | undefined;
              const pU2  = (prediction as any).pUnder as number | undefined;
              const n2   = bm2.sampleSize as number | undefined;
              const sf2  = (prediction as any).streakFlag as string | undefined;
              const isO2 = pO2 != null && pU2 != null && pO2 >= pU2;
              const summary2 = (prediction as any).sharpSummary || '';
              const body2    = (prediction as any).reasoning || '';
              const role2    = tm2.roleClassification || '';
              const roleLabels2: Record<string,string> = {
                awper:'AWPer', entry_fragger:'Entry Fragger', star_rifler:'Star Rifler',
                lurker:'Lurker', igl:'IGL', support:'Support', rifler:'Rifler',
              };
              const roleStr2 = roleLabels2[role2] || role2;
              const map2  = tm2.mapAwareness || (prediction as any).mapName || '';
              const ct2   = tm2.playerTeamStartsCt;
              const adrCareer2 = tm2.careerAdr as number | null | undefined;
              const adrRecent2 = tm2.recentAdr  as number | null | undefined;
              const adrDelta2  = (adrCareer2 != null && adrRecent2 != null) ? (adrRecent2 - adrCareer2) : null;
              const underdogM2 = tm2.underdogCompress as number | undefined;
              const formBias2  = tm2.formWindowBiasMult as number | undefined;
              const kprFactor2 = tm2.mapKprFactor as number | undefined;
              const ctWin2     = tm2.mapCtWinRate as number | undefined;
              const h2hG2      = tm2.h2hGames as number | undefined;
              const h2hAvg2    = tm2.h2hAvgKills as number | null | undefined;
              const streakAdj2 = tm2.streakPAdj as number | undefined;
              const kprCov2    = tm2.kprCoV as number | undefined;
              const hsAvg2     = tm2.avgHeadshotPct as number | null | undefined;

              const metricRows2: { label: string; val: string; color?: string }[] = [];
              if (n2 != null)       metricRows2.push({ label:'SAMPLE', val:`${n2} maps` });
              if (roleStr2)         metricRows2.push({ label:'ROLE', val: roleStr2,
                color: role2==='awper'?'#A084E8': role2==='entry_fragger'?'#FF8C42': role2==='star_rifler'?Colors.primary: Colors.textSecondary });
              if (hsAvg2 != null)   metricRows2.push({ label:'HS%', val:`${hsAvg2.toFixed(0)}%`,
                color: hsAvg2 < 28 ? '#A084E8' : undefined });
              if (adrCareer2 != null && adrRecent2 != null)
                metricRows2.push({ label:'ADR TREND', val:`${adrRecent2.toFixed(0)} vs ${adrCareer2.toFixed(0)}`,
                  color: adrDelta2! > 3 ? Colors.primary : adrDelta2! < -3 ? '#FF6B35' : Colors.textSecondary });
              if (map2) {
                let mapStr = map2.replace('de_','');
                if (kprFactor2 != null && Math.abs(kprFactor2-1)>0.01) mapStr += ` (KPR ×${kprFactor2.toFixed(2)})`;
                metricRows2.push({ label:'MAP', val: mapStr });
              }
              if (ct2 !== undefined && ct2 !== null && ctWin2 != null)
                metricRows2.push({ label:'SIDE', val:`${ct2?'CT':'T'} · map CT ${(ctWin2*100).toFixed(1)}%`,
                  color: (ct2 && ctWin2>0.52) ? Colors.primary : (!ct2 && ctWin2<0.50) ? Colors.primary : Colors.textSecondary });
              if (formBias2 != null && Math.abs(formBias2-1)>0.01)
                metricRows2.push({ label:'FORM', val:`${formBias2>1?'🔥 Hot':'❄️ Cold'} ×${formBias2.toFixed(2)}`,
                  color: formBias2>1 ? Colors.primary : '#60A5FA' });
              if (underdogM2 != null && Math.abs(underdogM2-1)>0.01)
                metricRows2.push({ label:'RANK GAP', val:`×${underdogM2.toFixed(2)} ${underdogM2<1?'(underdog)':'(fav)'}`,
                  color: underdogM2<1?'#FF6B35': Colors.primary });
              if (h2hG2 != null && h2hG2 >= 2)
                metricRows2.push({ label:'H2H', val:`${h2hG2}g avg ${h2hAvg2??'?'}` });
              if (sf2)              metricRows2.push({ label:'STREAK', val: sf2.replace(/🔥|❄️/g,'').trim(),
                color: sf2.includes('OVER') ? Colors.primary : '#60A5FA' });
              if (kprCov2 != null)  metricRows2.push({ label:'KPR CoV', val:kprCov2.toFixed(3),
                color: kprCov2>0.5?'#A084E8':undefined });

              const borderC2 = isO2 ? Colors.primary : '#FF6B35';
              return (
                <View style={[styles.scoutCard, { borderColor: borderC2 + '44' }]}>
                  {/* Header */}
                  <View style={styles.scoutHeader}>
                    <Ionicons name="analytics" size={13} color={Colors.primary} />
                    <Text style={styles.scoutTitle}>CS2 ENGINE v4</Text>
                    {n2 != null && (
                      <View style={[styles.mfSamplesBadge, { marginLeft: 'auto' }]}>
                        <Text style={styles.mfSamplesText}>{n2} MAPS</Text>
                      </View>
                    )}
                  </View>

                  {/* P(OVER) / P(UNDER) bar */}
                  {pO2 != null && pU2 != null && (
                    <View style={[styles.mfProbSection, { marginTop: 6 }]}>
                      <View style={styles.mfProbTrack}>
                        <View style={[styles.mfProbOverFill, { flex: pO2 }]} />
                        <View style={styles.mfProbDivider} />
                        <View style={[styles.mfProbUnderFill, { flex: pU2 }]} />
                      </View>
                      <View style={styles.mfProbLabels}>
                        <View style={styles.mfProbLabelLeft}>
                          <Text style={[styles.mfProbPct, { color: Colors.primary }]}>{pO2.toFixed(1)}%</Text>
                          <Text style={styles.mfProbDir}>OVER</Text>
                        </View>
                        <Text style={styles.mfProbVs}>MC</Text>
                        <View style={styles.mfProbLabelRight}>
                          <Text style={[styles.mfProbPct, { color: '#FF6B35' }]}>{pU2.toFixed(1)}%</Text>
                          <Text style={styles.mfProbDir}>UNDER</Text>
                        </View>
                      </View>
                    </View>
                  )}

                  {/* Metrics grid */}
                  {metricRows2.length > 0 && (
                    <View style={{ flexDirection:'row', flexWrap:'wrap', gap:6, marginTop:8 }}>
                      {metricRows2.map((row, i) => (
                        <View key={i} style={{ flexDirection:'row', gap:4, alignItems:'center',
                          backgroundColor:'#181818', borderRadius:5, paddingHorizontal:7, paddingVertical:4,
                        }}>
                          <Text style={{ fontSize:9, color:Colors.textTertiary, fontFamily:'JetBrainsMono_700Bold', letterSpacing:0.8 }}>{row.label}</Text>
                          <Text style={{ fontSize:10, color: row.color || Colors.textSecondary, fontFamily:'JetBrainsMono_600SemiBold' }}>{row.val}</Text>
                        </View>
                      ))}
                    </View>
                  )}

                  {/* Structured sharp summary */}
                  {summary2 ? (
                    <Text style={[styles.scoutSectionBody, { color:Colors.text, fontWeight:'600', marginTop:10, marginBottom:6 }]}>
                      {summary2}
                    </Text>
                  ) : null}
                  {body2 ? (
                    <Text style={[styles.scoutSectionBody, { color:Colors.textSecondary }]}>{body2}</Text>
                  ) : null}
                  {streakAdj2 != null && streakAdj2 !== 0 && (
                    <Text style={{ fontSize:10, color:Colors.textTertiary, marginTop:6, fontFamily:'JetBrainsMono_400Regular' }}>
                      Streak momentum adj: {streakAdj2>0?'+':''}{streakAdj2}% p_over
                    </Text>
                  )}
                </View>
              );
            })()}

            {/* ─── MLB ENGINE CARD ─── */}
            {prediction.sport === 'mlb' && (() => {
              const bmlb = (prediction as any).bayesianMetrics || {};
              const pO = (prediction as any).pOver  as number | undefined;
              const pU = (prediction as any).pUnder as number | undefined;
              const n  = (bmlb.priorSamples ?? (prediction as any).priorSamples) as number | undefined;
              const priorMean = bmlb.priorMean ?? (prediction as any).priorMean as number | undefined;
              const parkPct   = bmlb.parkFactorPct as number | undefined;
              const platoon   = bmlb.platoonSplitMult as number | undefined;
              const era       = bmlb.eraFactor as number | undefined;
              const babip     = bmlb.babipMult as number | undefined;
              const krate     = bmlb.kRateMult as number | undefined;
              const gameTotal = bmlb.gameTotalFactor as number | undefined;
              const lineup    = bmlb.lineupPositionMult as number | undefined;
              const sf        = (prediction as any).streakFlag as string | undefined;
              const isO = prediction.recommendation === 'OVER';
              const borderC = isO ? Colors.primary : '#FF6B35';

              type MetRow = { label: string; val: string; color?: string };
              const rows: MetRow[] = [];

              if (parkPct != null)
                rows.push({ label: 'PARK', val: `${parkPct >= 0 ? '+' : ''}${parkPct.toFixed(1)}%`, color: parkPct >= 0 ? Colors.primary : Colors.error });
              if (platoon != null && Math.abs(platoon - 1) > 0.005)
                rows.push({ label: 'PLATOON', val: `×${platoon.toFixed(2)}`, color: platoon > 1 ? Colors.primary : Colors.error });
              if (era != null && Math.abs(era - 1) > 0.005)
                rows.push({ label: 'ERA', val: `×${era.toFixed(2)}`, color: era > 1 ? Colors.primary : Colors.error });
              if (babip != null && Math.abs(babip - 1) > 0.005)
                rows.push({ label: 'BABIP', val: `×${babip.toFixed(2)}`, color: babip > 1 ? Colors.primary : Colors.error });
              if (krate != null && Math.abs(krate - 1) > 0.005)
                rows.push({ label: 'K-RATE', val: `×${krate.toFixed(2)}`, color: krate > 1 ? Colors.primary : Colors.error });
              if (gameTotal != null && Math.abs(gameTotal - 1) > 0.005)
                rows.push({ label: 'O/U', val: `×${gameTotal.toFixed(2)}`, color: gameTotal > 1 ? Colors.primary : Colors.error });
              if (lineup != null && Math.abs(lineup - 1) > 0.005)
                rows.push({ label: 'LINEUP', val: `×${lineup.toFixed(2)}`, color: lineup > 1 ? Colors.primary : Colors.error });

              return (
                <View style={[styles.scoutCard, { borderColor: borderC + '44' }]}>
                  <View style={styles.scoutHeader}>
                    <Ionicons name="analytics" size={13} color={Colors.primary} />
                    <Text style={styles.scoutTitle}>MLB ENGINE</Text>
                    {n != null && n > 0 && (
                      <View style={[styles.mfSamplesBadge, { marginLeft: 'auto' }]}>
                        <Text style={styles.mfSamplesText}>{n} GAMES</Text>
                      </View>
                    )}
                  </View>

                  {/* P(OVER) / P(UNDER) bar */}
                  {pO != null && pU != null && (
                    <View style={[styles.mfProbSection, { marginTop: 6 }]}>
                      <View style={styles.mfProbBar}>
                        <View style={[styles.mfProbFillOver,  { flex: pO / 100 }]} />
                        <View style={[styles.mfProbFillUnder, { flex: pU / 100 }]} />
                      </View>
                      <View style={styles.mfProbLabels}>
                        <Text style={[styles.mfProbLabel, { color: Colors.primary }]}>OVER {pO.toFixed(1)}%</Text>
                        <Text style={[styles.mfProbLabel, { color: Colors.error,   textAlign: 'right' }]}>UNDER {pU.toFixed(1)}%</Text>
                      </View>
                    </View>
                  )}

                  {/* Season baseline */}
                  {priorMean != null && (
                    <Text style={{ fontSize: 11, color: Colors.textSecondary, marginTop: 6, fontFamily: 'JetBrainsMono_400Regular' }}>
                      Season baseline: {priorMean.toFixed(2)}
                    </Text>
                  )}

                  {/* Multiplier rows */}
                  {rows.length > 0 && (
                    <View style={{ marginTop: 8, gap: 4 }}>
                      {rows.map((r, i) => (
                        <View key={i} style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 2, borderBottomWidth: i < rows.length - 1 ? 1 : 0, borderBottomColor: '#1A1A1A' }}>
                          <Text style={{ fontSize: 10, color: Colors.textSecondary, fontFamily: 'JetBrainsMono_400Regular', letterSpacing: 0.5 }}>{r.label}</Text>
                          <Text style={{ fontSize: 11, fontWeight: '700', color: r.color ?? Colors.text, fontFamily: 'JetBrainsMono_400Regular' }}>{r.val}</Text>
                        </View>
                      ))}
                    </View>
                  )}

                  {/* Streak flag */}
                  {sf && sf !== 'NONE' && (
                    <Text style={{ fontSize: 10, color: sf === 'OVER_STREAK' ? Colors.primary : '#60A5FA', marginTop: 6, fontFamily: 'JetBrainsMono_400Regular' }}>
                      {sf === 'OVER_STREAK' ? '🔥 OVER streak last 4+ games' : '❄️ UNDER streak last 4+ games'}
                    </Text>
                  )}
                </View>
              );
            })()}

            {/* ─── NBA / NHL / NFL ENGINE CARD ─── */}
            {prediction?.sport && ['nba', 'nhl', 'nfl'].includes(prediction.sport) && (() => {
              const bx   = (prediction as any).bayesianMetrics || {};
              const pO   = (prediction as any).pOver  as number | undefined;
              const pU   = (prediction as any).pUnder as number | undefined;
              const n    = bx.sampleSize ?? (prediction as any).priorSamples as number | undefined;
              const pm   = bx.priorMean  ?? (prediction as any).priorMean   as number | undefined;
              const sf   = (prediction as any).streakFlag as string | undefined;
              const kf   = ((prediction as any).keyFactors ?? []) as string[];
              const isO  = prediction.recommendation === 'OVER';
              const borderC = isO ? Colors.primary : '#FF6B35';
              const sportLabel = prediction.sport === 'nba' ? 'NBA' : prediction.sport === 'nhl' ? 'NHL' : 'NFL';

              return (
                <View style={[styles.scoutCard, { borderColor: borderC + '44' }]}>
                  <View style={styles.scoutHeader}>
                    <Ionicons name="analytics" size={13} color={Colors.primary} />
                    <Text style={styles.scoutTitle}>{sportLabel} ENGINE</Text>
                    {n != null && n > 0 && (
                      <View style={[styles.mfSamplesBadge, { marginLeft: 'auto' }]}>
                        <Text style={styles.mfSamplesText}>{n} GAMES</Text>
                      </View>
                    )}
                  </View>

                  {/* P(OVER) / P(UNDER) bar */}
                  {pO != null && pU != null && (
                    <View style={[styles.mfProbSection, { marginTop: 6 }]}>
                      <View style={styles.mfProbBar}>
                        <View style={[styles.mfProbFillOver,  { flex: pO / 100 }]} />
                        <View style={[styles.mfProbFillUnder, { flex: pU / 100 }]} />
                      </View>
                      <View style={styles.mfProbLabels}>
                        <Text style={[styles.mfProbLabel, { color: Colors.primary }]}>OVER {pO.toFixed(1)}%</Text>
                        <Text style={[styles.mfProbLabel, { color: Colors.error,   textAlign: 'right' }]}>UNDER {pU.toFixed(1)}%</Text>
                      </View>
                    </View>
                  )}

                  {/* Season baseline */}
                  {pm != null && (
                    <Text style={{ fontSize: 11, color: Colors.textSecondary, marginTop: 6, fontFamily: 'JetBrainsMono_400Regular' }}>
                      Season baseline: {pm.toFixed(1)}
                    </Text>
                  )}

                  {/* Streak flag */}
                  {sf && sf !== 'NONE' && (
                    <Text style={{ fontSize: 10, color: sf === 'OVER_STREAK' ? Colors.primary : '#60A5FA', marginTop: 6, fontFamily: 'JetBrainsMono_400Regular' }}>
                      {sf === 'OVER_STREAK' ? '🔥 OVER streak last 4+ games' : '❄️ UNDER streak last 4+ games'}
                    </Text>
                  )}

                  {/* Structured key factors */}
                  {kf.length > 0 && (
                    <View style={{ marginTop: 8, gap: 5 }}>
                      {kf.slice(0, 4).map((factor, i) => (
                        <View key={i} style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 6 }}>
                          <Text style={{ fontSize: 10, color: Colors.primary, marginTop: 1 }}>▸</Text>
                          <Text style={{ flex: 1, fontSize: 11, color: Colors.text, lineHeight: 15 }}>{factor}</Text>
                        </View>
                      ))}
                    </View>
                  )}
                </View>
              );
            })()}

            {/* ─── FULL STRUCTURED ANALYSIS ─── */}
            {false && (tacticalAnalysis || prediction.tacticalBreakdown || prediction.reasoning || prediction.sharpSummary) && (() => {
              const isOver = prediction.recommendation === 'OVER';
              const isUnder = prediction.recommendation === 'UNDER';
              const recColor = isOver ? Colors.success : isUnder ? Colors.error : Colors.textSecondary;
              const narrative = tacticalAnalysis
                || prediction.tacticalBreakdown
                || prediction.reasoning
                || prediction.sharpSummary
                || '';

              // Render markdown-lite: bold (**text**), bullets, headers, paragraphs
              const renderLine = (raw: string, key: number) => {
                const trimmed = raw.trimEnd();
                if (!trimmed) return null;

                const isH = /^\*{2}[^*]/.test(trimmed) && trimmed.endsWith('**') && !trimmed.slice(2, -2).includes('**');
                const isBullet = /^\s{0,3}[*\-]\s{1,3}/.test(trimmed) && !isH;
                const isSubBullet = /^\s{4,}[*\-]\s/.test(trimmed);

                const clean = trimmed
                  .replace(/^\s{0,6}[*\-]\s+/, '')
                  .replace(/^\*{2}(.*)\*{2}$/, '$1')
                  .trim();

                // Inline bold splitter
                const parts = clean.split(/(\*\*[^*]+\*\*)/g);
                const nodes = parts.map((p, i) =>
                  p.startsWith('**') && p.endsWith('**')
                    ? <Text key={i} style={{ fontWeight: '800', color: Colors.text }}>{p.slice(2, -2)}</Text>
                    : <Text key={i}>{p}</Text>
                );

                if (isH) {
                  return (
                    <Text key={key} style={{ fontSize: 11.5, fontWeight: '800', color: recColor, letterSpacing: 0.6, marginTop: 12, marginBottom: 3 }}>
                      {clean.toUpperCase()}
                    </Text>
                  );
                }
                if (isSubBullet) {
                  return (
                    <Text key={key} style={{ fontSize: 12, color: Colors.textSecondary, lineHeight: 18, marginLeft: 16, marginBottom: 1 }}>
                      {'◦ '}{nodes}
                    </Text>
                  );
                }
                if (isBullet) {
                  return (
                    <Text key={key} style={{ fontSize: 12, color: Colors.textSecondary, lineHeight: 18, marginLeft: 8, marginBottom: 1 }}>
                      {'• '}{nodes}
                    </Text>
                  );
                }
                return (
                  <Text key={key} style={{ fontSize: 12, color: Colors.textSecondary, lineHeight: 18, marginBottom: 2 }}>
                    {nodes}
                  </Text>
                );
              };

              const lines = narrative.split('\n');

              return (
                <View style={[styles.scoutCard, { borderColor: recColor + '33', marginTop: 10 }]}>
                  <View style={styles.scoutHeader}>
                    <Ionicons name="chatbubble-ellipses-outline" size={13} color={Colors.primary} />
                    <Text style={styles.scoutTitle}>
                      MODEL EXPLANATION
                    </Text>
                    <Text style={{ fontSize: 9, color: Colors.textTertiary, marginLeft: 'auto', fontWeight: '600' }}>
                      COMPACT MATCH CONTEXT
                    </Text>
                  </View>
                  <View style={{ gap: 0, marginTop: 4 }}>
                    {lines.map((line, i) => renderLine(line, i))}
                  </View>
                </View>
              );
            })()}

            {/* ─── MANAGER CHANGE WARNING BANNER ─── */}
            {(prediction as any)?.managerContext?.isRecent === true && (() => {
              const mc = (prediction as any).managerContext;
              return (
                <View style={{
                  flexDirection: 'row', alignItems: 'center', gap: 8,
                  backgroundColor: 'rgba(245,158,11,0.1)',
                  borderWidth: 1, borderColor: 'rgba(245,158,11,0.45)',
                  borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8,
                  marginTop: 8,
                }}>
                  <Ionicons name="alert-circle-outline" size={15} color="#F59E0B" />
                  <View style={{ flex: 1 }}>
                    <Text style={{ fontSize: 10, fontWeight: '800', color: '#F59E0B', letterSpacing: 1 }}>
                      MANAGER CHANGE
                    </Text>
                    {mc.coachName ? (
                      <Text style={{ fontSize: 11, color: 'rgba(255,255,255,0.6)', marginTop: 1 }}>
                        {mc.coachName} · projection uses post-change logs only
                      </Text>
                    ) : null}
                  </View>
                </View>
              );
            })()}

            {/* ─── LINEUP / TACTICAL PITCH ─── */}
            {prediction.sport === 'soccer' && prediction.lineup && (
              <View style={{ marginTop: 12 }}>
                <PitchDiagram lineup={prediction.lineup} highlightPlayerName={prediction.playerName} />
              </View>
            )}

            {/* ─── RISK & CONGESTION SIGNALS ─── */}
            {(prediction.riskSignals || prediction.congestion) && (() => {
              const risk = prediction.riskSignals;
              const cong = prediction.congestion;
              const riskColor = risk?.redCardRisk === 'high' ? '#FF6B35'
                : risk?.redCardRisk === 'elevated' ? '#FFB020'
                : Colors.textSecondary;
              const fatigueColor = cong?.fatigueFlag === 'high' ? '#FF6B35'
                : cong?.fatigueFlag === 'moderate' ? '#FFB020'
                : Colors.textSecondary;
              return (
                <View style={{ flexDirection: 'row', gap: 10, marginTop: 14 }}>
                  {risk && (
                    <View style={{ flex: 1, borderRadius: 18, overflow: 'hidden', borderWidth: 1, borderColor: riskColor + '33' }}>
                      <LinearGradient
                        colors={[riskColor + '1A', 'rgba(10,10,10,0.6)']}
                        start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                        style={{ padding: 14 }}
                      >
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
                          <Ionicons name="alert-circle-outline" size={13} color={riskColor} />
                          <Text style={{ fontSize: 9, fontWeight: '800', color: riskColor, letterSpacing: 0.6 }}>
                            DISMISSAL RISK
                          </Text>
                        </View>
                        <Text style={{ fontSize: 17, fontWeight: '800', color: Colors.text, marginTop: 7 }}>
                          {(risk.redCardRisk || 'low').toUpperCase()}
                        </Text>
                        {risk.note ? (
                          <Text style={{ fontSize: 10, color: Colors.textTertiary, marginTop: 4, lineHeight: 14 }}>
                            {risk.note}
                          </Text>
                        ) : null}
                      </LinearGradient>
                    </View>
                  )}
                  {cong && (
                    <View style={{ flex: 1, borderRadius: 18, overflow: 'hidden', borderWidth: 1, borderColor: fatigueColor + '33' }}>
                      <LinearGradient
                        colors={[fatigueColor + '1A', 'rgba(10,10,10,0.6)']}
                        start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                        style={{ padding: 14 }}
                      >
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
                          <Ionicons name="time-outline" size={13} color={fatigueColor} />
                          <Text style={{ fontSize: 9, fontWeight: '800', color: fatigueColor, letterSpacing: 0.6 }}>
                            FIXTURE LOAD
                          </Text>
                        </View>
                        <Text style={{ fontSize: 17, fontWeight: '800', color: Colors.text, marginTop: 7 }}>
                          {cong.teamRestDays != null ? `${cong.teamRestDays}d rest` : '—'}
                        </Text>
                        {cong.teamGamesIn14d != null && (
                          <Text style={{ fontSize: 10, color: Colors.textTertiary, marginTop: 4, lineHeight: 14 }}>
                            {cong.teamGamesIn14d} games in last 14 days
                          </Text>
                        )}
                      </LinearGradient>
                    </View>
                  )}
                </View>
              );
            })()}

            {/* ─── WORLD CUP CALIBRATION NOTICE ─── */}
            {prediction.isWorldCup && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 10,
                backgroundColor: '#60A5FA15', borderRadius: 8, paddingHorizontal: 10, paddingVertical: 7,
                borderWidth: 1, borderColor: '#60A5FA30' }}>
                <Ionicons name="trophy-outline" size={12} color="#60A5FA" />
                <Text style={{ fontSize: 10, color: '#60A5FA', fontWeight: '600', flex: 1 }}>
                  World Cup pick — confidence tracked separately with a conservative cap.
                </Text>
              </View>
            )}

            {/* ─── MARKET LINE (RENDERED ABOVE) ─── */}

            {/* ─── GAME LOG GRID (RENDERED ABOVE) ─── */}

            </>)}

            {saveError && (
              <View style={styles.inlineError}>
                <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                <Text style={styles.inlineErrorText}>{saveError}</Text>
                {saveError.toLowerCase().includes('session') && (
                   <TouchableOpacity onPress={async () => { await logout(); router.replace('/auth'); }} style={{ marginLeft: 8 }}>
                    <Text style={{ color: Colors.primary, fontSize: 12, fontWeight: '700' }}>Sign Out</Text>
                  </TouchableOpacity>
                )}
              </View>
            )}

            </View>{/* end captureContainer */}

            <TouchableOpacity
                style={[{
                  backgroundColor: prediction.recommendation === 'OVER' ? Colors.primary : '#FF3B30',
                  borderRadius: 18, paddingVertical: 16, paddingHorizontal: 24,
                  alignItems: 'center', justifyContent: 'center', marginTop: 10, gap: 1,
                  shadowColor: prediction.recommendation === 'OVER' ? Colors.primary : '#FF3B30',
                  shadowOffset: { width: 0, height: 10 }, shadowOpacity: 0.45, shadowRadius: 22,
                  elevation: 10,
                }, (saving || pickSaved) && { opacity: 0.65 }]}
                onPress={handleSavePick}
                disabled={saving || pickSaved}
                activeOpacity={0.82}
              >
                {saving ? (
                  <ActivityIndicator color="#000" size="small" />
                ) : pickSaved ? (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    <Ionicons name="checkmark-circle" size={20} color="#000" />
                    <Text style={{ color: '#000', fontWeight: '800', fontSize: 17 }}>Saved!</Text>
                  </View>
                ) : (
                  <>
                    <Text style={{ color: 'rgba(0,0,0,0.45)', fontWeight: '700', fontSize: 10, letterSpacing: 2.5, textTransform: 'uppercase' }}>
                      Save Pick
                    </Text>
                    <Text style={{ color: '#000', fontWeight: '900', fontSize: 26, letterSpacing: 0.5, lineHeight: 30 }}>
                      {`${prediction.recommendation}  ${prediction.line}`}
                    </Text>
                    {(prediction.pOver != null || prediction.pUnder != null) && (
                      <Text style={{ color: 'rgba(0,0,0,0.42)', fontWeight: '600', fontSize: 11, marginTop: 1 }}>
                        {prediction.recommendation === 'OVER'
                          ? `${(prediction.pOver ?? 0).toFixed(1)}%`
                          : prediction.recommendation === 'UNDER'
                            ? `${(prediction.pUnder ?? 0).toFixed(1)}%`
                            : 'Saved as calibration-only'
                        } model probability
                      </Text>
                    )}
                  </>
                )}
            </TouchableOpacity>

            {/* Secondary actions row */}
            <View style={{ flexDirection: 'row', gap: 10, marginTop: 10 }}>
              <TouchableOpacity
                style={[{
                  flex: 1, height: 44, borderRadius: 12, borderWidth: 1.5,
                  borderColor: Colors.primary, flexDirection: 'row', alignItems: 'center',
                  justifyContent: 'center', gap: 6,
                  backgroundColor: 'rgba(57,255,20,0.06)',
                }, savingImage && { opacity: 0.6 }]}
                onPress={handleSaveImage}
                disabled={savingImage}
                activeOpacity={0.85}
              >
                {savingImage
                  ? <ActivityIndicator color={Colors.primary} size="small" />
                  : <>
                      <Ionicons name="download-outline" size={14} color={Colors.primary} />
                      <Text style={{ color: Colors.primary, fontWeight: '700', fontSize: 13 }}>Save Image</Text>
                    </>
                }
              </TouchableOpacity>

              <TouchableOpacity
                style={{
                  flex: 1, height: 44, borderRadius: 12, borderWidth: 1,
                  borderColor: '#2a2a2a', alignItems: 'center', justifyContent: 'center',
                  backgroundColor: 'rgba(255,255,255,0.03)',
                }}
                onPress={reset}
                activeOpacity={0.85}
              >
                <Text style={{ color: Colors.textSecondary, fontWeight: '600', fontSize: 13 }}>Analyze Another</Text>
              </TouchableOpacity>
            </View>
          </Reanimated.View>
          </>
        )}

        {/* ─── SAVED ─── */}
        {phase === 'saved' && (
          <View style={styles.savedState}>
            <View style={styles.savedCheck}>
              <Ionicons name="checkmark" size={36} color="#000" />
            </View>
            <Text style={styles.savedTitle}>Pick Saved!</Text>
            <Text style={styles.savedSub}>
              {prediction?.recommendation} · {prediction?.playerName}{'\n'}
              {PROP_LABELS[prediction?.propType || ''] || prediction?.propType} · Line {prediction?.line}
            </Text>
            <TouchableOpacity style={styles.viewPicksBtn} onPress={() => router.push('/(tabs)/picks')} activeOpacity={0.85}>
              <Ionicons name="bookmark" size={16} color="#000" />
              <Text style={styles.viewPicksBtnText}>View in My Picks</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.newBtn} onPress={reset}>
              <Text style={styles.newBtnText}>Analyze Another</Text>
            </TouchableOpacity>
          </View>
        )}
        </View>
      </ScrollView>

      {/* Prop Picker Modal — SCAN mode correction */}
      <Modal visible={showPropEditScan} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setShowPropEditScan(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>Select Prop Type</Text>
            <ScrollView>
              {PROP_TYPES.map(p => (
                <TouchableOpacity
                  key={p.value}
                  style={[styles.modalItem, p.value === scanResult?.propType && styles.modalItemActive]}
                  onPress={() => {
                    setScanResult(prev => prev ? { ...prev, propType: p.value } : prev);
                    setShowPropEditScan(false);
                    Haptics.selectionAsync();
                  }}
                >
                  <Text style={[styles.modalItemText, p.value === scanResult?.propType && styles.modalItemTextActive]}>{p.label}</Text>
                  {p.value === scanResult?.propType && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* League Picker Modal — SCAN mode correction. Backed by full 1228-league
          fuzzy search so users can pick women's leagues, lower divisions, etc. */}
      <LeaguePickerModal
        visible={showLeagueEditScan}
        onClose={() => setShowLeagueEditScan(false)}
        selectedId={scanResult?.leagueId}
        onSelect={(l) => {
          setScanResult(prev => prev ? { ...prev, leagueId: l.id, leagueName: l.name } : prev);
          Haptics.selectionAsync();
        }}
        title="Correct League"
      />

      {/* CS2 Prop Picker Modal */}
      <Modal visible={cs2ShowPropPicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setCs2ShowPropPicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>CS2 Prop Type</Text>
            <ScrollView>
              {CS2_PROP_TYPES.map(p => (
                <TouchableOpacity
                  key={p.value}
                  style={[styles.modalItem, p.value === cs2PropType && styles.modalItemActive]}
                  onPress={() => { setCs2PropType(p.value); setCs2ShowPropPicker(false); Haptics.selectionAsync(); }}
                >
                  <Text style={[styles.modalItemText, p.value === cs2PropType && styles.modalItemTextActive]}>{p.label}</Text>
                  {p.value === cs2PropType && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* WTA Prop Picker Modal */}
      <Modal visible={wtaShowPropPicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setWtaShowPropPicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>WTA Prop Type</Text>
            <ScrollView>
              {WTA_PROP_TYPES.map(p => (
                <TouchableOpacity
                  key={p.value}
                  style={[styles.modalItem, p.value === wtaPropType && styles.modalItemActive]}
                  onPress={() => { setWtaPropType(p.value); setWtaShowPropPicker(false); Haptics.selectionAsync(); }}
                >
                  <Text style={[styles.modalItemText, p.value === wtaPropType && styles.modalItemTextActive]}>{p.label}</Text>
                  {p.value === wtaPropType && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* WTA Surface Picker Modal */}
      <Modal visible={wtaShowSurfacePicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setWtaShowSurfacePicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>Surface</Text>
            <ScrollView>
              {WTA_SURFACES.map(s => (
                <TouchableOpacity
                  key={s}
                  style={[styles.modalItem, s === wtaSurface && styles.modalItemActive]}
                  onPress={() => { setWtaSurface(s); setWtaShowSurfacePicker(false); Haptics.selectionAsync(); }}
                >
                  <Text style={[styles.modalItemText, s === wtaSurface && styles.modalItemTextActive]}>{s}</Text>
                  {s === wtaSurface && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* WTA Round Picker Modal */}
      <Modal visible={wtaShowRoundPicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setWtaShowRoundPicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>Round</Text>
            <ScrollView>
              {WTA_ROUNDS.map(r => (
                <TouchableOpacity
                  key={r}
                  style={[styles.modalItem, r === wtaRound && styles.modalItemActive]}
                  onPress={() => { setWtaRound(r); setWtaShowRoundPicker(false); Haptics.selectionAsync(); }}
                >
                  <Text style={[styles.modalItemText, r === wtaRound && styles.modalItemTextActive]}>{r}</Text>
                  {r === wtaRound && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* NBA Prop Picker Modal */}
      <Modal visible={nbaShowPropPicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setNbaShowPropPicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>NBA Prop Type</Text>
            <ScrollView>
              {NBA_PROP_TYPES.map(p => (
                <TouchableOpacity
                  key={p.value}
                  style={[styles.modalItem, p.value === nbaPropType && styles.modalItemActive]}
                  onPress={() => { setNbaPropType(p.value); setNbaShowPropPicker(false); Haptics.selectionAsync(); }}
                >
                  <Text style={[styles.modalItemText, p.value === nbaPropType && styles.modalItemTextActive]}>{p.label}</Text>
                  {p.value === nbaPropType && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* NFL Prop Picker Modal */}
      <Modal visible={nflShowPropPicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setNflShowPropPicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>NFL Prop Type</Text>
            <ScrollView>
              {NFL_PROP_TYPES.map(p => (
                <TouchableOpacity
                  key={p.value}
                  style={[styles.modalItem, p.value === nflPropType && styles.modalItemActive]}
                  onPress={() => { setNflPropType(p.value); setNflShowPropPicker(false); Haptics.selectionAsync(); }}
                >
                  <Text style={[styles.modalItemText, p.value === nflPropType && styles.modalItemTextActive]}>{p.label}</Text>
                  {p.value === nflPropType && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* NHL Prop Picker Modal */}
      <Modal visible={nhlShowPropPicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setNhlShowPropPicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>NHL Prop Type</Text>
            <ScrollView>
              {NHL_PROP_TYPES.map(p => (
                <TouchableOpacity
                  key={p.value}
                  style={[styles.modalItem, p.value === nhlPropType && styles.modalItemActive]}
                  onPress={() => { setNhlPropType(p.value); setNhlShowPropPicker(false); Haptics.selectionAsync(); }}
                >
                  <Text style={[styles.modalItemText, p.value === nhlPropType && styles.modalItemTextActive]}>{p.label}</Text>
                  {p.value === nhlPropType && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* MLB Prop Picker Modal */}
      <Modal visible={mlbShowPropPicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setMlbShowPropPicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>MLB Prop Type</Text>
            <ScrollView>
              {MLB_PROP_TYPES.map(p => (
                <TouchableOpacity
                  key={p.value}
                  style={[styles.modalItem, p.value === mlbPropType && styles.modalItemActive]}
                  onPress={() => { setMlbPropType(p.value); setMlbShowPropPicker(false); Haptics.selectionAsync(); }}
                >
                  <Text style={[styles.modalItemText, p.value === mlbPropType && styles.modalItemTextActive]}>{p.label}</Text>
                  {p.value === mlbPropType && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* Prop Picker Modal — MANUAL mode */}
      <Modal visible={showPropPicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setShowPropPicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>Prop Type</Text>
            <ScrollView>
              {PROP_TYPES.map(p => (
                <TouchableOpacity
                  key={p.value}
                  style={[styles.modalItem, p.value === propType && styles.modalItemActive]}
                  onPress={() => { setPropType(p.value); setShowPropPicker(false); }}
                >
                  <Text style={[styles.modalItemText, p.value === propType && styles.modalItemTextActive]}>{p.label}</Text>
                  {p.value === propType && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* League Picker Modal — MANUAL mode. Same full-cache fuzzy search. */}
      <LeaguePickerModal
        visible={showLeaguePicker}
        onClose={() => setShowLeaguePicker(false)}
        selectedId={leagueId}
        onSelect={(l) => {
          setLeagueId(l.id);
          setLeagueQuery(l.name);
        }}
        title="League"
      />

    </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  bgWatermark: {
    position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
    alignItems: 'center', justifyContent: 'center',
  },
  bgLogo: {
    width: SCREEN_W * 0.72,
    height: SCREEN_W * 0.72,
    opacity: 0.04,
  },
  bgRing1: {
    position: 'absolute',
    width: SCREEN_W * 0.78,
    height: SCREEN_W * 0.78,
    borderRadius: SCREEN_W * 0.39,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.07)',
  },
  bgRing2: {
    position: 'absolute',
    width: SCREEN_W * 1.0,
    height: SCREEN_W * 1.0,
    borderRadius: SCREEN_W * 0.5,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.04)',
  },
  bgRing3: {
    position: 'absolute',
    width: SCREEN_W * 1.22,
    height: SCREEN_W * 1.22,
    borderRadius: SCREEN_W * 0.61,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.025)',
  },
  header: { paddingHorizontal: 20, paddingBottom: 14, alignItems: 'center' },
  logoRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  logoImg: { width: 54, height: 54, marginBottom: 8 },
  logoText: { fontSize: 20, fontWeight: '800', color: Colors.text, letterSpacing: -0.3 },
  tagline: { fontSize: 11, color: Colors.primary, marginTop: 2, letterSpacing: 0.5, fontWeight: '600' },
  modeRow: {
    flexDirection: 'row',
    marginHorizontal: 20,
    backgroundColor: 'rgba(26,26,26,0.6)',
    borderRadius: 14,
    padding: 3,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.12)',
  },
  modeTab: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    paddingVertical: 9, gap: 6, borderRadius: 11,
  },
  modeTabActive: {
    backgroundColor: 'rgba(57,255,20,0.15)',
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.35)',
    shadowColor: Colors.primary,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.25,
    shadowRadius: 8,
  },
  modeTabText: { fontSize: 13, color: Colors.textSecondary, fontWeight: '600', letterSpacing: 0.3 },
  modeTabTextActive: { color: Colors.primary, fontWeight: '700' },
  // ── Scan screen branded header ────────────────────────────────────────────
  scanHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 18,
    paddingVertical: 12,
    backgroundColor: 'rgba(8,8,8,0.85)',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(57,255,20,0.08)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.4,
    shadowRadius: 8,
  },
  scanHeaderBrand: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  scanHeaderLogo: {
    width: 36,
    height: 36,
  },

  // ── Paywall overlay ──
  paywallOverlay: {
    position: 'absolute',
    top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: 'rgba(5,5,5,0.92)',
    zIndex: 50,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 32,
    gap: 12,
  },
  paywallOverlayTitle: {
    fontSize: 20,
    fontWeight: '800',
    color: Colors.text,
    letterSpacing: 0.5,
    textAlign: 'center',
  },
  paywallOverlayBody: {
    fontSize: 13,
    color: Colors.textSecondary,
    textAlign: 'center',
    lineHeight: 20,
    maxWidth: 280,
  },
  paywallOverlayBtn: {
    backgroundColor: Colors.primary,
    paddingVertical: 14,
    paddingHorizontal: 32,
    borderRadius: 12,
    marginTop: 8,
  },
  paywallOverlayBtnText: {
    color: '#000',
    fontWeight: '800',
    fontSize: 15,
    letterSpacing: 0.5,
  },
  scanHeaderTitle: {
    fontSize: 15,
    fontWeight: '900',
    color: Colors.text,
    letterSpacing: 1.5,
  },
  scanHeaderSub: {
    fontSize: 10,
    color: Colors.primary,
    fontWeight: '600',
    letterSpacing: 0.5,
    marginTop: 1,
  },

  propPickerSheet: {
    backgroundColor: '#111111', borderRadius: 20, padding: 16,
    width: 300, maxHeight: 420, borderWidth: 1, borderColor: '#222',
  },
  propPickerTitle: {
    color: Colors.text, fontSize: 15, fontWeight: '700',
    marginBottom: 12, paddingHorizontal: 4,
  },
  propPickerItem: {
    paddingHorizontal: 12, paddingVertical: 13, borderRadius: 10,
  },
  propPickerItemActive: { backgroundColor: 'rgba(57,255,20,0.10)' },
  propPickerText: { color: Colors.textSecondary, fontSize: 14 },
  sportPickerHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 16,
  },
  sportPickerClose: { fontSize: 12, color: '#888', backgroundColor: '#1a1a1a', paddingHorizontal: 12, paddingVertical: 6, borderRadius: 12 },
  sportGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  sportGridItem: {
    width: '47%',
    aspectRatio: 1,
    backgroundColor: '#1a1a1a',
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    borderWidth: 1,
    borderColor: '#222',
  },
  sportGridItemActive: {
    backgroundColor: 'rgba(57,255,20,0.08)',
    borderColor: 'rgba(57,255,20,0.35)',
  },
  sportGridLabel: { fontSize: 13, fontWeight: '600', color: '#bbb' },
  sportGridLabelActive: { color: Colors.primary, fontWeight: '700' },
  sportSectionHeader: {
    paddingHorizontal: 4, paddingTop: 14, paddingBottom: 6,
    borderBottomWidth: 1, borderBottomColor: 'rgba(57,255,20,0.1)', marginBottom: 4,
  },
  sportSectionLabel: {
    fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.2, textTransform: 'uppercase',
  },
  sportListItem: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingVertical: 11, paddingHorizontal: 6, borderRadius: 10, marginBottom: 2,
  },
  sportListItemActive: {
    backgroundColor: 'rgba(57,255,20,0.08)',
  },
  sportListLabel: { fontSize: 14, fontWeight: '600', color: '#ccc', flex: 1 },
  sportListLabelActive: { color: Colors.primary, fontWeight: '700' },
  body: { paddingHorizontal: 20, paddingBottom: 40, flexGrow: 1 },
  /* Upload box */
  uploadBox: {
    backgroundColor: 'rgba(17,17,17,0.95)', borderRadius: Colors.radiusLg,
    padding: 32, alignItems: 'center', borderWidth: 1.5,
    borderColor: 'rgba(57,255,20,0.25)', borderStyle: 'dashed', gap: 10,
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.1, shadowRadius: 20,
  },
  uploadTitle: { fontSize: 18, fontWeight: '700', color: Colors.text },
  uploadSub: { fontSize: 13, color: Colors.textSecondary, textAlign: 'center' },
  galleryBtnBig: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: Colors.primary, paddingVertical: 13, paddingHorizontal: 28,
    borderRadius: Colors.radius, marginTop: 8,
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4, shadowRadius: 10, elevation: 6,
  },
  galleryBtnBigText: { color: '#000', fontWeight: '800', fontSize: 15 },

  /* ─── HERO UPLOAD BOX (Scan idle state — clean, no image) ─── */
  heroImageWrap: {
    width: '100%',
    aspectRatio: 1.5,
    borderRadius: Colors.radiusLg,
    overflow: 'hidden',
    marginBottom: 10,
  },
  heroImage: {
    width: '100%',
    height: '100%',
  },

  /* Autocomplete dropdown (used by NBA/NFL/NHL/WNBA player search) */
  autocompleteList: {
    backgroundColor: '#1a1a1a',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#333',
    marginTop: 2,
    marginBottom: 6,
    overflow: 'hidden',
  },
  autocompleteItem: {
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#222',
  },
  autocompleteText: {
    color: Colors.text,
    fontSize: 14,
    fontWeight: '600',
  },
  autocompleteSubText: {
    color: Colors.textSecondary,
    fontSize: 12,
    marginTop: 2,
  },
  resolvedBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: '#0d1f0d',
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 7,
    marginTop: 4,
    marginBottom: 6,
    borderWidth: 1,
    borderColor: Colors.primary + '30',
  },
  resolvedBadgeText: {
    color: Colors.primary,
    fontSize: 12,
    flex: 1,
  },

  /* Capture container (wraps analysis content for html2canvas) */
  captureContainer: { backgroundColor: '#000' },

  /* Inline error */
  inlineError: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    backgroundColor: Colors.errorDim, borderRadius: Colors.radius,
    padding: 12, marginTop: 10, borderWidth: 1, borderColor: Colors.error + '40',
  },
  inlineErrorText: { color: Colors.error, fontSize: 13, flex: 1, lineHeight: 18 },
  scanFillHint: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: '#0d1f0d', borderRadius: 8,
    padding: 10, marginBottom: 12, borderWidth: 1, borderColor: Colors.primary + '40',
  },
  scanFillHintText: { color: Colors.primary, fontSize: 12, flex: 1, fontFamily: 'Inter_400Regular' },

  scannedPreview: { width: '100%', height: 280, borderRadius: Colors.radius, marginBottom: 12 },
  scanSummaryCard: {
    backgroundColor: '#111111',
    borderRadius: 14,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: '#222',
  },
  detectedBadge: {
    backgroundColor: Colors.primary,
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  detectedBadgeText: {
    fontSize: 9,
    fontWeight: '800',
    color: '#000',
    letterSpacing: 0.5,
  },
  scanSummaryName: {
    fontSize: 18,
    fontWeight: '800',
    color: Colors.text,
  },
  scanSummaryMeta: {
    fontSize: 13,
    color: Colors.textSecondary,
    fontWeight: '600',
  },
  scanSummaryDot: {
    fontSize: 13,
    color: '#444',
    fontWeight: '700',
  },
  /* Detected card */
  sectionLabel: {
    fontSize: 11, fontWeight: '700', color: Colors.primary,
    letterSpacing: 1.5, marginBottom: 10,
  },
  detectedCard: {
    backgroundColor: 'rgba(17,17,17,0.95)', borderRadius: Colors.radiusLg,
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.1)', marginBottom: 14,
    zIndex: 50,
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.06, shadowRadius: 14,
  },
  detectedTop: { flexDirection: 'row', alignItems: 'flex-start', padding: 16, gap: 12 },
  playerAvatarWrap: {
    width: 44, height: 44, borderRadius: 22, backgroundColor: Colors.cardSecondary,
    alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  detectedInfo: { flex: 1, gap: 6 },
  detectedName: { fontSize: 18, fontWeight: '800', color: Colors.text },
  badgeRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', alignItems: 'center' },
  teamBadge: { backgroundColor: Colors.cardSecondary, borderRadius: 6, paddingHorizontal: 10, paddingVertical: 4 },
  teamBadgeTouchable: {
    backgroundColor: Colors.cardSecondary, borderRadius: 6, paddingHorizontal: 10, paddingVertical: 4,
    flexDirection: 'row', alignItems: 'center',
    borderWidth: 1, borderColor: '#333',
  },
  teamBadgeText: { fontSize: 12, color: Colors.textSecondary, fontWeight: '600' },
  teamEditRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  oppEditRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 2 },
  teamEditInput: {
    backgroundColor: Colors.cardSecondary, borderRadius: 6, paddingHorizontal: 10, paddingVertical: 4,
    fontSize: 16, color: Colors.text, fontWeight: '600', minWidth: 100, maxWidth: 160,
    borderWidth: 1, borderColor: Colors.primary,
  },
  teamEditConfirm: { padding: 5, backgroundColor: Colors.primaryDim, borderRadius: 6 },
  teamEditCancel: { padding: 5 },
  matchedBadge: {
    backgroundColor: Colors.primaryDim, borderRadius: 6, paddingHorizontal: 10, paddingVertical: 4,
    borderWidth: 1, borderColor: Colors.border,
  },
  matchedText: { fontSize: 11, color: Colors.primary, fontWeight: '700', letterSpacing: 0.5 },
  vsText: { fontSize: 12, color: Colors.textTertiary },

  /* Venue toggle */
  venueRow: {
    flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16,
    paddingBottom: 14, gap: 12,
  },
  venueLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1 },
  venueToggle: {
    flexDirection: 'row', backgroundColor: 'rgba(26,26,26,0.6)',
    borderRadius: 12, padding: 3, gap: 2, flex: 1,
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.12)',
  },
  venueOption: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    paddingVertical: 8, gap: 5, borderRadius: 10,
  },
  venueOptionActive: {
    backgroundColor: 'rgba(57,255,20,0.15)',
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.35)',
  },
  venueOptionText: { fontSize: 12, color: Colors.textSecondary, fontWeight: '700', letterSpacing: 0.5 },
  venueOptionTextActive: { color: Colors.primary, fontWeight: '800' },

  detectedStats: { flexDirection: 'row', borderTopWidth: 1, borderTopColor: Colors.borderSubtle },
  detectedStat: { flex: 1, padding: 14, alignItems: 'center', gap: 4 },
  detectedStatLabel: { fontSize: 9, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1 },
  detectedStatVal: { fontSize: 14, fontWeight: '700', color: Colors.text, textAlign: 'center' },
  detectedStatDivider: { width: 1, backgroundColor: Colors.borderSubtle, marginVertical: 10 },

  /* Prediction button */
  predictBtn: {
    backgroundColor: Colors.primary, borderRadius: 14, height: 54,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.55, shadowRadius: 20, elevation: 10,
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.4)',
  },
  predictBtnLoading: { opacity: 0.8 },
  predictBtnCancel: {
    backgroundColor: Colors.error,
    borderWidth: 0,
  },
  predictBtnText: { color: '#000', fontWeight: '800', fontSize: 16, letterSpacing: 0.5 },
  rescanBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    justifyContent: 'center', paddingVertical: 14,
  },
  rescanText: { color: Colors.textSecondary, fontSize: 13 },
  cancelBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 10, paddingVertical: 6,
    borderRadius: 6,
    backgroundColor: 'rgba(220,38,38,0.12)',
    marginTop: 2,
  },
  cancelBtnText: { color: Colors.error, fontSize: 12, fontWeight: '700' },

  /* Manual form */
  manualForm: { gap: 10, zIndex: 1 },
  playerConfirmCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginTop: 2,
    padding: 10,
    backgroundColor: 'rgba(57,255,20,0.06)',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.3)',
  },
  playerConfirmEyebrow: {
    color: Colors.primary,
    fontSize: 9,
    fontWeight: '800',
    letterSpacing: 1.1,
  },
  playerConfirmName: {
    color: Colors.text,
    fontSize: 14,
    fontWeight: '800',
    marginTop: 3,
  },
  playerConfirmTeam: {
    color: Colors.textSecondary,
    fontSize: 11,
    marginTop: 2,
  },
  playerConfirmBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderRadius: 6,
    backgroundColor: Colors.primary,
  },
  playerConfirmBtnText: {
    color: '#000',
    fontSize: 11,
    fontWeight: '800',
  },
  formEyebrow: {
    fontSize: 11, color: Colors.textTertiary, fontWeight: '700',
    letterSpacing: 2.5, textAlign: 'center', marginBottom: 14,
    textTransform: 'uppercase',
  },
  autoFillBanner: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    marginBottom: 4, padding: 10,
    backgroundColor: 'rgba(57,255,20,0.06)', borderRadius: 8,
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.25)',
  },
  changePlayerBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    alignSelf: 'flex-start', paddingVertical: 5, paddingHorizontal: 10,
    marginTop: 6, borderRadius: 20,
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.3)',
    backgroundColor: 'rgba(57,255,20,0.06)',
  },
  changePlayerBtnText: {
    color: Colors.primary, fontSize: 11, fontFamily: 'Inter_500Medium', letterSpacing: 0.3,
  },
  sportTeamContext: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 3,
    marginBottom: 5,
    marginLeft: 2,
  },
  sportTeamContextText: {
    color: Colors.primary,
    fontSize: 11,
    fontWeight: '600',
  },
  fieldLabel: {
    fontSize: 10, color: Colors.primary, fontWeight: '800',
    letterSpacing: 1.2, marginBottom: 4, marginTop: 10, textTransform: 'uppercase',
  },
  fieldLabelOpt: {
    fontSize: 10, color: Colors.textTertiary, fontWeight: '400', textTransform: 'none', letterSpacing: 0,
  },
  textInput: {
    backgroundColor: 'rgba(17,17,17,0.8)', borderRadius: 12, borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.12)', color: Colors.text, fontSize: 16,
    paddingHorizontal: 14, height: 48,
    shadowColor: '#000', shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3, shadowRadius: 6,
  },
  pickerBtn: {
    backgroundColor: 'rgba(17,17,17,0.8)', borderRadius: 12, borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.12)', paddingHorizontal: 14, height: 48,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    shadowColor: '#000', shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3, shadowRadius: 6,
  },
  pickerBtnText: { color: Colors.text, fontSize: 15 },
  pickerModal: {
    backgroundColor: '#111', borderTopLeftRadius: 16, borderTopRightRadius: 16,
    paddingVertical: 8, paddingHorizontal: 4, position: 'absolute', bottom: 0, left: 0, right: 0,
  },
  pickerOption: {
    paddingHorizontal: 20, paddingVertical: 14, borderRadius: 8,
  },
  pickerOptionActive: {
    backgroundColor: 'rgba(57,255,20,0.1)',
  },
  pickerOptionText: { color: Colors.textSecondary, fontSize: 15 },
  pickerOptionTextActive: { color: Colors.primary, fontWeight: '700' },

  /* Analysis card — glass panel treatment */
  analysisCard: {
    backgroundColor: '#0A0B0D', borderRadius: 28,
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.14)', overflow: 'hidden', marginBottom: 16,
    shadowColor: '#000', shadowOffset: { width: 0, height: 14 },
    shadowOpacity: 0.5, shadowRadius: 26, elevation: 8,
    position: 'relative',
  },
  glassSheenTop: {
    position: 'absolute', top: 0, left: 0, right: 0, height: 130, zIndex: 0,
  },
  glassHairline: {
    position: 'absolute', top: 0, left: 24, right: 24, height: 1.5,
    backgroundColor: 'rgba(57,255,20,0.5)', opacity: 0.6, zIndex: 1,
  },
  analysisHeader: {
    flexDirection: 'row', justifyContent: 'space-between',
    alignItems: 'flex-start', padding: 18,
  },
  ownerMediaStrip: {
    width: 54, marginRight: 10, alignItems: 'center', gap: 5,
  },
  analysisPlayerPhoto: {
    width: 48, height: 48, borderRadius: 24, backgroundColor: '#202020',
  },
  analysisTeamLogo: {
    width: 22, height: 22, resizeMode: 'contain' as const,
  },
  analysisPlayerInfo: { flex: 1, marginRight: 12 },
  analysisPlayer: { fontSize: 20, fontWeight: '800', color: Colors.text },
  analysisTeam: { fontSize: 12, color: Colors.textSecondary, marginTop: 3 },
  analysisVenue: { fontSize: 11, color: Colors.textTertiary, marginTop: 3, letterSpacing: 0.5 },
  recBadge: { paddingHorizontal: 14, paddingVertical: 7, borderRadius: 10 },
  recText: { fontSize: 14, fontWeight: '800', letterSpacing: 0.5 },
  analysisDivider: { height: 1, backgroundColor: Colors.borderSubtle },
  analysisStats: { flexDirection: 'row', paddingVertical: 4 },
  analysisStat: { flex: 1, alignItems: 'center', padding: 16, gap: 4 },
  analysisStatLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '600' },
  analysisStatVal: { fontSize: 22, fontWeight: '800', color: Colors.text },
  analysisStatSub: { fontSize: 9, color: Colors.textTertiary, letterSpacing: 0.8 },
  analysisStatDivider: { width: 1, backgroundColor: Colors.borderSubtle, marginVertical: 14 },
  probabilityExplainer: {
    paddingHorizontal: 16,
    paddingBottom: 7,
    color: Colors.textTertiary,
    fontSize: 8,
    lineHeight: 12,
  },

  /* Confidence interval */
  ciRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 10,
  },
  ciLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.8 },
  ciVal: { fontSize: 13, color: Colors.textSecondary, fontWeight: '600' },

  /* Model Factors card */
  /* ─── ANALYSIS CARD ACCENT STRIPE ─── */
  analysisAccentStripe: {
    height: 3, borderTopLeftRadius: Colors.radiusLg, borderTopRightRadius: Colors.radiusLg,
    marginBottom: 0,
  },

  /* ─── CONFIDENCE GAUGE ─── */
  confGaugeWrap: {
    paddingHorizontal: 16, paddingBottom: 8, paddingTop: 4, gap: 4,
  },
  confGaugeTrack: {
    height: 8, backgroundColor: 'rgba(255,255,255,0.05)', borderRadius: 4,
    overflow: 'hidden', position: 'relative',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.06)',
  },
  confGaugeFill: {
    height: '100%', borderRadius: 4,
    shadowRadius: 8, shadowOpacity: 0.6, shadowOffset: { width: 0, height: 0 },
  },
  confGaugeMidMark: {
    position: 'absolute', left: '0%' as any, top: 0, bottom: 0,
    width: 1, backgroundColor: 'rgba(255,255,255,0.15)',
  },
  confGaugeLabels: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
  },
  confGaugeLabelEdge: {
    fontSize: 8, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.3,
  },
  confGaugeLabelCenter: {
    fontSize: 9, fontWeight: '800', letterSpacing: 0.5,
  },

  /* ─── ALGORITHM BREAKDOWN (MODEL FACTORS) ─── */
  mfCard: {
    paddingHorizontal: 16, paddingVertical: 14, gap: 12,
  },
  mfHeader: {
    flexDirection: 'row', alignItems: 'center', gap: 7,
  },
  mfTitle: {
    fontSize: 11, color: Colors.primary, fontWeight: '800', letterSpacing: 1.2, flex: 1,
  },
  mfSamplesBadge: {
    backgroundColor: 'rgba(57,255,20,0.10)', borderRadius: 10,
    paddingHorizontal: 8, paddingVertical: 3,
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.25)',
  },
  mfSamplesText: {
    fontSize: 9, color: Colors.primary, fontWeight: '700', letterSpacing: 0.5,
  },

  /* Probability meter */
  mfProbSection: { gap: 6 },
  mfProbTrack: {
    flexDirection: 'row', height: 10, borderRadius: 5, overflow: 'hidden', gap: 0,
  },
  mfProbOverFill: {
    backgroundColor: 'rgba(57,255,20,0.55)',
  },
  mfProbDivider: { width: 2, backgroundColor: Colors.background },
  mfProbUnderFill: {
    backgroundColor: 'rgba(255,107,53,0.50)',
  },
  mfProbBar: { flexDirection: 'row', height: 6, borderRadius: 3, overflow: 'hidden', backgroundColor: 'rgba(255,255,255,0.1)' },
  mfProbFillOver: { backgroundColor: 'rgba(57,255,20,0.55)' },
  mfProbFillUnder: { backgroundColor: 'rgba(255,107,53,0.50)' },
  mfProbLabel: { fontSize: 10, fontWeight: '600' },
  mfProbLabels: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
  },
  mfProbLabelLeft: { alignItems: 'flex-start', gap: 1 },
  mfProbLabelRight: { alignItems: 'flex-end', gap: 1 },
  mfProbPct: { fontSize: 18, fontWeight: '900', lineHeight: 22 },
  mfProbDir: { fontSize: 8, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1 },
  mfProbVs: { fontSize: 9, color: Colors.textTertiary, fontWeight: '500' },

  /* Metrics grid */
  mfMetricsGrid: {
    flexDirection: 'row', flexWrap: 'wrap', gap: 6,
  },
  mfMetric: {
    flex: 1, minWidth: 70, alignItems: 'center', gap: 2,
    backgroundColor: Colors.cardSecondary, borderRadius: 8,
    paddingVertical: 8, paddingHorizontal: 6,
    borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  mfMetricHighlight: {
    borderWidth: 1,
  },
  mfMetricLabel: {
    fontSize: 7, color: Colors.textTertiary, fontWeight: '700',
    letterSpacing: 0.8, textAlign: 'center',
  },
  mfMetricVal: {
    fontSize: 18, fontWeight: '900', color: Colors.text, lineHeight: 22,
  },
  mfMetricSub: {
    fontSize: 8, color: Colors.textSecondary, fontWeight: '600', textAlign: 'center',
  },
  compactMetricChip: {
    minWidth: 68,
    flexGrow: 1,
    backgroundColor: 'rgba(255,255,255,0.035)',
    borderRadius: 7,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    paddingHorizontal: 8,
    paddingVertical: 6,
    alignItems: 'center',
  },
  compactMetricLabel: {
    fontSize: 7,
    color: Colors.textTertiary,
    fontWeight: '800',
    letterSpacing: 0.7,
  },
  compactMetricValue: {
    marginTop: 2,
    fontSize: 13,
    color: Colors.text,
    fontWeight: '800',
    fontFamily: 'JetBrainsMono_700Bold',
  },
  compactInputText: {
    fontSize: 8,
    color: Colors.textTertiary,
    fontWeight: '700',
    letterSpacing: 0.5,
    backgroundColor: 'rgba(255,255,255,0.035)',
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 3,
  },

  /* Signal chain */
  mfChainSection: { gap: 6 },
  mfChainTitle: {
    fontSize: 8, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1.2,
  },
  mfChainRow: {
    flexDirection: 'row', alignItems: 'center', gap: 4, paddingBottom: 2,
  },
  mfChainNode: {
    alignItems: 'center', gap: 2,
    backgroundColor: Colors.cardSecondary, borderRadius: 7,
    paddingVertical: 6, paddingHorizontal: 8,
    borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  mfChainNodeNum: {
    fontSize: 14, fontWeight: '900', color: Colors.text, lineHeight: 17,
  },
  mfChainNodeSub: {
    fontSize: 7, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.7,
  },
  mfChainArrow: {
    fontSize: 16, color: '#2a2a2a', fontWeight: '700', marginHorizontal: 0,
  },
  mfChainStep: {
    alignItems: 'center', gap: 1,
    borderRadius: 6, paddingVertical: 5, paddingHorizontal: 7, borderWidth: 1,
  },
  mfChainStepLabel: {
    fontSize: 7, fontWeight: '800', letterSpacing: 0.6,
  },
  mfChainStepPct: {
    fontSize: 10, fontWeight: '900',
  },
  mfChainStepN: {
    fontSize: 7, color: Colors.textTertiary, fontWeight: '600',
  },

  /* Stakes banner */
  mfStakeBanner: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderRadius: 8, borderWidth: 1, padding: 10,
  },
  mfStakeBannerLabel: {
    fontSize: 10, fontWeight: '800', letterSpacing: 0.5,
  },
  mfStakeBannerReason: {
    fontSize: 9, color: Colors.textTertiary, fontWeight: '500', fontStyle: 'italic', marginTop: 1,
  },
  mfStakeBannerMult: {
    fontSize: 13, fontWeight: '900', letterSpacing: 0.5,
  },

  /* Opp profile row */
  mfOppRow: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    backgroundColor: 'rgba(160,132,232,0.08)', borderRadius: 6,
    paddingHorizontal: 8, paddingVertical: 5,
    borderWidth: 1, borderColor: 'rgba(160,132,232,0.20)',
  },
  mfOppLabel: {
    fontSize: 10, color: Colors.textSecondary, fontWeight: '600', flex: 1,
  },

  /* Opponent Defense Profile card */
  mfOppCard: {
    borderRadius: 8, borderWidth: 1,
    paddingHorizontal: 10, paddingVertical: 7,
    gap: 5,
  },
  mfOppCardHeader: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
  },
  mfOppCardTitle: {
    fontSize: 9, fontWeight: '800', letterSpacing: 0.8, flex: 1,
  },
  mfOppTierPill: {
    borderRadius: 4, borderWidth: 1,
    paddingHorizontal: 5, paddingVertical: 1,
  },
  mfOppTierText: {
    fontSize: 8, fontWeight: '800', letterSpacing: 0.5,
  },
  mfOppCardBody: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
  },
  mfOppStat: {
    flex: 1, alignItems: 'center',
  },
  mfOppStatVal: {
    fontSize: 13, fontWeight: '700', color: Colors.text,
  },
  mfOppStatSub: {
    fontSize: 8, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.3, marginTop: 1,
  },
  mfOppDivLine: {
    width: 1, height: 24, backgroundColor: 'rgba(255,255,255,0.08)',
  },
  mfOppCardDesc: {
    fontSize: 9, color: Colors.textSecondary, lineHeight: 13,
  },

  /* Legacy mf styles kept for any remaining references */
  mfRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  mfLabel: { fontSize: 9, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.7, flexShrink: 0 },
  mfVal: { fontSize: 11, color: Colors.textSecondary, fontWeight: '600', textAlign: 'right', flexShrink: 1 },
  mfPossRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  mfH2HPill: {
    backgroundColor: 'rgba(57,255,20,0.10)', borderRadius: 4,
    paddingHorizontal: 6, paddingVertical: 2, borderWidth: 1, borderColor: 'rgba(57,255,20,0.30)',
  },
  mfH2HPillText: { fontSize: 9, color: Colors.primary, fontWeight: '700', letterSpacing: 0.5 },
  mfStakesReason: { fontSize: 9, color: Colors.textTertiary, fontWeight: '500', textAlign: 'right', fontStyle: 'italic' },

  /* Moneyline & Game Type */
  matchOddsRow: { paddingHorizontal: 16, paddingVertical: 12, gap: 10 },
  moneylineWrap: { gap: 6 },
  secondLegBanner: {
    marginHorizontal: 16, marginBottom: 12, padding: 12,
    backgroundColor: '#0f172a', borderRadius: 10,
    borderWidth: 1, borderColor: '#334155',
  },
  secondLegHeader: { flexDirection: 'row', gap: 6, marginBottom: 8 },
  secondLegBadge: {
    backgroundColor: '#7c3aed', borderRadius: 4, paddingHorizontal: 7, paddingVertical: 3,
  },
  secondLegBadgeText: { fontSize: 10, fontWeight: '800', color: '#fff', letterSpacing: 1 },
  secondLegFirstLeg: { fontSize: 11, color: Colors.textSecondary, marginBottom: 6 },
  secondLegAggRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10, marginBottom: 6 },
  secondLegAggTeam: { fontSize: 12, fontWeight: '700', color: Colors.text, flex: 1, textAlign: 'center' },
  secondLegAggScore: { fontSize: 22, fontWeight: '900', letterSpacing: 2 },
  secondLegStatus: { fontSize: 11, fontWeight: '600', textAlign: 'center' },
  moneylineHeader: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  moneylineLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1.2 },
  mlDisclaimer: { fontSize: 9, color: Colors.textTertiary, marginTop: 4, fontStyle: 'italic' },
  ftsWrap: { marginTop: 10, gap: 6 },
  ftsHeader: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  ftsLabel: { fontSize: 10, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 1 },
  ftsBars: { gap: 5 },
  ftsBarRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  ftsTeamLabel: { width: 52, fontSize: 9, fontWeight: '700', letterSpacing: 0.5 },
  ftsBarBg: { flex: 1, height: 6, backgroundColor: '#1a1a1a', borderRadius: 3, overflow: 'hidden' },
  ftsBarFill: { height: '100%', borderRadius: 3 },
  ftsPct: { width: 30, fontSize: 10, fontWeight: '800', textAlign: 'right', fontVariant: ['tabular-nums'] as any },
  ftsNote: { fontSize: 8, color: Colors.textTertiary, fontStyle: 'italic' },
  moneylinePills: { flexDirection: 'row', gap: 6 },
  mlPill: {
    flex: 1, backgroundColor: 'rgba(255,255,255,0.035)', borderRadius: 12, paddingVertical: 10,
    alignItems: 'center', borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  mlPillTeam: { fontSize: 9, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.8, marginBottom: 2 },
  mlPillOdds: { fontSize: 15, color: Colors.text, fontWeight: '800', fontVariant: ['tabular-nums'] as any },
  gameTypeWrap: { marginTop: 2, gap: 2 },
  gameTypeLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1.2 },
  gameTypeValue: { fontSize: 14, color: Colors.primary, fontWeight: '800', letterSpacing: 0.5 },
  gameTypeSub: { fontSize: 11, color: Colors.textSecondary },

  /* Expected Possession */
  possRow: { paddingHorizontal: 16, paddingVertical: 12, gap: 6 },
  possHeader: { flexDirection: 'row', alignItems: 'center', gap: 5, marginBottom: 2 },
  possLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1.2 },
  possBarWrap: {
    flexDirection: 'row', height: 10, borderRadius: 5, overflow: 'hidden',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.06)',
  },
  possBarHome: {
    backgroundColor: Colors.primary, borderTopLeftRadius: 5, borderBottomLeftRadius: 5,
    shadowColor: Colors.primary, shadowOpacity: 0.6, shadowRadius: 6, shadowOffset: { width: 0, height: 0 },
  },
  possBarAway: {
    backgroundColor: '#f43f5e', borderTopRightRadius: 5, borderBottomRightRadius: 5,
    shadowColor: '#f43f5e', shadowOpacity: 0.5, shadowRadius: 6, shadowOffset: { width: 0, height: 0 },
  },
  possNumbers: { flexDirection: 'row', justifyContent: 'space-between' },
  possHomeText: { fontSize: 13, fontWeight: '800', color: Colors.primary, fontVariant: ['tabular-nums'] as any },
  possAwayText: { fontSize: 13, fontWeight: '800', color: '#f43f5e', fontVariant: ['tabular-nums'] as any },
  possSub: { fontSize: 10, color: Colors.textTertiary, marginTop: 2 },

  /* Reasoning */
  reasoningBox: { padding: 16, gap: 8 },
  aiAnalysisBox: { padding: 16, gap: 12 },
  reasoningHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  reasoningLabel: { fontSize: 10, color: Colors.primary, fontWeight: '700', letterSpacing: 1.5 },
  reasoningText: { fontSize: 13, color: Colors.textSecondary, lineHeight: 20 },

  /* Scout Report card — glass panel treatment */
  scoutCard: {
    backgroundColor: 'rgba(255,255,255,0.025)',
    borderRadius: 18,
    borderWidth: 1,
    borderColor: '#1E1E1E',
    padding: 14,
    gap: 8,
    marginBottom: 8,
    shadowColor: '#000', shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.3, shadowRadius: 12,
  },
  scoutHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  scoutTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.8, flex: 1 },
  scoutBlend: { fontSize: 9, color: Colors.textTertiary, letterSpacing: 0.5 },
  scoutSection: { gap: 4 },
  scoutSectionTitle: { fontSize: 10, fontWeight: '800', letterSpacing: 1.5 },
  scoutSectionBody: { fontSize: 11, color: Colors.textSecondary, lineHeight: 16 },
  scenarioProbRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 6,
  },
  scenarioProbPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 9,
    paddingVertical: 3,
    borderRadius: 20,
    borderWidth: 1,
    backgroundColor: '#0a0a0a',
  },
  scenarioProbLabel: {
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  scenarioProbPct: {
    fontSize: 12,
    fontWeight: '800',
  },

  /* AI section blocks */
  aiBlocks: { gap: 14, marginTop: 4 },
  aiVerdictBlock: {
    backgroundColor: 'rgba(57,255,20,0.06)',
    borderLeftWidth: 3,
    borderLeftColor: Colors.primary,
    borderRadius: 8,
    padding: 12,
    gap: 6,
  },
  aiVerdictPill: {
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(57,255,20,0.12)',
    borderRadius: 4,
    paddingHorizontal: 7,
    paddingVertical: 2,
  },
  aiVerdictLabel: { fontSize: 9, fontWeight: '800', letterSpacing: 1.5 },
  aiVerdictText: { fontSize: 14, fontWeight: '600', lineHeight: 21, color: Colors.text },
  aiTldrBlock: {
    backgroundColor: Colors.cardSecondary,
    borderRadius: 8,
    padding: 12,
  },
  aiTldrText: { fontSize: 12, color: Colors.textSecondary, lineHeight: 18, fontStyle: 'italic' },
  aiSection: { gap: 5 },
  aiSectionHeader: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  aiSectionTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.2 },
  aiSectionBody: { fontSize: 13, color: Colors.textSecondary, lineHeight: 20 },
  aiBodyText: { fontSize: 13, color: Colors.textSecondary, lineHeight: 20 },

  /* ─── REVERSE FORMULA CARD ─── */
  rfCard: {
    backgroundColor: Colors.card, borderRadius: 10,
    padding: 11, borderWidth: 1, borderColor: Colors.borderSubtle, marginTop: 8, gap: 6,
  },
  rfHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  rfTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  rfTitle: { fontSize: 11, fontWeight: '800', color: Colors.primary, letterSpacing: 1.5 },
  rfGamesAnalyzed: { fontSize: 9, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.5 },
  rfRow: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  rfRowLabel: { fontSize: 9, fontWeight: '700', color: Colors.textSecondary, letterSpacing: 0.5, width: 66 },
  rfBarTrack: {
    flex: 1, height: 3, backgroundColor: Colors.cardSecondary,
    borderRadius: 2, overflow: 'hidden',
  },
  rfBarFill: { height: '100%', borderRadius: 2 },
  rfPct: { fontSize: 10, fontWeight: '700', width: 28, textAlign: 'right' },
  rfVal: { fontSize: 11, fontWeight: '800', width: 42, textAlign: 'right' },
  edgeSafetyWrapper: {
    paddingTop: 10, paddingBottom: 4, gap: 8,
  },
  edgeSafetyBanner: {
    flexDirection: 'row', gap: 8, paddingHorizontal: 16,
  },
  edgeSafetyPill: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingHorizontal: 10, paddingVertical: 6,
    borderRadius: 20, borderWidth: 1, flex: 1, justifyContent: 'center',
  },
  edgeSafetyPillLabel: { fontSize: 9, fontWeight: '600', letterSpacing: 0.8 },
  edgeSafetyPillValue: { fontSize: 11, fontWeight: '800', letterSpacing: 0.5 },
  edgeSafetyWhy: {
    marginHorizontal: 16,
    borderLeftWidth: 2,
    paddingLeft: 10,
    paddingVertical: 4,
  },
  edgeSafetyWhyText: {
    fontSize: 12, lineHeight: 17, fontWeight: '500',
  },

  rfBadgeRow: { flexDirection: 'row', gap: 5, flexWrap: 'wrap', marginTop: 1 },
  rfBadge: {
    paddingHorizontal: 7, paddingVertical: 3,
    borderRadius: 5, borderWidth: 1,
  },
  rfBadgeMomentum: { backgroundColor: 'rgba(255,140,66,0.12)', borderColor: '#FF8C42' },
  rfBadgeMomentumText: { fontSize: 10, fontWeight: '700', color: '#FF8C42' },
  rfBadgeText: { fontSize: 10, fontWeight: '800' },
  rfBadgeVol: { backgroundColor: Colors.cardSecondary, borderColor: Colors.border },
  rfBadgeVolText: { fontSize: 9, fontWeight: '600', color: Colors.textSecondary },
  rfProjectionRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    backgroundColor: Colors.cardSecondary, borderRadius: 8, padding: 9, marginTop: 2,
  },
  rfProjectionLabel: { fontSize: 11, color: Colors.textSecondary, fontWeight: '600' },
  rfProjectionRight: { flexDirection: 'row', alignItems: 'baseline', gap: 8 },
  rfProjectionVal: { fontSize: 16, fontWeight: '800', color: '#4DA6FF' },
  rfProjectionProb: { fontSize: 10, color: '#4DA6FF', fontWeight: '600' },

  /* ─── GAME LOG GRID ─── */
  gameLogsCard: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    padding: 11, borderWidth: 1, borderColor: Colors.borderSubtle, marginTop: 8, gap: 8,
  },
  syntheticNotice: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    backgroundColor: 'rgba(255,255,255,0.04)', borderRadius: 8,
    padding: 12, borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  syntheticNoticeText: {
    flex: 1, fontSize: 12, color: Colors.textSecondary, lineHeight: 17,
  },
  gameLogsHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  glHeaderLeft: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  gameLogsTitle: { fontSize: 10, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 1.2 },
  gameLogsHistoryMeta: {
    fontSize: 8,
    color: Colors.primary,
    fontWeight: '700',
    letterSpacing: 0.7,
    marginTop: 3,
  },
  hitRateBadge: {
    backgroundColor: 'rgba(57,255,20,0.12)', borderRadius: 20,
    paddingHorizontal: 10, paddingVertical: 3, borderWidth: 1, borderColor: 'rgba(57,255,20,0.3)',
  },
  hitRateBadgeText: { fontSize: 10, fontWeight: '700', color: Colors.success },
  glTabRow: {
    flexDirection: 'row', backgroundColor: Colors.cardSecondary,
    borderRadius: 8, padding: 2, gap: 2,
  },
  glTab: { flex: 1, alignItems: 'center', paddingVertical: 5, borderRadius: 6 },
  glTabActive: { backgroundColor: Colors.primaryDim },
  glTabText: { fontSize: 11, fontWeight: '700', color: Colors.textSecondary, letterSpacing: 0.5 },
  glTabTextActive: { color: Colors.primary },
  glGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 4 },
  glTile: {
    borderRadius: 7, padding: 5, alignItems: 'center',
    gap: 2, borderWidth: 1, position: 'relative',
  },
  glTileOver: {
    backgroundColor: 'rgba(57,255,20,0.07)', borderColor: 'rgba(57,255,20,0.3)',
  },
  glTileUnder: {
    backgroundColor: 'rgba(255,59,48,0.07)', borderColor: 'rgba(255,59,48,0.2)',
  },
  glDot: {
    position: 'absolute', top: 4, right: 4,
    width: 4, height: 4, borderRadius: 2, backgroundColor: '#FF8C42',
  },
  glTileVal: { fontSize: 13, fontWeight: '900', lineHeight: 16 },
  glTileMins: { fontSize: 7, color: Colors.textSecondary, fontWeight: '600' },
  glVenueBadge: {
    backgroundColor: '#1a1a1a', borderRadius: 4,
    paddingHorizontal: 5, paddingVertical: 2,
  },
  glVenueText: { fontSize: 8, fontWeight: '800', color: Colors.textSecondary, letterSpacing: 0.5 },
  glTileOpp: { fontSize: 8, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.3 },
  glTileScore: { fontSize: 8, color: Colors.textSecondary, fontWeight: '600' },
  glOppRow: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  glTileRank: { fontSize: 7, color: Colors.textTertiary, fontWeight: '700' },
  glTilePoss: {
    fontSize: 7, color: '#4A8FFF', fontWeight: '700', letterSpacing: 0.3, marginTop: 2,
  },
  glTileSecStat: {
    fontSize: 7, color: Colors.textSecondary, fontWeight: '700', letterSpacing: 0.3, marginTop: 1,
  },
  glTileDeselected: {
    backgroundColor: 'rgba(255,255,255,0.02)', borderColor: 'rgba(255,255,255,0.06)',
  },
  glTierDot: {
    position: 'absolute', top: 3, right: 3, width: 5, height: 5, borderRadius: 2.5,
  },
  glLowMinDot: {
    position: 'absolute', top: 3, left: 3, width: 5, height: 5, borderRadius: 2.5,
    backgroundColor: '#FF8C00',
  },
  glMinsBarWrap: {
    width: '100%', height: 2.5, backgroundColor: 'rgba(255,255,255,0.05)',
    borderRadius: 1.5, marginTop: 3, overflow: 'hidden',
  },
  glMinsBarFill: { height: '100%', borderRadius: 1.5 },
  glHitRateRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 5, borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.05)',
  },
  glHitRateLeft: { alignItems: 'center', minWidth: 34 },
  glHitRateCount: { fontSize: 14, fontWeight: '800' },
  glHitRateLabel: { fontSize: 7, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.8, marginTop: 1 },
  glHitRateBarWrap: { flex: 1, height: 4, backgroundColor: 'rgba(255,255,255,0.06)', borderRadius: 2, overflow: 'hidden' },
  glHitRateFill: { height: '100%', borderRadius: 2 },
  glHitRatePct: { fontSize: 12, fontWeight: '800', minWidth: 36, textAlign: 'right' },
  glSelectedMean: { fontSize: 9, color: Colors.textSecondary, fontWeight: '700' },
  glResetBtn: {
    backgroundColor: 'rgba(255,255,255,0.07)', borderRadius: 5,
    paddingHorizontal: 9, paddingVertical: 3, borderWidth: 1, borderColor: 'rgba(255,255,255,0.12)',
  },
  glResetBtnText: { fontSize: 9, fontWeight: '700', color: Colors.textSecondary, letterSpacing: 0.5 },
  glLineAdjuster: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 12,
    paddingVertical: 4, borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.05)', marginBottom: 2,
  },
  glLineBtn: {
    width: 26, height: 26, borderRadius: 13,
    backgroundColor: 'rgba(255,255,255,0.07)', alignItems: 'center', justifyContent: 'center',
  },
  glLineBtnText: { color: Colors.primary, fontSize: 18, fontWeight: '700', lineHeight: 20 },
  glLineValue: { color: Colors.text, fontSize: 14, fontWeight: '800', letterSpacing: 0.5 },
  glLineLabel: { color: Colors.textTertiary, fontSize: 8, letterSpacing: 0.8, marginTop: 1 },
  glLineResetBtn: {
    paddingHorizontal: 8, paddingVertical: 4, borderRadius: 4, backgroundColor: 'rgba(255,255,255,0.06)',
  },
  glLineResetText: { color: Colors.textSecondary, fontSize: 9, fontWeight: '600', letterSpacing: 0.5 },
  glTabQuality: {
    paddingHorizontal: 10, paddingVertical: 7, borderRadius: 7,
    backgroundColor: 'rgba(255,140,0,0.10)', borderWidth: 1, borderColor: 'rgba(255,140,0,0.28)',
    alignItems: 'center',
  },
  glTabQualityText: { fontSize: 10, fontWeight: '800', color: '#FF9500', letterSpacing: 0.5 },
  glLegendRow: {
    flexDirection: 'row', gap: 10,
    paddingTop: 10, borderTopWidth: 1, borderTopColor: 'rgba(255,255,255,0.05)',
  },
  glLegendGroup: { flex: 1, gap: 4 },
  glLegendDivider: { width: 1, backgroundColor: 'rgba(255,255,255,0.07)' },
  glLegendTitle: {
    fontSize: 7, fontWeight: '800', color: Colors.textTertiary,
    letterSpacing: 1, marginBottom: 2,
  },
  glLegendItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  glLegendDot: { width: 5, height: 5, borderRadius: 2.5 },
  glLegendBar: { width: 14, height: 2.5, borderRadius: 1.5 },
  glLegendLabel: { fontSize: 8, color: Colors.textTertiary, fontWeight: '500' },

  /* Game log header right (avg possession badge) */
  glHeaderRight: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  glOppPossBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: 'rgba(74,108,255,0.10)', borderRadius: 20,
    paddingHorizontal: 8, paddingVertical: 3,
    borderWidth: 1, borderColor: 'rgba(74,108,255,0.25)',
  },
  glOppPossLabel: { fontSize: 8, fontWeight: '700', color: '#4A6CFF', letterSpacing: 0.5 },
  glOppPossVal: { fontSize: 9, fontWeight: '800', color: '#4A6CFF' },

  avgRow: { flexDirection: 'row', gap: 12 },
  avgText: { fontSize: 10, color: Colors.textSecondary, fontWeight: '600', letterSpacing: 0.5 },
  defStatsRow: {
    flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 5,
    paddingTop: 5, borderTopWidth: 1, borderTopColor: 'rgba(255,255,255,0.06)',
  },
  defStatsLabel: { fontSize: 9, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.5, marginRight: 2 },
  defStatChip: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    backgroundColor: 'rgba(255,255,255,0.06)', borderRadius: 6,
    paddingHorizontal: 6, paddingVertical: 3,
  },
  defStatChipLabel: { fontSize: 8, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.4 },
  defStatChipVal: { fontSize: 10, color: Colors.text, fontWeight: '800' },

  /* ─── H2H text view ─── */
  h2hTextView: { paddingTop: 4, gap: 4 },
  h2hTextSummary: {
    fontSize: 10, color: Colors.textSecondary, fontWeight: '600', lineHeight: 15,
  },
  h2hTextRows: { gap: 2 },
  h2hTextRow: {
    fontSize: 9, color: Colors.textTertiary, lineHeight: 15,
    fontFamily: 'JetBrainsMono_400Regular',
  },
  h2hTextMore: { fontSize: 8, color: Colors.textTertiary, marginTop: 1 },
  h2hEmptyState: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingTop: 7, paddingBottom: 3,
  },
  h2hEmptyText: {
    fontSize: 10, color: Colors.textTertiary, fontWeight: '600',
  },

  /* Sharp verdict card */
  sharpVerdictCard: {
    borderRadius: Colors.radiusLg, borderWidth: 1.5,
    padding: 16, gap: 10, marginTop: 4,
    backgroundColor: 'rgba(255,255,255,0.03)',
  },
  sharpVerdictHeader: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  sharpVerdictPill: {
    borderRadius: 20, borderWidth: 1, paddingHorizontal: 10, paddingVertical: 4,
  },
  sharpVerdictPillText: { fontSize: 11, fontWeight: '900', letterSpacing: 0.8 },
  sharpVerdictEdge: { fontSize: 11, fontWeight: '700', marginLeft: 'auto' as any },
  sharpVerdictText: { fontSize: 14, color: Colors.text, lineHeight: 21, fontWeight: '500' },

  /* Save/New buttons */
  saveBtn: {
    backgroundColor: Colors.primary, borderRadius: Colors.radius, height: 52,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    marginBottom: 10,
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3, shadowRadius: 10, elevation: 6,
  },
  saveBtnText: { color: '#000', fontWeight: '800', fontSize: 16 },
  saveImgBtn: {
    height: 48, borderRadius: Colors.radius, borderWidth: 1.5,
    borderColor: Colors.primary, flexDirection: 'row', alignItems: 'center',
    justifyContent: 'center', gap: 8, marginBottom: 10,
    backgroundColor: 'rgba(57,255,20,0.07)',
  },
  saveImgBtnText: { color: Colors.primary, fontWeight: '700', fontSize: 15 },
  newBtn: { alignItems: 'center', paddingVertical: 14 },
  newBtnText: { color: Colors.textSecondary, fontSize: 14, fontWeight: '600' },

  /* Pressure Dynamics — glass panel treatment */
  pressureCard: {
    backgroundColor: 'rgba(255,255,255,0.03)', borderRadius: 20,
    borderWidth: 1, borderColor: Colors.borderSubtle, padding: 16, gap: 12,
    shadowColor: '#000', shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.3, shadowRadius: 12,
  },
  pressureHeaderRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  pressureTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.2 },
  pressureBody: { fontSize: 12, color: Colors.textSecondary, lineHeight: 18 },
  pressureTeamsRow: { flexDirection: 'row', alignItems: 'stretch', gap: 10 },
  pressureTeamBlock: { flex: 1, alignItems: 'center', gap: 4 },
  pressureTeamName: { fontSize: 13, fontWeight: '700', color: Colors.text, textAlign: 'center' },
  pressureLabel: { borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3 },
  pressureLabelAggressor: { backgroundColor: 'rgba(57,255,20,0.12)', borderWidth: 1, borderColor: 'rgba(57,255,20,0.3)' },
  pressureLabelDefender: { backgroundColor: 'rgba(255,149,0,0.10)', borderWidth: 1, borderColor: 'rgba(255,149,0,0.3)' },
  pressureLabelText: { fontSize: 9, fontWeight: '800', color: Colors.textSecondary, letterSpacing: 0.8 },
  pressurePossText: { fontSize: 11, color: Colors.textTertiary },
  pressureVsDivider: { width: 1, backgroundColor: Colors.borderSubtle, alignSelf: 'stretch' },

  /* Position Comparison (Proof) */
  pcCard: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    borderWidth: 1, borderColor: Colors.borderSubtle, overflow: 'hidden', marginTop: 12,
  },
  pcHeader: { padding: 14, gap: 4, borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle },
  pcTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  pcTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.2 },
  pcSub: { fontSize: 11, color: Colors.textSecondary },
  pcRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 14, paddingVertical: 10 },
  pcRowBorder: { borderTopWidth: 1, borderTopColor: Colors.borderSubtle },
  pcLeft: { flex: 1, marginRight: 12 },
  pcPlayerName: { fontSize: 13, fontWeight: '600', color: Colors.text },
  pcMeta: { fontSize: 10, color: Colors.textTertiary, marginTop: 1 },
  pcStatBadge: { fontSize: 10, color: Colors.primary, fontWeight: '700', marginTop: 3 },
  pcVal: { fontSize: 16, fontWeight: '800' },

  /* Saved state */
  savedState: { alignItems: 'center', paddingTop: 30, gap: 14 },
  savedCheck: {
    width: 72, height: 72, borderRadius: 36, backgroundColor: Colors.primary,
    alignItems: 'center', justifyContent: 'center',
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.4, shadowRadius: 16, elevation: 10,
  },
  savedTitle: { fontSize: 24, fontWeight: '800', color: Colors.text },
  savedSub: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center', lineHeight: 22 },
  viewPicksBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: Colors.primary, borderRadius: Colors.radius,
    paddingVertical: 14, paddingHorizontal: 32, marginTop: 8,
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3, shadowRadius: 10, elevation: 6,
  },
  viewPicksBtnText: { color: '#000', fontWeight: '800', fontSize: 15 },

  /* Modals */
  modalOverlay: { flex: 1, backgroundColor: Colors.overlay, justifyContent: 'flex-end' },
  modalSheet: {
    backgroundColor: Colors.card,
    borderTopLeftRadius: Colors.radiusLg, borderTopRightRadius: Colors.radiusLg,
    padding: 20, maxHeight: '70%',
  },
  modalTitle: { fontSize: 16, fontWeight: '700', color: Colors.text, marginBottom: 14 },
  modalItem: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: Colors.border,
  },
  modalItemActive: { backgroundColor: Colors.primaryDim, borderRadius: 8, paddingHorizontal: 10 },
  modalItemText: { fontSize: 15, color: Colors.text },
  modalItemTextActive: { color: Colors.primary, fontWeight: '600' },
  detectedSportBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    minHeight: 38,
    paddingHorizontal: 16,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(57,255,20,0.10)',
    backgroundColor: 'rgba(57,255,20,0.045)',
  },
  detectedSportBannerText: {
    color: Colors.primary,
    fontSize: 13,
    fontWeight: '800',
    letterSpacing: 0.3,
  },
  detectedSportBannerHint: {
    color: Colors.textTertiary,
    fontSize: 8,
    fontWeight: '700',
    letterSpacing: 1,
    marginLeft: 3,
  },
  universalPlayerSection: {
    marginBottom: 4,
    position: 'relative',
    zIndex: 9999,
    elevation: 999,
  },

  summarySection: { padding: 16, gap: 10 },
  summaryHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  summaryTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.5 },
  summaryGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  summaryItem: {
    backgroundColor: Colors.cardSecondary, borderRadius: 10, padding: 12,
    alignItems: 'center', gap: 4, minWidth: '30%' as unknown as number, flex: 1,
  },
  summaryLabel: { fontSize: 9, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.5, textAlign: 'center' },
  summaryValue: { fontSize: 18, fontWeight: '800', color: Colors.text },
  summarySub: { fontSize: 8, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.8 },

  /* Match context tag line in header */
  matchContextText: {
    fontSize: 10, color: Colors.primary, fontWeight: '600', letterSpacing: 0.5, marginTop: 2,
  },

  /* Data quality banner */
  dataQualityBanner: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 6,
    backgroundColor: '#F59E0B15', paddingHorizontal: 14, paddingVertical: 8,
    borderTopWidth: 1, borderTopColor: '#F59E0B30',
  },
  dataQualityText: { fontSize: 11, color: '#F59E0B', flex: 1, lineHeight: 16 },

  /* Sharp Intelligence section */
  sharpIntelBox: {
    backgroundColor: '#FFD70008', paddingHorizontal: 16, paddingBottom: 4,
  },
  sharpIntelHeader: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingTop: 12, paddingBottom: 8,
  },
  sharpIntelLabel: {
    fontSize: 10, fontWeight: '800', color: '#FFD700', letterSpacing: 1.5,
  },
  sharpRow: {
    paddingBottom: 12, marginBottom: 2,
    borderBottomWidth: 1, borderBottomColor: Colors.border + '60',
  },
  sharpRowTitle: {
    fontSize: 10, fontWeight: '800', color: Colors.textTertiary,
    letterSpacing: 0.8, marginBottom: 4,
  },
  sharpRowText: {
    fontSize: 13, color: Colors.textSecondary, lineHeight: 19,
  },

  /* Line vs Season Average */
  lineVsAvgRow: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: 16, paddingVertical: 12, gap: 12,
  },
  lineVsAvgLeft: {
    alignItems: 'center', minWidth: 56,
  },
  lineVsAvgLabel: {
    fontSize: 9, fontWeight: '700', color: Colors.textTertiary,
    letterSpacing: 0.8, textTransform: 'uppercase', marginBottom: 2,
  },
  lineVsAvgVal: {
    fontSize: 18, fontWeight: '800', color: Colors.text,
  },
  lineVsAvgMid: {
    flex: 1, gap: 2,
  },
  lineVsAvgDelta: {
    fontSize: 16, fontWeight: '800',
  },
  lineVsAvgNote: {
    fontSize: 11, color: Colors.textTertiary, letterSpacing: 0.2,
  },
  devBandPill: {
    borderRadius: 8, borderWidth: 1,
    paddingHorizontal: 10, paddingVertical: 6,
    alignItems: 'center', gap: 2,
  },
  devBandPillText: {
    fontSize: 10, fontWeight: '800', letterSpacing: 0.8,
  },
  devBandPillHit: {
    fontSize: 9, fontWeight: '700',
  },

  /* Edge explanation block */
  edgeExplainBox: {
    marginHorizontal: 16,
    marginBottom: 14,
    padding: 14,
    backgroundColor: '#0a0a0a',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#1e1e1e',
  },
  edgeExplainHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    marginBottom: 8,
  },
  edgeExplainTitle: {
    fontSize: 9,
    fontWeight: '800',
    letterSpacing: 1.2,
  },
  edgeExplainBody: {
    fontSize: 13,
    color: '#aaa',
    lineHeight: 20,
  },

  // ── Game Script Intelligence ─────────────────────────────────────────────
  gsCard: {
    marginHorizontal: 16,
    marginBottom: 14,
    padding: 14,
    backgroundColor: '#080808',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#1a1a1a',
  },
  gsHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    marginBottom: 12,
  },
  gsTitle: {
    fontSize: 9,
    fontWeight: '800',
    letterSpacing: 1.2,
  },
  gsScenario: {
    marginBottom: 10,
  },
  gsScenarioHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  gsScenarioLabel: {
    fontSize: 9,
    fontWeight: '700',
    letterSpacing: 0.8,
  },
  gsScenarioProb: {
    fontSize: 10,
    color: '#6B7280',
  },
  gsBarBg: {
    height: 4,
    backgroundColor: '#1a1a1a',
    borderRadius: 2,
    marginBottom: 5,
    overflow: 'hidden',
  },
  gsBarFill: {
    height: 4,
    borderRadius: 2,
  },
  gsScenarioStats: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  gsStatVal: {
    fontSize: 11,
    color: '#9CA3AF',
  },
  gsStatBadge: {
    fontSize: 9,
    fontWeight: '700',
    borderWidth: 1,
    borderRadius: 4,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  gsSummaryRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    marginTop: 8,
    marginBottom: 8,
  },
  gsSummaryPill: {
    backgroundColor: '#111',
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 4,
    alignItems: 'center',
    minWidth: 70,
  },
  gsSummaryLabel: {
    fontSize: 8,
    color: '#6B7280',
    fontWeight: '600',
    letterSpacing: 0.5,
    marginBottom: 1,
  },
  gsSummaryVal: {
    fontSize: 12,
    fontWeight: '800',
  },
  gsFinding: {
    fontSize: 11,
    color: '#7B8A99',
    lineHeight: 17,
    marginTop: 4,
    borderTopWidth: 1,
    borderTopColor: '#111',
    paddingTop: 8,
  },
  gsIntelSection: {
    marginTop: 8,
    marginBottom: 4,
    borderTopWidth: 1,
    borderTopColor: '#1a1a1a',
    paddingTop: 10,
  },
  gsIntelTitle: {
    fontSize: 8,
    fontWeight: '700',
    color: '#4B5563',
    letterSpacing: 1,
    marginBottom: 8,
  },
  gsIntelRow: {
    flexDirection: 'row',
    gap: 8,
    flexWrap: 'wrap',
  },
  gsIntelChip: {
    flex: 1,
    minWidth: 120,
    backgroundColor: '#0d0d0d',
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
  },
  gsIntelChipLabel: {
    fontSize: 8,
    fontWeight: '700',
    letterSpacing: 0.8,
    marginBottom: 4,
  },
  gsIntelChipVal: {
    fontSize: 22,
    fontWeight: '900',
    marginBottom: 2,
  },
  gsIntelChipSub: {
    fontSize: 9,
    color: '#6B7280',
    marginBottom: 3,
  },
  gsIntelChipDir: {
    fontSize: 10,
    fontWeight: '700',
  },
  gsWarningBanner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 6,
    backgroundColor: '#1C1400',
    borderWidth: 1,
    borderColor: '#FBBF24',
    borderRadius: 6,
    padding: 8,
    marginBottom: 8,
  },
  gsWarningText: {
    fontSize: 10,
    color: '#FBBF24',
    fontWeight: '600',
    flex: 1,
    lineHeight: 15,
  },
  // ── New Game Script Banner (big + highlighted)
  gsBanner: {
    flexDirection: 'row',
    marginHorizontal: 16,
    marginBottom: 14,
    borderRadius: 10,
    borderWidth: 1,
    overflow: 'hidden',
    backgroundColor: '#0a0a0a',
  },
  gsBannerStripe: { width: 4 },
  gsBannerBody: { flex: 1, padding: 14, gap: 6 },
  gsBannerHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  gsBannerIconWrap: {
    width: 30, height: 30, borderRadius: 8,
    alignItems: 'center', justifyContent: 'center',
  },
  gsBannerLabel: { fontSize: 9, fontWeight: '800', letterSpacing: 1.2 },
  gsBannerProbBadge: {
    borderRadius: 8, borderWidth: 1,
    paddingHorizontal: 10, paddingVertical: 4,
    marginLeft: 'auto',
  },
  gsBannerProb: { fontSize: 12, fontWeight: '800' },
  gsBannerTitle: { fontSize: 18, fontWeight: '900', letterSpacing: 0.3 },
  gsBannerScenarios: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 6 },
  gsBannerChip: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: '#151515', borderRadius: 5, borderWidth: 1,
    paddingHorizontal: 8, paddingVertical: 4,
  },
  gsBannerChipName: { fontSize: 9, color: '#9CA3AF', fontWeight: '600' },
  gsBannerChipPct: { fontSize: 9, fontWeight: '800' },
  gsBannerBottom: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginTop: 8, paddingTop: 8, borderTopWidth: 1, borderTopColor: '#1a1a1a',
  },
  gsBannerSub: { fontSize: 10, color: '#6B7280', fontWeight: '500' },
  gsBannerImplied: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  gsBannerImpliedText: { fontSize: 10, fontWeight: '700' },
  gsBannerImpliedDivider: { fontSize: 10, color: '#444' },
});
