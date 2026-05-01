import { Platform } from 'react-native';

const getApiBase = (): string => {
  // On web: use relative URL — the proxy server handles /api → localhost:8000
  if (Platform.OS === 'web' && typeof window !== 'undefined') {
    return '';
  }
  // For native app builds: use EXPO_PUBLIC_API_URL or localhost fallback
  const env = process.env.EXPO_PUBLIC_API_URL;
  if (env) return env;
  return 'http://localhost:8000';
};

async function apiCall<T = unknown>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const base = getApiBase();
  const url = `${base}${endpoint}`;
  let resp: Response;
  try {
    resp = await fetch(url, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    });
  } catch (e) {
    throw new Error('Cannot reach server. Please try again.');
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => null);
    const detail = (err as { detail?: string | Array<{ msg?: string }> } | null)?.detail;
    let message: string;
    if (Array.isArray(detail)) {
      message = detail.map((d) => d.msg || 'Validation error').join(', ');
    } else if (typeof detail === 'string' && detail.trim()) {
      message = detail;
    } else if (resp.status === 404) {
      message = `Endpoint missing (${endpoint}). Please refresh and try again.`;
    } else if (resp.status === 502 || resp.status === 503 || resp.status === 504) {
      message = 'Server is unreachable right now. Please try again in a moment.';
    } else if (resp.status === 401 || resp.status === 403) {
      message = 'Your session expired. Please sign in again.';
    } else if (resp.status >= 500) {
      message = `Server error (${resp.status}). Please try again.`;
    } else {
      message = `Request failed (${resp.status}).`;
    }
    if (typeof console !== 'undefined') {
      console.warn('[apiCall] failed', { endpoint, status: resp.status, detail });
    }
    throw new Error(message);
  }
  return resp.json() as Promise<T>;
}

export interface AuthResponse {
  email: string;
  session_token: string;
  access_type?: string;
}

export interface AccessCheckResponse {
  verified?: boolean;
  email?: string;
  session_token?: string;
  access_type?: string;
  requires_password?: boolean;
  requires_password_setup?: boolean;
  message?: string;
  denied?: boolean;
  denial_reason?: string;
}

export async function verifyAccess(email: string): Promise<AccessCheckResponse> {
  return apiCall<AccessCheckResponse>('/api/auth/verify-access', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

export async function setPassword(email: string, password: string): Promise<AuthResponse> {
  return apiCall<AuthResponse>('/api/auth/set-password', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

export async function authLogin(email: string, password: string): Promise<AuthResponse> {
  return apiCall<AuthResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

export async function authLogout(email: string, session_token: string) {
  return apiCall('/api/auth/logout', {
    method: 'POST',
    body: JSON.stringify({ email, session_token }),
  });
}

export async function verifySession(email: string, session_token: string) {
  return apiCall('/api/auth/verify-session', {
    method: 'POST',
    body: JSON.stringify({ email, session_token }),
  });
}

export async function linkPayment(loginEmail: string, paymentEmail: string): Promise<AccessCheckResponse> {
  return apiCall<AccessCheckResponse>('/api/auth/link-payment', {
    method: 'POST',
    body: JSON.stringify({ login_email: loginEmail, payment_email: paymentEmail }),
  });
}

export async function createCheckout(email: string, planKey: string): Promise<{ checkoutUrl?: string; checkout_url?: string; redirect_url?: string; error?: string }> {
  const redirectUrl = typeof window !== 'undefined'
    ? `${window.location.origin}/auth`
    : 'https://reversepicks.com/auth';
  return apiCall('/api/stripe/create-checkout', {
    method: 'POST',
    body: JSON.stringify({ email, planKey, redirectUrl }),
  });
}

export interface ScanResult {
  playerName?: string;
  playerTeam?: string;
  teamName?: string;
  opponentName?: string;
  propType?: string;
  line?: number;
  playerId?: number;
  teamId?: number;
  opponentId?: number;
  leagueId?: number;
  venue?: string;
  error?: string;
}

interface RawPick {
  extracted?: {
    playerName?: string;
    propType?: string;
    line?: number;
    venue?: string;
    opponentName?: string;
    playerTeam?: string;
    league?: string;
    leagueId?: number;
  };
  resolved?: {
    id?: number;
    playerId?: number;
    teamId?: number;
    teamName?: string;
    name?: string;
  };
  resolvedOpponent?: {
    id?: number;
    name?: string;
  };
}

export async function scanProp(imageBase64: string, sport = 'soccer'): Promise<ScanResult> {
  const resp = await apiCall<{ picks?: RawPick[]; success?: boolean; error?: string }>(
    '/api/scan-prop',
    { method: 'POST', body: JSON.stringify({ image_base64: imageBase64, sport }) }
  );
  if (resp.error) return { error: resp.error };
  if (resp.picks && resp.picks.length > 0) {
    const pick = resp.picks[0];
    const ext = pick.extracted || {};
    const res = pick.resolved || {};
    const opp = pick.resolvedOpponent || {};
    return {
      playerName: ext.playerName,
      propType: ext.propType,
      line: ext.line,
      venue: ext.venue,
      opponentName: ext.opponentName || opp.teamName || opp.name,
      playerTeam: ext.playerTeam,
      teamName: res.teamName || ext.playerTeam,
      leagueId: ext.leagueId,
      playerId: res.id || res.playerId,
      teamId: res.teamId,
      opponentId: opp.teamId || opp.id,
    };
  }
  return { error: 'No prop data detected. Try a clearer image.' };
}

export interface GameLog {
  date: string;
  opponent: string;
  venue: string;
  value: number | null;
  minutes: number;
  score?: string;
  oppRank?: number | null;
  teamPossession?: number | null;
  opponentPossession?: number | null;
  blocks?: number | null;
  interceptions?: number | null;
  tackles?: number | null;
  clearances?: number | null;
  synthetic?: boolean;
}

export interface H2HMatch {
  date: string;
  score: string;
  venue: string;
  minutes: number;
  targetStat: number | null;
  opponent: string;
  teamPossession?: number | null;
  opponentPossession?: number | null;
}

export interface PredictionResult {
  playerName?: string;
  teamName?: string;
  propType?: string;
  line?: number;
  projection?: number;
  confidence?: number;
  rawConfidence?: number;
  recommendation?: 'OVER' | 'UNDER' | 'PASS';
  reasoning?: string;
  tacticalBreakdown?: string;
  blendNote?: string;
  aiProjection?: number;
  bayesianComponent?: number;
  bayesianProjection?: number;
  edgeScore?: number;
  fixtureDate?: string;
  opponentName?: string;
  confidenceLevel?: string;
  confidenceInterval?: [number, number];
  priorMean?: number;
  lineDeviationBand?: string;
  lineDeviationPct?: number;
  lineDeviationHitRate?: number;
  gameScript?: {
    p_team_trails?: number;
    p_opponent_scores_first?: number;
    trailing_avg?: number;
    normal_avg?: number;
    overall_avg?: number;
    inflation_factor?: number;
    inflated_proj?: number;
    script_adjusted_proj?: number;
    confidence_delta?: number;
    sample_size?: number;
    trailing_sample_size?: number;
    key_finding?: string;
    trailing_near_line?: boolean;
    p_player_team_scores_first?: number;
    fts_no_goal_pct?: number;
    fts_sample?: number;
    positional_depth?: {
      vs_dominant_trailing_avg?: number;
      vs_moderate_trailing_avg?: number;
      vs_dominant_sample?: number;
      vs_moderate_sample?: number;
    };
    opponent_facilitation?: {
      avg_allowed?: number;
      sample_size?: number;
      fixtures_analysed?: number;
      facilitates?: boolean;
      position_label?: string;
    };
    scenarios?: Array<{
      label: string;
      probability?: number;
      projected_stat?: number;
      vs_line?: number;
      direction?: string;
    }>;
  };
  momentumMean?: number;
  momentumEffect?: number;
  momentumLabel?: string;
  streakFlag?: string;
  pOver?: number;
  pUnder?: number;
  volatility?: string;
  priorSamples?: number;
  covariateAdjustment?: number;
  reversalFlag?: string;
  gameLogs?: GameLog[];
  homeAvg?: number;
  awayAvg?: number;
  sampleSize?: number;
  hitRates?: { overHits: number; underHits: number; overPct: number; underPct: number; total: number };
  h2hPlayerStats?: { matches: H2HMatch[]; avgVsOpponent?: number; sampleSize: number; targetProp?: string };
  positionComparison?: { positionShort?: string; opponent?: string; venue?: string; avgStatValue?: number; sampleSize?: number; players?: Record<string, unknown>[] };
  expectedPossession?: { home: number; away: number };
  possessionMultiplier?: number;
  possessionTeamAvg?: number;
  possessionOppAvg?: number;
  moneyline?: { home: string; draw: string; away: string };
  expectedGameType?: string;
  favorite?: string;
  keyMatchupFactor?: string;
  homeTeam?: string;
  awayTeam?: string;
  teamId?: number;
  opponentId?: number;
  leagueId?: number;
  playerId?: number;
  playerPosition?: string;
  playerRole?: string;
  sharpSummary?: string;
  keyEvidence?: string;
  gameFlowDynamics?: string;
  scenarioAnalysis?: string;
  matchContext?: { league?: string; round?: string; date?: string };
  gameSituation?: {
    isKnockout: boolean;
    isSecondLeg: boolean;
    aggregate: {
      firstLegFound: boolean;
      firstLegScore: string;
      homeTeamAggregate: number;
      awayTeamAggregate: number;
      goalDeficit: number;
      homeTeamTrailing: boolean;
      mustWinByGoals: number;
    };
    injuries: string;
  };
  dataQuality?: { level?: string; message?: string; gamesWithData?: number; totalGames?: number };
  analysisSummary?: {
    statLabel?: string;
    venue?: string;
    venueSampleSize?: number;
    venueAverage?: number | null;
    opponentAllowedAverage?: number | null;
    goalkeeperSaveRate?: number | null;
    goalkeeperSaveSample?: number | null;
    opponentShotsOnTarget?: number | null;
  };
  error?: string;
}

interface RawPrediction {
  player?: { id?: number; name?: string; team?: string; position?: string; role?: string };
  propType?: string;
  line?: number;
  projectedValue?: number;
  recommendation?: string;
  confidenceScore?: number;
  confidenceLevel?: string;
  confidenceInterval?: [number, number];
  reasoning?: string;
  tacticalBreakdown?: string;
  blendNote?: string;
  aiProjection?: number;
  bayesianComponent?: number;
  opponent?: string;
  _request?: {
    teamId?: number;
    opponentId?: number;
    leagueId?: number;
    playerId?: number;
    venue?: string;
  };
  bayesianMetrics?: {
    posteriorMean?: number;
    edgeZ?: number;
    momentumEffect?: number;
    momentumMean?: number;
    momentumLabel?: string;
    priorMean?: number;
    streakFlag?: string;
    pOver?: number;
    pUnder?: number;
    reversalFlag?: string;
    volatility?: string;
    priorSamples?: number;
    covariateAdjustment?: number;
    cv?: number;
  };
  playerGameLogs?: {
    games?: Record<string, unknown>[];
    homeAvg?: number;
    awayAvg?: number;
    hitRates?: { overHits: number; underHits: number; overPct: number; underPct: number; total: number; summary?: string };
  };
  h2hPlayerStats?: {
    matches?: Array<{
      date?: string;
      score?: string;
      venue?: string;
      minutes?: number;
      targetStat?: number | null;
      opponent?: string;
    }>;
    avgVsOpponent?: number;
    sampleSize?: number;
    targetProp?: string;
  };
  matchDominance?: {
    applied?: boolean;
    multiplier?: number;
    expectedPoss?: number;
    teamSeasonAvg?: number;
    oppSeasonAvg?: number;
    notes?: string[];
  };
  matchupOverview?: {
    expectedPossession?: { home: number; away: number };
    homeTeam?: string;
    awayTeam?: string;
    moneyline?: { home: string; draw: string; away: string };
    favorite?: string;
    expectedGameType?: string;
    keyMatchupFactor?: string;
  };
  positionComparison?: Record<string, unknown>;
  sharpSummary?: string;
  keyEvidence?: string;
  gameFlowDynamics?: string;
  scenarioAnalysis?: string;
  matchContext?: { league?: string; round?: string; date?: string };
  gameSituation?: Record<string, unknown>;
  dataQuality?: { level?: string; message?: string; gamesWithData?: number; totalGames?: number };
  analysisSummary?: {
    statLabel?: string;
    venue?: string;
    venueSampleSize?: number;
    venueAverage?: number | null;
    opponentAllowedAverage?: number | null;
    goalkeeperSaveRate?: number | null;
    goalkeeperSaveSample?: number | null;
    opponentShotsOnTarget?: number | null;
  };
  error?: string;
}

const GAME_LOG_FIELD_MAP: Record<string, string> = {
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

export async function predict(request: Record<string, unknown>): Promise<PredictionResult> {
  const raw = await apiCall<RawPrediction>('/api/predict', {
    method: 'POST',
    body: JSON.stringify(request),
  });
  if (raw.error) return { error: raw.error };
  const rec = raw.recommendation?.toUpperCase() as 'OVER' | 'UNDER' | 'PASS' | undefined;
  const bm = raw.bayesianMetrics || {};

  const propTypeStr = (raw.propType || request.propType as string || '');
  const statField = GAME_LOG_FIELD_MAP[propTypeStr];
  const rawGames = (raw.playerGameLogs?.games || []) as Record<string, unknown>[];
  const gameLogs: GameLog[] = rawGames.length > 0
    ? rawGames
        .map(g => {
          // Prefer the mapped field, fall back to backend-computed targetStat
          const mappedVal = statField ? (g[statField] as number | null | undefined) : undefined;
          const value = mappedVal != null ? mappedVal : (g.targetStat as number | null | undefined) ?? null;
          return {
            date: (g.date as string) || '',
            opponent: (g.opponent as string) || '',
            venue: (g.venue as string) || '',
            value,
            minutes: (g.minutes as number) || 0,
            score: (g.score as string) || undefined,
            oppRank: (g.oppRank as number | null) ?? undefined,
            teamPossession: (g.teamPossession as number | null) ?? null,
            opponentPossession: (g.opponentPossession as number | null) ?? null,
            blocks: (g.tackles_blocks as number | null) ?? null,
            interceptions: (g.tackles_interceptions as number | null) ?? null,
            tackles: (g.tackles_total as number | null) ?? null,
            clearances: (g.tackles_clearances as number | null) ?? null,
            synthetic: !!(g.synthetic),
          };
        })
        .filter(g => g.value != null)
    : [];

  return {
    playerName: raw.player?.name || (request.playerName as string) || '',
    teamName: raw.player?.team || (request.teamName as string) || '',
    opponentName: raw.opponent || (request.opponentName as string) || '',
    propType: raw.propType || (request.propType as string) || '',
    line: raw.line ?? (request.line as number) ?? 0,
    projection: raw.projectedValue,
    confidence: raw.confidenceScore,
    rawConfidence: raw.rawConfidence ?? raw.confidenceScore,
    recommendation: rec,
    reasoning: raw.tacticalBreakdown || raw.reasoning,
    confidenceLevel: raw.confidenceLevel,
    confidenceInterval: raw.confidenceInterval,
    bayesianProjection: bm.posteriorMean,
    edgeScore: bm.edgeZ,
    priorMean: bm.priorMean,
    momentumMean: bm.momentumMean,
    momentumEffect: bm.momentumEffect,
    momentumLabel: bm.momentumLabel,
    streakFlag: bm.streakFlag,
    pOver: bm.pOver,
    pUnder: bm.pUnder,
    volatility: bm.volatility,
    priorSamples: bm.priorSamples,
    covariateAdjustment: bm.covariateAdjustment,
    reversalFlag: bm.reversalFlag,
    gameLogs: gameLogs.length > 0 ? gameLogs : undefined,
    homeAvg: raw.playerGameLogs?.homeAvg,
    awayAvg: raw.playerGameLogs?.awayAvg,
    sampleSize: rawGames.length || undefined,
    hitRates: raw.playerGameLogs?.hitRates
      ? {
          overHits: raw.playerGameLogs.hitRates.overHits,
          underHits: raw.playerGameLogs.hitRates.underHits,
          overPct: raw.playerGameLogs.hitRates.overPct,
          underPct: raw.playerGameLogs.hitRates.underPct,
          total: raw.playerGameLogs.hitRates.total,
        }
      : undefined,
    h2hPlayerStats: raw.h2hPlayerStats?.matches?.length
      ? {
          matches: raw.h2hPlayerStats.matches.map(m => ({
            date: m.date || '',
            score: m.score || m.matchScore || '',
            venue: m.venue || '',
            minutes: m.minutesPlayed || m.minutes || 0,
            targetStat: m.targetStat ?? null,
            opponent: m.opponent || '',
            teamPossession: (m.teamPossession as number | null) ?? null,
            opponentPossession: (m.opponentPossession as number | null) ?? null,
          })),
          avgVsOpponent: raw.h2hPlayerStats.avgVsOpponent,
          sampleSize: raw.h2hPlayerStats.sampleSize || 0,
          targetProp: raw.h2hPlayerStats.targetProp,
        }
      : undefined,
    expectedPossession: raw.matchupOverview?.expectedPossession
      ?? (raw.matchDominance?.expectedPoss != null && raw.matchDominance.expectedPoss !== 50
        ? { home: raw.matchDominance.expectedPoss, away: 100 - raw.matchDominance.expectedPoss }
        : undefined),
    possessionMultiplier: raw.matchDominance?.multiplier,
    possessionTeamAvg: raw.matchDominance?.teamSeasonAvg ?? undefined,
    possessionOppAvg: raw.matchDominance?.oppSeasonAvg ?? undefined,
    moneyline: raw.matchupOverview?.moneyline ?? undefined,
    expectedGameType: raw.matchupOverview?.expectedGameType ?? undefined,
    favorite: raw.matchupOverview?.favorite ?? undefined,
    keyMatchupFactor: raw.matchupOverview?.keyMatchupFactor ?? undefined,
    homeTeam: raw.matchupOverview?.homeTeam ?? undefined,
    awayTeam: raw.matchupOverview?.awayTeam ?? undefined,
    positionComparison: raw.positionComparison ?? undefined,
    teamId: raw._request?.teamId || (request.teamId as number) || undefined,
    opponentId: raw._request?.opponentId || (request.opponentId as number) || undefined,
    leagueId: raw._request?.leagueId || (request.leagueId as number) || undefined,
    playerId: raw._request?.playerId || raw.player?.id || undefined,
    playerPosition: raw.player?.position || undefined,
    playerRole: raw.player?.role || undefined,
    sharpSummary: raw.sharpSummary || undefined,
    keyEvidence: raw.keyEvidence || undefined,
    gameFlowDynamics: raw.gameFlowDynamics || undefined,
    scenarioAnalysis: raw.scenarioAnalysis || undefined,
    matchContext: raw.matchContext ? { league: raw.matchContext.league, round: raw.matchContext.round, date: raw.matchContext.date } : undefined,
    gameSituation: raw.gameSituation ?? undefined,
    gameScript: raw.gameScript ?? undefined,
    lineDeviationBand: raw.lineDeviationBand ?? undefined,
    lineDeviationPct: raw.lineDeviationPct ?? undefined,
    lineDeviationHitRate: raw.lineDeviationHitRate ?? undefined,
    dataQuality: raw.dataQuality ? { level: raw.dataQuality.level, message: raw.dataQuality.message, gamesWithData: raw.dataQuality.gamesWithData, totalGames: raw.dataQuality.totalGames } : undefined,
    analysisSummary: raw.analysisSummary ?? undefined,
    tacticalBreakdown: raw.tacticalBreakdown || raw.reasoning || undefined,
    blendNote: raw.blendNote || undefined,
    aiProjection: raw.aiProjection || undefined,
    bayesianComponent: raw.bayesianComponent || undefined,
  };
}

export interface Pick {
  _id?: string;
  id?: string;
  pickId?: string;
  playerName: string;
  teamName?: string;
  opponentName?: string;
  propType: string;
  line: number;
  projection?: number;
  recommendation?: string;
  confidence?: number;
  confidenceLevel?: string;
  projectedValue?: number;
  status?: string;
  result?: string;
  actualValue?: number | null;
  currentValue?: number | null;
  pace?: number | null;
  hitPct?: number | null;
  elapsed?: number | null;
  period?: string;
  matchStatus?: string;
  fixtureId?: number | null;
  createdAt?: string;
  sport?: string;
  venue?: string;
  trackingId?: string;
  position?: string;
  role?: string;
  coinFlip?: boolean;
  matchScore?: string;
  finalHomeGoals?: number | null;
  finalAwayGoals?: number | null;
  homeTeam?: string;
  awayTeam?: string;
  homePoss?: number | null;
  awayPoss?: number | null;
  projHomePoss?: number | null;
  projAwayPoss?: number | null;
}

export async function listPicks(email: string, token: string): Promise<Pick[]> {
  const resp = await apiCall<{ picks: Record<string, unknown>[] }>('/api/picks/list', {
    method: 'POST',
    body: JSON.stringify({ email, token }),
  });
  return (resp.picks || []).map(p => ({
    pickId: p.pickId as string,
    _id: (p.pickId as string) || (p._id as string),
    id: (p.pickId as string) || (p.id as string),
    playerName: (p.playerName as string) || '',
    teamName: p.teamName as string,
    opponentName: p.opponentName as string,
    propType: (p.propType as string) || '',
    line: (p.line as number) || 0,
    // normalize projectedValue → projection
    projection: (p.projectedValue as number) ?? (p.projection as number),
    // normalize to uppercase OVER/UNDER
    recommendation: ((p.recommendation as string) || '').toUpperCase() || undefined,
    // normalize confidenceScore → confidence
    confidence: (p.confidenceScore as number) ?? (p.confidence as number),
    confidenceLevel: p.confidenceLevel as string,
    projectedValue: p.projectedValue as number,
    status: p.status as string,
    result: p.result as string,
    actualValue: p.actualValue as number ?? null,
    currentValue: (p.currentValue as number) ?? null,
    pace: (p.pace as number) ?? null,
    hitPct: (p.hitPct as number) ?? null,
    elapsed: (p.elapsed as number) ?? null,
    period: p.period as string,
    matchStatus: p.matchStatus as string,
    fixtureId: (p.fixtureId as number) ?? null,
    createdAt: (p.timestamp as string) || (p.createdAt as string),
    sport: p.sport as string,
    venue: p.venue as string,
    trackingId: p.trackingId as string,
    position: (p.position as string) || undefined,
    role: (p.role as string) || undefined,
    coinFlip: (p.coinFlip as boolean) || undefined,
    matchScore: (p.matchScore as string) || undefined,
    finalHomeGoals: (p.finalHomeGoals as number) ?? null,
    finalAwayGoals: (p.finalAwayGoals as number) ?? null,
    homeTeam: (p.homeTeam as string) || undefined,
    awayTeam: (p.awayTeam as string) || undefined,
    homePoss: (p.homePoss as number) ?? null,
    awayPoss: (p.awayPoss as number) ?? null,
    projHomePoss: (p.projHomePoss as number) ?? null,
    projAwayPoss: (p.projAwayPoss as number) ?? null,
  }));
}

export async function savePick(email: string, token: string, pick: Record<string, unknown>) {
  return apiCall('/api/picks/save', {
    method: 'POST',
    body: JSON.stringify({ email, token, pick }),
  });
}

export async function deletePick(email: string, token: string, pickId: string) {
  return apiCall('/api/picks/delete', {
    method: 'POST',
    body: JSON.stringify({ email, token, pickId }),
  });
}

export async function fetchPickAnalysis(email: string, token: string, pickId: string): Promise<{ found: boolean; analysis?: Record<string, unknown> }> {
  const params = new URLSearchParams({ email, token, pickId });
  return apiCall<{ found: boolean; analysis?: Record<string, unknown> }>(`/api/picks/analysis?${params.toString()}`);
}

export interface IntelDashboard {
  topPicks?: unknown[];
  insights?: string;
  marketTrends?: unknown[];
}

export async function getIntelDashboard(email: string, token: string): Promise<IntelDashboard> {
  return apiCall<IntelDashboard>(
    `/api/intel/dashboard?email=${encodeURIComponent(email)}&token=${encodeURIComponent(token)}&sport=soccer`
  );
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

export async function startChat(sessionId?: string): Promise<{ session_id: string; message: string }> {
  return apiCall('/api/chat/start', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export async function sendChatMessage(sessionId: string, message: string): Promise<{ response: string }> {
  return apiCall('/api/chat/message', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, message }),
  });
}

export async function searchPlayers(query: string, leagueId?: number) {
  return apiCall('/api/players/search', {
    method: 'POST',
    body: JSON.stringify({ query, league_id: leagueId }),
  });
}

export interface TeamSearchResult {
  teamId: number;
  teamName: string;
  leagueId: number;
}

export async function searchTeams(query: string, leagueId?: number): Promise<{ results: TeamSearchResult[] }> {
  const params = new URLSearchParams({ q: query });
  if (leagueId) params.set('league_id', String(leagueId));
  return apiCall(`/api/search/teams?${params.toString()}`);
}

export interface LeagueSearchResult {
  id: number;
  name: string;
  country: string;
  logo?: string;
}

export async function searchLeagues(query: string): Promise<{ leagues: LeagueSearchResult[] }> {
  const params = new URLSearchParams({ search: query });
  return apiCall(`/api/leagues/search?${params.toString()}`);
}

export interface PlayerSearchResult {
  playerId: number;
  playerName: string;
  teamId: number;
  teamName: string;
  leagueId: number;
  position?: string;
}

export async function searchPlayersQuick(query: string, leagueId?: number): Promise<{ players: PlayerSearchResult[] }> {
  return apiCall('/api/players/search', {
    method: 'POST',
    body: JSON.stringify({ query, league_id: leagueId }),
  });
}

export interface SubscriptionStatus {
  active: boolean;
  plan?: string;
  planKey?: string;
  planLabel?: string;
  cadence?: string;
  status?: string;
  cardLast4?: string;
  cardBrand?: string;
  subscribedAt?: string;
  expiresAt?: string;
  canceledAt?: string;
}

export async function getSubscriptionStatus(email: string, accessType?: string): Promise<SubscriptionStatus> {
  const isStripe = accessType?.toLowerCase().includes('stripe');
  if (isStripe) {
    return apiCall<SubscriptionStatus>(`/api/stripe/status/${encodeURIComponent(email)}`);
  }
  return apiCall<SubscriptionStatus>(`/api/square/status/${encodeURIComponent(email)}`);
}

export async function cancelSubscription(email: string, accessType?: string): Promise<{ success: boolean; status?: string; message?: string }> {
  const isStripe = accessType?.toLowerCase().includes('stripe');
  if (isStripe) {
    return apiCall('/api/stripe/cancel', {
      method: 'POST',
      body: JSON.stringify({ email }),
    });
  }
  return apiCall('/api/square/cancel', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

export async function changePlan(email: string, newPlanKey: string, accessType?: string): Promise<{ success?: boolean; previous_plan?: string; new_plan?: string; new_label?: string; message?: string }> {
  const isStripe = accessType?.toLowerCase().includes('stripe');
  if (isStripe) {
    return apiCall('/api/stripe/change-plan', {
      method: 'POST',
      body: JSON.stringify({ email, new_plan_key: newPlanKey }),
    });
  }
  return apiCall('/api/square/change-plan', {
    method: 'POST',
    body: JSON.stringify({ email, new_plan_key: newPlanKey }),
  });
}

export async function resubscribeCheckout(email: string, planKey: string, accessType?: string): Promise<{ checkoutUrl?: string; checkout_url?: string; redirect_url?: string; error?: string }> {
  const redirectUrl = typeof window !== 'undefined'
    ? `${window.location.origin}/auth`
    : 'https://reversepicks.com/auth';
  const isStripe = accessType?.toLowerCase().includes('stripe');
  if (isStripe) {
    return apiCall('/api/stripe/resubscribe-checkout', {
      method: 'POST',
      body: JSON.stringify({ email, planKey, redirectUrl }),
    });
  }
  return apiCall('/api/square/resubscribe-checkout', {
    method: 'POST',
    body: JSON.stringify({ email, planKey, redirectUrl }),
  });
}

export interface AnalyticsBucket {
  label: string;
  hits: number;
  misses: number;
  total: number;
  winPct: number;
}

export interface AnalyticsData {
  overall: { hits: number; misses: number; total: number; winPct: number };
  streak: { type: string | null; count: number };
  recentForm: { result: string; name: string }[];
  byDirection: AnalyticsBucket[];
  byVenue: AnalyticsBucket[];
  byPosition: AnalyticsBucket[];
  byPropType: AnalyticsBucket[];
  byLeague: AnalyticsBucket[];
}

export async function getOwnerAnalytics(): Promise<AnalyticsData> {
  return apiCall('/api/admin/analytics');
}

export interface PlayerPickRow {
  playerName: string;
  position: string;
  posRaw: string;
  propType: string;
  direction: string;
  line: number | null;
  projection: number | null;
  deviationPct: number | null;
  band: string;
  bandOrder: number;
  venue: string;
  result: string;
  actual: number | null;
  opponent: string;
  teamName: string;
  league: string;
  againstBook: boolean;
  confidence: number | null;
  date: string;
}

export interface BandSummaryRow {
  band: string;
  bandOrder: number;
  propType: string;
  direction: string;
  position: string;
  venue: string;
  hitPct: number;
  hits: number;
  misses: number;
  total: number;
  avgLine: number | null;
  uniquePlayers: number;
  league: string;
}

export interface OverallBandRow {
  band: string;
  direction: string;
  hitPct: number;
  hits: number;
  total: number;
  bandOrder: number;
}

export interface TopPropsData {
  playerRows: PlayerPickRow[];
  bandSummary: BandSummaryRow[];
  overallSummary: OverallBandRow[];
  totalDeduped: number;
  totalRaw: number;
}

// Legacy alias — some old imports may still reference this
export type TopPropsRow = BandSummaryRow;

export async function getTopPropsTable(): Promise<TopPropsData> {
  return apiCall('/api/admin/top-props-table');
}

export const PLAN_OPTIONS = [
  { key: 'weekly', name: 'Weekly', price: '$11/week' },
  { key: 'monthly', name: 'Monthly', price: '$39.99/month' },
  { key: 'quarterly', name: 'Quarterly', price: '$99.99/3 months' },
] as const;

export const PROP_TYPES = [
  { value: 'pass_attempts', label: 'Pass Attempts' },
  { value: 'shots', label: 'Shots' },
  { value: 'shots_on_target', label: 'Shots on Target' },
  { value: 'goals', label: 'Goals' },
  { value: 'assists', label: 'Assists' },
  { value: 'key_passes', label: 'Key Passes' },
  { value: 'tackles', label: 'Tackles' },
  { value: 'saves', label: 'Saves' },
  { value: 'dribbles', label: 'Dribbles' },
  { value: 'crosses', label: 'Crosses' },
];

export const LEAGUES = [
  { id: 39, name: 'Premier League' },
  { id: 140, name: 'La Liga' },
  { id: 135, name: 'Serie A' },
  { id: 78, name: 'Bundesliga' },
  { id: 61, name: 'Ligue 1' },
  { id: 2, name: 'Champions League' },
  { id: 3, name: 'Europa League' },
  { id: 253, name: 'MLS' },
];

export async function contactSupport(name: string, email: string, message: string): Promise<{ success: boolean; error?: string }> {
  return apiCall('/api/support/contact', {
    method: 'POST',
    body: JSON.stringify({ name, email, message }),
  });
}
