import { Platform } from 'react-native';

const getApiBase = (): string => {
  // On web: use relative URLs — proxy.js handles /api/* → localhost:8000 in both
  // dev (picard.replit.dev) and production VM (reversepicks.com). Both environments
  // connect to the same Atlas MongoDB so data is identical.
  if (Platform.OS === 'web' && typeof window !== 'undefined') {
    return '';
  }
  // For native app builds: use EXPO_PUBLIC_API_URL or localhost fallback
  const env = process.env.EXPO_PUBLIC_API_URL;
  if (env) return env;
  return 'http://localhost:8000';
};

// Endpoints that involve AI synthesis — give them a generous timeout
const LONG_TIMEOUT_PATHS   = ['/api/predict', '/api/mlb/predict', '/api/wta/predict', '/api/scan-prop', '/api/chat/message'];
const MEDIUM_TIMEOUT_PATHS = ['/api/players/search', '/api/players/', '/api/match-script'];  // search can hit API-Football strategy fallbacks; match-script hits an AI press-intensity call
const CS2_PREDICT_PATH     = '/api/cs2/predict';
const LONG_TIMEOUT_MS      = 90_000;   // 90 s — soccer / MLB / scan
const MEDIUM_TIMEOUT_MS    = 40_000;   // 40 s — player search (may fall through to API-Football strategies)
const CS2_TIMEOUT_MS       = 150_000;  // 150 s — CS2 first-call cold cache hits 20+ BDL endpoints
const SHORT_TIMEOUT_MS     = 15_000;   // 15 s — all other API calls

export async function apiCall<T = unknown>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const base = getApiBase();
  const url = `${base}${endpoint}`;
  const isCs2Predict = endpoint.startsWith(CS2_PREDICT_PATH);
  const isLong   = LONG_TIMEOUT_PATHS.some(p => endpoint.startsWith(p));
  const isMedium = MEDIUM_TIMEOUT_PATHS.some(p => endpoint.startsWith(p));
  const timeoutMs = isCs2Predict ? CS2_TIMEOUT_MS : isLong ? LONG_TIMEOUT_MS : isMedium ? MEDIUM_TIMEOUT_MS : SHORT_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  // Wire caller-supplied external signal into the internal controller so a user cancel also aborts the fetch
  const externalSignal = options.signal;
  const onExternalAbort = () => controller.abort();
  externalSignal?.addEventListener('abort', onExternalAbort);
  let resp: Response;
  try {
    resp = await fetch(url, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
      signal: controller.signal,
    });
  } catch (e: unknown) {
    if (e instanceof Error && e.name === 'AbortError') {
      // If the caller cancelled via their own signal, throw a distinct error so the UI can silently bail
      if (externalSignal?.aborted) {
        throw new Error('__CANCELLED__');
      }
      throw new Error('Request timed out. The server is taking too long — please try again.');
    }
    throw new Error('Cannot reach server. Please try again.');
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener('abort', onExternalAbort);
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
    } else if (resp.status === 401) {
      message = 'Your session expired. Please sign in again.';
    } else if (resp.status === 403) {
      message = 'Predictions require an active subscription. Tap Account to manage your plan.';
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
  owner_pin_required?: boolean;
}

export async function verifyAccess(email: string, pin?: string): Promise<AccessCheckResponse> {
  // Always send `pin` (even as "" when not yet supplied).
  // Old App Store binary has the previous compiled bundle (no pin field) → backend sees pin=None → auto-login.
  // New builds and web always send pin="" or pin="code" → backend gate fires → access code screen.
  return apiCall<AccessCheckResponse>('/api/auth/verify-access', {
    method: 'POST',
    body: JSON.stringify({ email, pin: pin ?? '' }),
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

export async function deleteAccount(email: string, sessionToken: string): Promise<{ ok: boolean; message: string }> {
  return apiCall('/api/auth/delete-account', {
    method: 'POST',
    body: JSON.stringify({ email, session_token: sessionToken }),
  });
}

export async function iapSignup(
  email: string,
  productId: string,
  expiresAtMs?: number,
): Promise<AuthResponse & { has_access?: boolean; message?: string }> {
  return apiCall('/api/auth/iap-signup', {
    method: 'POST',
    body: JSON.stringify({ email, product_id: productId, expires_at_ms: expiresAtMs ?? null }),
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

export async function requestCode(email: string): Promise<{ sent: boolean; message: string }> {
  return apiCall('/api/auth/send-code', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

export async function verifyCode(email: string, code: string): Promise<AuthResponse & { has_access?: boolean; message?: string }> {
  return apiCall('/api/auth/verify-code', {
    method: 'POST',
    body: JSON.stringify({ email, code }),
  });
}

export async function createCheckout(email: string, planKey: string): Promise<{ checkoutUrl?: string; checkout_url?: string; redirect_url?: string; error?: string }> {
  const redirectUrl = typeof window !== 'undefined' && window.location != null
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
  leagueName?: string;
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
      opponentName: ext.opponentName || (opp as any).teamName || opp.name,
      playerTeam: ext.playerTeam,
      teamName: (res as any).teamName || ext.playerTeam,
      leagueId: ext.leagueId,
      playerId: res.id || (res as any).playerId,
      teamId: (res as any).teamId,
      opponentId: (opp as any).teamId || opp.id,
    };
  }
  return { error: 'No prop data detected. Try a clearer image.' };
}

export interface GameLog {
  date: string;
  opponent?: string | null;
  venue: string;
  value: number | null;
  minutes: number;
  score?: string;
  oppRank?: number | null;
  oppTier?: string | null;
  quality?: boolean;
  teamPossession?: number | null;
  opponentPossession?: number | null;
  blocks?: number | null;
  interceptions?: number | null;
  tackles?: number | null;
  clearances?: number | null;
  synthetic?: boolean;
  // MLB-specific fields
  sport?: string;
  gameNumber?: number | null;
  ip?: number | null;
  pitchCount?: number | null;
  pHits?: number | null;
  era?: number | null;
  hits?: number | null;
  atBats?: number | null;
  hr?: number | null;
  rbi?: number | null;
  avg?: number | null;
  // MLB enrichment fields (from team schedule positional match)
  gameDate?: string | null;
  isHome?: boolean | null;
  homeScore?: number | null;
  awayScore?: number | null;
  won?: boolean | null;
  propType?: string;
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
  confidenceScore?: number;
  bayesianMetrics?: Record<string, unknown>;
  reasoning?: string;
  tacticalBreakdown?: string;
  blendNote?: string;
  aiProjection?: number;
  bayesianComponent?: number;
  bayesianProjection?: number;
  edgeScore?: number;
  fixtureDate?: string;
  opponentName?: string;
  opponent?: string;
  confidenceLevel?: string;
  confidenceInterval?: [number, number];
  priorMean?: number;
  priorWeight?: number;
  momentumWeight?: number;
  covariateWeight?: number;
  lineDeviationBand?: string;
  lineDeviationPct?: number;
  lineDeviationHitRate?: number;
  sport?: string;
  tacticalAlerts?: string[];
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
    dominant?: string;
    color?: string;
    dominant_probability?: number;
    expected_total_goals?: number;
    implied_home?: number;
    implied_away?: number;
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
  keyFactors?: string[];
  qualitySignal?: string;
  currentOppTier?: string;
  currentOppRank?: number;
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
  edgeRating?: 'SHARP EDGE' | 'EDGE' | 'MARGINAL' | 'NO EDGE';
  safetyRating?: 'SAFE' | 'MODERATE' | 'RISKY' | 'AVOID';
  propHistoricalRate?: number;
  propHistoricalN?: number;
  coinFlip?: boolean;
  prizePicksContext?: string;
  scenarioProbabilities?: { best: number; base: number; worst: number };
  /** True when AI text generation is still running in the background; frontend should poll */
  aiPending?: boolean;
  /** Populated when the player was resolved by name and multiple cache entries share the same
   *  abbreviated name (e.g. "J. Valencia" for three different players). The frontend should
   *  show a disambiguation banner so the user can verify the correct player was selected. */
  playerCandidates?: Array<{ playerId: number; playerName: string; teamName: string; position: string; leagueId?: number }>;
  error?: string;
  isWorldCup?: boolean;
  riskSignals?: {
    yellowCardAvg?: number;
    redCardRisk?: 'low' | 'elevated' | 'high';
    opponentYellowCardAvg?: number;
    note?: string;
  };
  congestion?: {
    teamRestDays?: number;
    opponentRestDays?: number;
    teamGamesIn14d?: number;
    opponentGamesIn14d?: number;
    fatigueFlag?: 'low' | 'moderate' | 'high';
  };
  lineup?: {
    status?: 'confirmed' | 'predicted';
    home: {
      teamName?: string;
      formation?: string;
      coach?: string;
      players: Array<{ name: string; x: number; y: number; number?: number; position?: string }>;
    };
    away: {
      teamName?: string;
      formation?: string;
      coach?: string;
      players: Array<{ name: string; x: number; y: number; number?: number; position?: string }>;
    };
  };
}

interface RawPrediction {
  sport?: string;
  player?: { id?: number; name?: string; team?: string; position?: string; role?: string };
  propType?: string;
  line?: number;
  projectedValue?: number;
  recommendation?: string;
  confidenceScore?: number;
  confidenceLevel?: string;
  confidenceInterval?: [number, number];
  playerCandidates?: Array<{ playerId: number; playerName: string; teamName: string; position: string; leagueId?: number }>;
  reasoning?: string;
  tacticalBreakdown?: string;
  sharpSummary?: string;
  tacticalAlerts?: string[];
  isWorldCup?: boolean;
  riskSignals?: PredictionResult['riskSignals'];
  congestion?: PredictionResult['congestion'];
  lineup?: PredictionResult['lineup'];
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
    priorWeight?: number;
    momentumWeight?: number;
    covariateWeight?: number;
    streakFlag?: string;
    pOver?: number;
    pUnder?: number;
    reversalFlag?: string;
    volatility?: string;
    priorSamples?: number;
    covariateAdjustment?: number;
    cv?: number;
    opponentH2HAvg?: number;
    opponentH2HSamples?: number;
    opponentH2HWeight?: number;
    h2hLineHitRate?: number;
    h2hLineSampleN?: number;
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
      matchScore?: string;
      venue?: string;
      minutes?: number;
      minutesPlayed?: number;
      targetStat?: number | null;
      opponent?: string;
      teamPossession?: number | null;
      opponentPossession?: number | null;
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
    playerIsHome?: boolean;
    moneyline?: { home: number | string; draw?: number | string; away: number | string };
    favorite?: string;
    expectedGameType?: string;
    keyMatchupFactor?: string;
    surface?: string;
  };
  positionComparison?: Record<string, unknown>;
  keyEvidence?: string;
  gameFlowDynamics?: string;
  scenarioAnalysis?: string;
  scenarioProbabilities?: { best: number; base: number; worst: number };
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
  edgeRating?: string;
  safetyRating?: string;
  coinFlip?: boolean;
  rawConfidence?: number;
  lineDeviationBand?: string;
  lineDeviationPct?: number;
  lineDeviationHitRate?: number;
  gameScript?: Record<string, unknown>;
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

export async function predict(request: Record<string, unknown>, signal?: AbortSignal): Promise<PredictionResult> {
  const raw = await apiCall<RawPrediction>('/api/predict', {
    method: 'POST',
    body: JSON.stringify(request),
    signal,
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
            oppTier: (g.oppTier as string | null) ?? undefined,
            quality: (g.quality as boolean) ?? undefined,
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
    reasoning: raw.reasoning || undefined,
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
    priorWeight: bm.priorWeight,
    momentumWeight: bm.momentumWeight,
    covariateWeight: bm.covariateWeight,
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
    moneyline: raw.matchupOverview?.moneyline
      ? {
          home: String(raw.matchupOverview.moneyline.home),
          draw: String(raw.matchupOverview.moneyline.draw || ''),
          away: String(raw.matchupOverview.moneyline.away),
        }
      : undefined,
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
    sport: raw.sport || (request.sport as string) || undefined,
    tacticalAlerts: raw.tacticalAlerts || undefined,
    isWorldCup: (raw as any).isWorldCup || undefined,
    riskSignals: (raw as any).riskSignals ?? undefined,
    congestion: (raw as any).congestion ?? undefined,
    lineup: (raw as any).lineup ?? undefined,
    sharpSummary: raw.sharpSummary || undefined,
    keyEvidence: raw.keyEvidence || undefined,
    gameFlowDynamics: raw.gameFlowDynamics || undefined,
    scenarioAnalysis: raw.scenarioAnalysis || undefined,
    scenarioProbabilities: raw.scenarioProbabilities ?? undefined,
    matchContext: raw.matchContext ? { league: raw.matchContext.league, round: raw.matchContext.round, date: raw.matchContext.date } : undefined,
    gameSituation: (raw.gameSituation as any) ?? undefined,
    gameScript: raw.gameScript ?? undefined,
    lineDeviationBand: raw.lineDeviationBand ?? undefined,
    lineDeviationPct: raw.lineDeviationPct ?? undefined,
    lineDeviationHitRate: raw.lineDeviationHitRate ?? undefined,
    dataQuality: raw.dataQuality ? { level: raw.dataQuality.level, message: raw.dataQuality.message, gamesWithData: raw.dataQuality.gamesWithData, totalGames: raw.dataQuality.totalGames } : undefined,
    analysisSummary: raw.analysisSummary ?? undefined,
    tacticalBreakdown: raw.tacticalBreakdown || undefined,
    keyFactors: Array.isArray((raw as any).keyFactors) ? ((raw as any).keyFactors as string[]) : undefined,
    qualitySignal: (raw as any).qualitySignal || undefined,
    currentOppTier: (raw as any).currentOppTier || undefined,
    currentOppRank: (raw as any).currentOppRank ?? undefined,
    blendNote: raw.blendNote || undefined,
    aiProjection: raw.aiProjection || undefined,
    bayesianComponent: raw.bayesianComponent || undefined,
    edgeRating: raw.edgeRating as PredictionResult['edgeRating'] ?? undefined,
    safetyRating: raw.safetyRating as PredictionResult['safetyRating'] ?? undefined,
    propHistoricalRate: (raw as any).propHistoricalRate ?? undefined,
    propHistoricalN: (raw as any).propHistoricalN ?? undefined,
    coinFlip: raw.coinFlip ?? undefined,
    playerCandidates: raw.playerCandidates ?? undefined,
    prizePicksContext: (raw as any).prizePicksContext ?? undefined,
    aiPending: (raw as any).aiPending ?? undefined,
  };
}

/**
 * F5: Poll for AI narrative completion after receiving a math-only prediction.
 * Returns the AI result (tacticalBreakdown, sharpSummary, etc.) when ready.
 */
export async function pollAiNarrative(
  request: Record<string, unknown>
): Promise<{ ready: boolean; failed: boolean; data: Record<string, unknown> | null }> {
  const raw = await apiCall<{
    ready: boolean;
    failed: boolean;
    data: Record<string, unknown> | null;
  }>('/api/predict/ai-poll', {
    method: 'POST',
    body: JSON.stringify(request),
  });
  return {
    ready: raw.ready ?? false,
    failed: raw.failed ?? false,
    data: raw.data ?? null,
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
  paceMismatch?: boolean | null;
  paceWarning?: string | null;
  elapsed?: number | null;
  period?: string;
  matchStatus?: string;
  fixtureId?: number | null;
  createdAt?: string;
  settledAt?: string;
  sport?: string;
  venue?: string;
  trackingId?: string;
  position?: string;
  role?: string;
  leagueId?: number;
  leagueName?: string;
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
  oppAvgPoss?: number | null;
  // AI analysis fields (persisted for offline analysis modal)
  sharpSummary?: string;
  reasoning?: string;
  tacticalBreakdown?: string;
  tacticalAlerts?: string[];
  gameScript?: Record<string, unknown>;
  bayesianMetrics?: Record<string, unknown>;
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
    paceMismatch: (p.paceMismatch as boolean) ?? null,
    paceWarning: (p.paceWarning as string) ?? null,
    elapsed: (p.elapsed as number) ?? null,
    period: p.period as string,
    matchStatus: p.matchStatus as string,
    fixtureId: (p.fixtureId as number) ?? null,
    leagueId: (p.leagueId as number) ?? undefined,
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
    oppAvgPoss: (p.oppAvgPoss as number) ?? null,
    // AI analysis fields persisted on pick
    sharpSummary: (p.sharpSummary as string) || undefined,
    reasoning: (p.reasoning as string) || undefined,
    tacticalBreakdown: (p.tacticalBreakdown as string) || undefined,
    tacticalAlerts: (p.tacticalAlerts as string[]) || undefined,
    gameScript: (p.gameScript as Record<string, unknown>) || undefined,
    bayesianMetrics: (p.bayesianMetrics as Record<string, unknown>) || undefined,
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

export async function generateMatchReview(email: string, token: string, pickId: string): Promise<string | null> {
  try {
    const res = await apiCall<{ ok: boolean; matchReview?: string }>(
      `/api/picks/${encodeURIComponent(pickId)}/review`,
      { method: 'POST', headers: { 'x-user-email': email, 'x-user-token': token, 'Content-Type': 'application/json' }, body: JSON.stringify({}) }
    );
    return res?.matchReview ?? null;
  } catch {
    return null;
  }
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

export interface PlayerContext {
  teamId: number;
  teamName: string;
  leagueId: number;
  isNational: boolean;
}

export interface NextMatchData {
  found: boolean;
  isHome?: boolean;
  opponent?: { id: number; name: string };
  leagueId?: number;
  leagueName?: string;
  date?: string;
  fixtureId?: number;
}

export async function getPlayerContexts(playerId: number): Promise<{ contexts: PlayerContext[] }> {
  return apiCall(`/api/players/${playerId}/contexts`);
}

export interface PlayerRoleResult {
  position: string;
  role: string;
  source?: string;
  cached?: boolean;
}

export async function resolvePlayerRole(
  playerId: number | null,
  playerName: string,
  teamName?: string,
  genericPosition?: string,
): Promise<PlayerRoleResult> {
  try {
    return await apiCall<PlayerRoleResult>('/api/players/resolve-role', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        playerId: playerId || null,
        playerName,
        teamName: teamName || '',
        genericPosition: genericPosition || '',
      }),
    });
  } catch {
    return { position: '', role: '', source: 'error', cached: false };
  }
}

export async function getTeamNextMatch(teamId: number): Promise<NextMatchData> {
  return apiCall(`/api/teams/${teamId}/next-match`);
}

export async function getLeagueById(id: number): Promise<{ id: number; name: string; country: string }> {
  try {
    return await apiCall(`/api/leagues/by-id/${id}`);
  } catch {
    return { id, name: '', country: '' };
  }
}

export interface MatchScriptData {
  available: boolean;
  noCleanScript?: boolean;
  primaryScript?: string;
  isFavorable?: boolean;
  moneyline?: number;
  expectedPossession?: number;
  isFavoriteTeam?: boolean;
  explanation?: string;
  tacticalModifier?: string;
  expectedEffects?: string[];
  reason?: string;
}

export async function getMatchScript(params: {
  teamId: number;
  opponentId: number;
  leagueId: number;
  isHome: boolean;
  teamName: string;
  opponentName: string;
  leagueName?: string;
}): Promise<MatchScriptData> {
  const qs = new URLSearchParams({
    teamId: String(params.teamId),
    opponentId: String(params.opponentId),
    leagueId: String(params.leagueId),
    isHome: String(params.isHome),
    teamName: params.teamName,
    opponentName: params.opponentName,
    leagueName: params.leagueName || '',
  });
  try {
    return await apiCall(`/api/match-script?${qs.toString()}`);
  } catch {
    return {
      available: false,
      noCleanScript: true,
      primaryScript: undefined,
      isFavorable: false,
      explanation: 'Could not reach the server to load the match script. Pull to refresh or try again shortly.',
      tacticalModifier: undefined,
      expectedEffects: [],
      reason: 'request_failed',
    };
  }
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

export async function getSubscriptionStatus(email: string, _accessType?: string): Promise<SubscriptionStatus> {
  return apiCall<SubscriptionStatus>(`/api/stripe/status/${encodeURIComponent(email)}`);
}

export async function cancelSubscription(email: string, _accessType?: string): Promise<{ success: boolean; status?: string; message?: string }> {
  return apiCall('/api/stripe/cancel', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

export async function changePlan(email: string, newPlanKey: string, _accessType?: string): Promise<{ success?: boolean; previous_plan?: string; new_plan?: string; new_label?: string; message?: string }> {
  return apiCall('/api/stripe/change-plan', {
    method: 'POST',
    body: JSON.stringify({ email, new_plan_key: newPlanKey }),
  });
}

export async function resubscribeCheckout(email: string, planKey: string, _accessType?: string): Promise<{ checkoutUrl?: string; checkout_url?: string; redirect_url?: string; error?: string }> {
  const redirectUrl = typeof window !== 'undefined' && window.location != null
    ? `${window.location.origin}/auth`
    : 'https://reversepicks.com/auth';
  return apiCall('/api/stripe/resubscribe-checkout', {
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

export interface ConfidenceTier {
  label: string;
  hits: number;
  misses: number;
  total: number;
  winPct: number;
  roi: number;
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
  brierScore: number | null;
  brierN: number;
  confidenceTiers: ConfidenceTier[];
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
  { key: 'weekly',  name: 'Weekly',  price: '$9.99/week'  },
  { key: 'monthly', name: 'Monthly', price: '$59.99/month' },
] as const;

export const PROP_TYPES = [
  { value: 'pass_attempts',          label: 'Pass Attempts' },
  { value: 'shots',                  label: 'Shots' },
  { value: 'shots_on_target',        label: 'Shots on Target' },
  { value: 'shots_assisted',         label: 'Shot Assists' },
  { value: 'goals',                  label: 'Goals' },
  { value: 'assists',                label: 'Assists' },
  { value: 'key_passes',             label: 'Key Passes' },
  { value: 'fouls_drawn',            label: 'Fouls Drawn' },
  { value: 'fouls_committed',        label: 'Fouls Committed' },
  { value: 'clearances',             label: 'Clearances' },
  { value: 'interceptions',          label: 'Interceptions' },
  { value: 'tackles',                label: 'Tackles' },
  { value: 'saves',                  label: 'Saves' },
  { value: 'dribbles',               label: 'Dribbles' },
  { value: 'crosses',                label: 'Crosses' },
];

export const CS2_PROP_TYPES = [
  // Maps 1-2 (full match, most common on PrizePicks)
  { value: 'maps_1_2_kills',      label: 'Maps 1-2 Kills' },
  { value: 'maps_1_2_deaths',     label: 'Maps 1-2 Deaths' },
  { value: 'maps_1_2_assists',    label: 'Maps 1-2 Assists' },
  { value: 'maps_1_2_adr',        label: 'Maps 1-2 ADR' },
  { value: 'maps_1_2_headshots',  label: 'Maps 1-2 Headshots' },
  // Map 1 only
  { value: 'map1_kills',          label: 'Map 1 Kills' },
  // Maps 1-3 (all 3 maps combined — when series plays out to map 3)
  { value: 'maps_1_3_kills',      label: 'Maps 1-3 Kills' },
  { value: 'maps_1_3_headshots',  label: 'Maps 1-3 Headshots' },
  // Map 3 props (when series goes to a deciding map)
  { value: 'map3_kills',          label: 'Map 3 Kills' },
  { value: 'map3_headshots',      label: 'Map 3 Headshots' },
  { value: 'map3_deaths',         label: 'Map 3 Deaths' },
  { value: 'map3_assists',        label: 'Map 3 Assists' },
  { value: 'map3_adr',            label: 'Map 3 ADR' },
  // Per-map props
  { value: 'kills',               label: 'Kills (per map)' },
  { value: 'deaths',              label: 'Deaths (per map)' },
  { value: 'assists',             label: 'Assists (per map)' },
  { value: 'adr',                 label: 'ADR (per map)' },
  { value: 'headshots',           label: 'Headshots (per map)' },
  { value: 'headshot_pct',        label: 'Headshot %' },
  { value: 'first_kills',         label: 'First Kills' },
  { value: 'clutches_won',        label: 'Clutches Won' },
  { value: 'rating',              label: 'Rating' },
];

export interface Cs2Player {
  id: number;
  nickname: string;
  fullName: string;
  team: { id: number; name: string; short_name?: string | null } | null;
  isActive: boolean | null;
  age?: number | null;
}

export async function searchCs2Players(query: string): Promise<Cs2Player[]> {
  if (!query || query.length < 2) return [];
  return apiCall<Cs2Player[]>(`/api/cs2/players/search?q=${encodeURIComponent(query)}`);
}

export interface Cs2Team {
  id: number;
  name: string;
  shortName?: string | null;
}

export async function searchCs2Teams(query: string): Promise<Cs2Team[]> {
  if (!query || query.length < 2) return [];
  return apiCall<Cs2Team[]>(`/api/cs2/teams/search?q=${encodeURIComponent(query)}`);
}

export interface Cs2NextMatch {
  found: boolean;
  matchId?: number | null;
  opponent?: { id: number | null; name: string; rank?: number | null } | null;
  tournament?: string;
  tier?: string;
  date?: string;
}

export async function getCs2NextMatch(playerId?: number | null, teamId?: number | null): Promise<Cs2NextMatch> {
  try {
    const params = new URLSearchParams();
    if (playerId) params.set('playerId', String(playerId));
    if (teamId)   params.set('teamId',   String(teamId));
    return await apiCall<Cs2NextMatch>(`/api/cs2/next-match?${params}`);
  } catch {
    return { found: false };
  }
}

// ─── WTA Tennis ─────────────────────────────────────────────────────────────

export const WTA_PROP_TYPES = [
  { value: 'total_games',        label: 'Total Games (Match)' },
  { value: 'player_games_won',   label: 'Player Games Won' },
  { value: 'opponent_games_won', label: 'Opponent Games Won' },
  { value: 'total_sets',         label: 'Total Sets' },
  { value: 'player_sets_won',    label: 'Player Sets Won' },
  { value: 'set_1_total_games',  label: 'Set 1 Total Games' },
  { value: 'set_1_player_games', label: 'Set 1 Player Games' },
  { value: 'match_winner',       label: 'Match Winner' },
  { value: 'first_set_winner',   label: 'First Set Winner' },
];

export const WTA_SURFACES = ['Hard', 'Clay', 'Grass'];
export const WTA_ROUNDS   = ['F', 'SF', 'QF', 'R16', 'R32', 'R64', 'R128', 'Qualifying'];

export interface WtaPlayer {
  id:           number;
  firstName:    string;
  lastName:     string;
  fullName:     string;
  country?:     string | null;
  currentRank?: number | null;
  isActive?:    boolean;
}

export async function searchWtaPlayers(query: string): Promise<WtaPlayer[]> {
  if (!query || query.length < 2) return [];
  return apiCall<WtaPlayer[]>(`/api/wta/players/search?q=${encodeURIComponent(query)}`);
}

export interface WtaNextMatch {
  found: boolean;
  matchId?: number | null;
  opponent?: { id: number | null; name: string; rank?: number | null } | null;
  surface?: string;
  round?: string;
  tournament?: string;
  tournamentId?: number | null;
  date?: string;
}

export async function getWtaNextMatch(playerId: number): Promise<WtaNextMatch> {
  try {
    return await apiCall<WtaNextMatch>(`/api/wta/next-match?playerId=${playerId}`);
  } catch {
    return { found: false };
  }
}

export async function wtaPredict(request: Record<string, unknown>, signal?: AbortSignal): Promise<PredictionResult> {
  const raw = await apiCall<any>('/api/wta/predict', {
    method: 'POST',
    body: JSON.stringify(request),
    signal,
  });
  if (raw.error) return { error: raw.error } as PredictionResult;
  const bm  = raw.bayesianMetrics || {};
  const rec = (raw.recommendation || '').toUpperCase() as 'OVER' | 'UNDER' | 'PASS';

  const gameLogs = (raw.matchLogs || raw.gameLogs || []).map((g: any) => ({
    date:     g.date ?? '',
    opponent: g.opponent ?? g.opponentName ?? '',
    venue:    g.venue ?? 'neutral',
    value:    g.value ?? g.totalGames ?? g.playerGamesWon ?? null,
    score:    g.score ?? g.matchScore ?? undefined,
    minutes:  0,
    sport:    'wta',
    surface:  g.surface ?? '',
    round:    g.round ?? '',
    tournament: g.tournament ?? '',
    setScores: g.setScores ?? [],
    playerGamesWon:   g.playerGamesWon,
    opponentGamesWon: g.opponentGamesWon,
    totalGames:       g.totalGames,
    setsPlayed:       g.setsPlayed,
    wonMatch:         g.wonMatch,
  }));

  return {
    sport:               'wta',
    playerName:          raw.playerName,
    playerId:            raw.playerId,
    teamName:            raw.opponentName ? '' : '',
    opponentName:        raw.opponentName,
    opponentId:          raw.opponentId,
    propType:            raw.propType,
    propLabel:           raw.propLabel,
    line:                raw.line,
    projection:          raw.projection,
    bayesianProjection:  raw.projection,
    confidence:          raw.confidenceScore != null ? raw.confidenceScore / 100 : null,
    confidenceScore:     raw.confidenceScore,
    confidenceLevel:     raw.confidenceLevel,
    recommendation:      rec,
    pOver:               raw.pOver,
    pUnder:              raw.pUnder,
    sharpSummary:        raw.sharpSummary      || undefined,
    reasoning:           raw.reasoning         || undefined,
    tacticalBreakdown:   raw.tacticalBreakdown || undefined,
    surface:             raw.surface,
    round:               raw.round,
    tournament:          raw.tournament,
    subjectRank:         raw.subjectRank,
    opponentRank:        raw.opponentRank,
    h2h:                 raw.h2h,
    gameLogs,
    matchupOverview: raw.matchupOverview ? {
      homeTeam:         raw.matchupOverview.homeTeam,
      awayTeam:         raw.matchupOverview.awayTeam,
      playerIsHome:     raw.matchupOverview.playerIsHome,
      surface:          raw.matchupOverview.surface,
      expectedGameType: raw.matchupOverview.expectedGameType,
      keyMatchupFactor: raw.matchupOverview.keyMatchupFactor,
    } : undefined,
    bayesianMetrics: {
      priorMean:       bm.priorMean,
      momentumMean:    bm.momentumMean,
      sampleSize:      bm.sampleSize,
      tacticalMetrics: bm.tacticalMetrics,
    },
  } as unknown as PredictionResult;
}

export async function cs2Predict(request: Record<string, unknown>, signal?: AbortSignal): Promise<PredictionResult> {
  const raw = await apiCall<any>('/api/cs2/predict', {
    method: 'POST',
    body: JSON.stringify(request),
    signal,
  });
  if (raw.error) return { error: raw.error } as PredictionResult;
  const bm  = raw.bayesianMetrics || {};
  const rec = (raw.recommendation || '').toUpperCase() as 'OVER' | 'UNDER' | 'PASS';

  const gameLogs = (raw.gameLogs || []).map((g: any) => ({
    date:           g.date ?? '',
    opponent:       g.opponent ?? '',
    venue:          g.wonMap === true ? 'home' : g.wonMap === false ? 'away' : '',
    value:          g[raw.propType] ?? null,
    minutes:        0,
    sport:          'cs2',
    mapName:        g.mapName ?? '',
    mapNumber:      g.mapNumber ?? null,
    kills:          g.kills ?? null,
    deaths:         g.deaths ?? null,
    assists:        g.assists ?? null,
    adr:            g.adr ?? null,
    kast:           g.kast ?? null,
    rating:         g.rating ?? null,
    headshotPct:        g.headshotPct ?? null,
    headshotCount:      g.headshotCount ?? null,
    firstKills:         g.firstKills ?? null,
    clutchesWon:        g.clutchesWon ?? null,
    wonMap:             g.wonMap ?? null,
    tournament:         g.tournament ?? '',
    tier:               g.tier ?? '',
    maps_1_2_headshots: g.maps_1_2_headshots ?? null,
    map3_kills:         g.map3_kills ?? null,
    map3_headshots:     g.map3_headshots ?? null,
    map3_deaths:        g.map3_deaths ?? null,
    map3_assists:       g.map3_assists ?? null,
    map3_adr:           g.map3_adr ?? null,
    map3_played:        g.map3_played ?? false,
  })).filter((g: any) => g.value != null);

  return {
    playerName:         raw.playerName || '',
    teamName:           raw.teamName || '',
    opponentName:       raw.opponentName || '',
    propType:           raw.propType || '',
    line:               raw.line ?? 0,
    projection:         raw.projection,
    confidence:         raw.confidenceScore,
    rawConfidence:      raw.confidenceScore,
    recommendation:     rec,
    confidenceLevel:    raw.confidenceLevel,
    pOver:              raw.pOver ?? bm.pOver,
    pUnder:             raw.pUnder ?? bm.pUnder,
    priorSamples:       raw.sampleSize,
    priorMean:          raw.priorMean ?? bm.priorMean,
    momentumMean:       raw.momentumMean ?? bm.momentumMean,
    streakFlag:         raw.streakFlag ?? '',
    sharpSummary:       raw.sharpSummary || undefined,
    reasoning:          raw.reasoning || undefined,
    tacticalBreakdown:  raw.reasoning || undefined,
    playerId:           raw.playerId,
    teamId:             raw.teamId,
    sampleSize:         raw.sampleSize,
    gameLogs,
    bayesianMetrics:    bm,
    sport:              'cs2',
  } as unknown as PredictionResult;
}

// ─── NBA ────────────────────────────────────────────────────────────────────

export const NBA_PROP_TYPES = [
  { value: 'pts',           label: 'Points' },
  { value: 'reb',           label: 'Rebounds' },
  { value: 'ast',           label: 'Assists' },
  { value: 'stl',           label: 'Steals' },
  { value: 'blk',           label: 'Blocks' },
  { value: 'fg3m',          label: '3-Pointers Made' },
  { value: 'tov',           label: 'Turnovers' },
  { value: 'pts_reb_ast',   label: 'Pts + Reb + Ast' },
  { value: 'pts_reb',       label: 'Pts + Reb' },
  { value: 'pts_ast',       label: 'Pts + Ast' },
  { value: 'reb_ast',       label: 'Reb + Ast' },
  { value: 'stl_blk',       label: 'Stl + Blk' },
  { value: 'fantasy_pts',   label: 'Fantasy Points' },
];

export interface NbaPlayer {
  id:         number;
  firstName:  string;
  lastName:   string;
  fullName?:  string;
  position:   string;
  team?:      { id: number; full_name: string; abbreviation: string } | null;
}

export async function searchNbaPlayers(query: string): Promise<NbaPlayer[]> {
  if (!query || query.length < 2) return [];
  const raw = await apiCall<any>(`/api/nba/players/search?q=${encodeURIComponent(query)}`);
  const rows: any[] = Array.isArray(raw) ? raw : (raw?.players || raw?.results || []);
  return rows.map((p: any) => ({
    id:        p.id ?? p.player_id ?? 0,
    firstName: p.first_name ?? '',
    lastName:  p.last_name  ?? '',
    fullName:  p.full_name  ?? `${p.first_name ?? ''} ${p.last_name ?? ''}`.trim(),
    position:  p.position   ?? '',
    team:      p.team       ?? null,
  }));
}

export interface NbaNextMatch {
  found:     boolean;
  gameId?:   number | null;
  date?:     string;
  venue?:    'home' | 'away';
  opponent?: { id: number | null; name: string; abbreviation?: string } | null;
}

export async function getNbaNextMatch(playerId: number): Promise<NbaNextMatch> {
  try {
    return await apiCall<NbaNextMatch>(`/api/nba/next-match?player_id=${playerId}`);
  } catch {
    return { found: false };
  }
}

export async function nbaPredict(request: Record<string, unknown>, signal?: AbortSignal): Promise<PredictionResult> {
  const raw = await apiCall<any>('/api/nba/predict', {
    method: 'POST',
    body:   JSON.stringify(request),
    signal,
  });
  if (raw.error) return { error: raw.error } as PredictionResult;
  const bm  = raw.bayesianMetrics || {};
  const rec = (raw.recommendation || '').toUpperCase() as 'OVER' | 'UNDER' | 'PASS';

  const gameLogs = (raw.gameLogs || []).map((g: any) => ({
    date:       g.date     ?? '',
    opponent:   g.opponent ?? '',
    venue:      g.venue    ?? '',
    value:      g[raw.propType] ?? g.value ?? null,
    minutes:    g.min ?? g.minutes ?? 0,
    sport:      'nba',
    pts:        g.pts    ?? null,
    reb:        g.reb    ?? null,
    ast:        g.ast    ?? null,
    stl:        g.stl    ?? null,
    blk:        g.blk    ?? null,
    fg3m:       g.fg3m   ?? null,
    tov:        g.tov    ?? null,
    won:        g.won    ?? null,
  }));

  return {
    sport:              'nba',
    playerName:         raw.playerName   || '',
    playerId:           raw.playerId,
    teamName:           raw.teamName     || '',
    opponentName:       raw.opponentName || '',
    propType:           raw.propType     || '',
    line:               raw.line         ?? 0,
    projection:         raw.projection,
    bayesianProjection: raw.projection,
    confidence:         raw.confidenceScore != null ? raw.confidenceScore / 100 : null,
    rawConfidence:      raw.rawConfidence ?? raw.confidenceScore,
    confidenceScore:    raw.confidenceScore,
    confidenceLevel:    raw.confidenceLevel,
    recommendation:     rec,
    pOver:              raw.pOver  ?? bm.pOver,
    pUnder:             raw.pUnder ?? bm.pUnder,
    sharpSummary:       raw.sharpSummary       || undefined,
    reasoning:          raw.reasoning          || undefined,
    tacticalBreakdown:  raw.tacticalBreakdown  || undefined,
    keyFactors:         raw.keyFactors         ?? [],
    streakFlag:         raw.streakFlag         ?? '',
    priorSamples:       raw.sampleSize         ?? bm.sampleSize,
    priorMean:          raw.priorMean          ?? bm.priorMean,
    momentumMean:       raw.momentum           ?? bm.momentumMean,
    gameLogs,
    bayesianMetrics: {
      priorMean:    bm.priorMean    ?? raw.priorMean,
      momentumMean: bm.momentumMean ?? raw.momentum,
      sampleSize:   bm.sampleSize   ?? raw.sampleSize,
    },
  } as unknown as PredictionResult;
}

// ─── NHL ────────────────────────────────────────────────────────────────────

export const NHL_PROP_TYPES = [
  { value: 'goals',         label: 'Goals' },
  { value: 'assists',       label: 'Assists' },
  { value: 'points',        label: 'Points (G+A)' },
  { value: 'shots',         label: 'Shots on Goal' },
  { value: 'blocked_shots', label: 'Blocked Shots' },
  { value: 'hits',          label: 'Hits' },
  { value: 'saves',         label: 'Saves (Goalie)' },
  { value: 'toi',           label: 'Time on Ice (min)' },
];

export interface NhlPlayer {
  id:         number;
  firstName:  string;
  lastName:   string;
  fullName?:  string;
  position:   string;
  team?:      { id: number; full_name: string; abbreviation: string } | null;
}

export async function searchNhlPlayers(query: string): Promise<NhlPlayer[]> {
  if (!query || query.length < 2) return [];
  const raw = await apiCall<any>(`/api/nhl/players/search?q=${encodeURIComponent(query)}`);
  const rows: any[] = Array.isArray(raw) ? raw : (raw?.players || raw?.results || []);
  return rows.map((p: any) => ({
    id:        p.id         ?? p.player_id ?? 0,
    firstName: p.first_name ?? '',
    lastName:  p.last_name  ?? '',
    fullName:  p.full_name  ?? `${p.first_name ?? ''} ${p.last_name ?? ''}`.trim(),
    position:  p.position   ?? '',
    team:      p.team       ?? null,
  }));
}

export interface NhlNextMatch {
  found:     boolean;
  gameId?:   number | null;
  date?:     string;
  venue?:    'home' | 'away';
  opponent?: { id: number | null; name: string; abbreviation?: string } | null;
}

export async function getNhlNextMatch(playerId: number): Promise<NhlNextMatch> {
  try {
    return await apiCall<NhlNextMatch>(`/api/nhl/next-match?player_id=${playerId}`);
  } catch {
    return { found: false };
  }
}

export async function nhlPredict(request: Record<string, unknown>, signal?: AbortSignal): Promise<PredictionResult> {
  const raw = await apiCall<any>('/api/nhl/predict', {
    method: 'POST',
    body:   JSON.stringify(request),
    signal,
  });
  if (raw.error) return { error: raw.error } as PredictionResult;
  const bm  = raw.bayesianMetrics || {};
  const rec = (raw.recommendation || '').toUpperCase() as 'OVER' | 'UNDER' | 'PASS';

  const gameLogs = (raw.gameLogs || []).map((g: any) => ({
    date:          g.date     ?? '',
    opponent:      g.opponent ?? '',
    venue:         g.venue    ?? '',
    value:         g[raw.propType] ?? g.value ?? null,
    minutes:       g.toi ?? 0,
    sport:         'nhl',
    goals:         g.goals         ?? null,
    assists:       g.assists       ?? null,
    points:        g.points        ?? null,
    shots:         g.shots         ?? null,
    blockedShots:  g.blocked_shots ?? null,
    hits:          g.hits          ?? null,
    saves:         g.saves         ?? null,
    won:           g.won           ?? null,
  }));

  return {
    sport:              'nhl',
    playerName:         raw.playerName   || '',
    playerId:           raw.playerId,
    teamName:           raw.teamName     || '',
    opponentName:       raw.opponentName || '',
    propType:           raw.propType     || '',
    line:               raw.line         ?? 0,
    projection:         raw.projection,
    bayesianProjection: raw.projection,
    confidence:         raw.confidenceScore != null ? raw.confidenceScore / 100 : null,
    rawConfidence:      raw.rawConfidence ?? raw.confidenceScore,
    confidenceScore:    raw.confidenceScore,
    confidenceLevel:    raw.confidenceLevel,
    recommendation:     rec,
    pOver:              raw.pOver  ?? bm.pOver,
    pUnder:             raw.pUnder ?? bm.pUnder,
    sharpSummary:       raw.sharpSummary      || undefined,
    reasoning:          raw.reasoning         || undefined,
    tacticalBreakdown:  raw.tacticalBreakdown || undefined,
    keyFactors:         raw.keyFactors        ?? [],
    streakFlag:         raw.streakFlag        ?? '',
    priorSamples:       raw.sampleSize        ?? bm.sampleSize,
    priorMean:          raw.priorMean         ?? bm.priorMean,
    momentumMean:       raw.momentum          ?? bm.momentumMean,
    gameLogs,
    bayesianMetrics: {
      priorMean:    bm.priorMean    ?? raw.priorMean,
      momentumMean: bm.momentumMean ?? raw.momentum,
      sampleSize:   bm.sampleSize   ?? raw.sampleSize,
    },
  } as unknown as PredictionResult;
}

// ─── NFL ────────────────────────────────────────────────────────────────────

export const NFL_PROP_TYPES = [
  { value: 'passing_yards',         label: 'Passing Yards' },
  { value: 'passing_tds',           label: 'Passing TDs' },
  { value: 'completions',           label: 'Completions' },
  { value: 'pass_attempts',         label: 'Pass Attempts' },
  { value: 'interceptions',         label: 'Interceptions Thrown' },
  { value: 'passing_rushing_yards', label: 'Pass + Rush Yards' },
  { value: 'rushing_yards',         label: 'Rushing Yards' },
  { value: 'rushing_tds',           label: 'Rushing TDs' },
  { value: 'carries',               label: 'Carries' },
  { value: 'receiving_yards',       label: 'Receiving Yards' },
  { value: 'receiving_tds',         label: 'Receiving TDs' },
  { value: 'receptions',            label: 'Receptions' },
  { value: 'targets',               label: 'Targets' },
  { value: 'fantasy_points',        label: 'Fantasy Points' },
  { value: 'anytime_td',            label: 'Anytime TD Scorer' },
];

export interface NflPlayer {
  id:        number;
  firstName: string;
  lastName:  string;
  fullName?: string;
  position:  string;
  team?:     { id: number; full_name: string; abbreviation: string } | null;
  jersey?:   string | null;
  college?:  string | null;
}

export async function searchNflPlayers(query: string): Promise<NflPlayer[]> {
  if (!query || query.length < 2) return [];
  const raw = await apiCall<any>(`/api/nfl/players/search?q=${encodeURIComponent(query)}`);
  const rows: any[] = Array.isArray(raw) ? raw : (raw?.players || raw?.results || []);
  return rows.map((p: any) => ({
    id:        p.id        ?? 0,
    firstName: p.firstName ?? p.first_name  ?? '',
    lastName:  p.lastName  ?? p.last_name   ?? '',
    fullName:  p.fullName  ?? `${p.firstName ?? p.first_name ?? ''} ${p.lastName ?? p.last_name ?? ''}`.trim(),
    position:  p.position  ?? '',
    team:      p.team      ?? null,
    jersey:    p.jersey    ?? null,
    college:   p.college   ?? null,
  }));
}

export interface NflNextMatch {
  found:     boolean;
  gameId?:   number | null;
  date?:     string;
  venue?:    'home' | 'away';
  opponent?: { id: number | null; name: string; abbreviation?: string } | null;
}

export async function getNflNextMatch(playerId: number): Promise<NflNextMatch> {
  try {
    return await apiCall<NflNextMatch>(`/api/nfl/next-match?player_id=${playerId}`);
  } catch {
    return { found: false };
  }
}

export async function nflPredict(request: Record<string, unknown>, signal?: AbortSignal): Promise<PredictionResult> {
  const raw = await apiCall<any>('/api/nfl/predict', {
    method: 'POST',
    body:   JSON.stringify(request),
    signal,
  });
  if (raw.error) return { error: raw.error } as PredictionResult;
  const bm  = raw.bayesianMetrics || {};
  const rec = (raw.recommendation || '').toUpperCase() as 'OVER' | 'UNDER' | 'PASS';
  const gameLogs = (raw.gameLogs || []).map((g: any) => ({
    date:            g.date            ?? '',
    opponent:        g.opponent        ?? '',
    venue:           g.venue           ?? '',
    value:           g.value           ?? null,
    week:            g.week            ?? null,
    sport:           'nfl',
    passing_yards:   g.passing_yards   ?? null,
    rushing_yards:   g.rushing_yards   ?? null,
    receiving_yards: g.receiving_yards ?? null,
    receptions:      g.receptions      ?? null,
  }));
  return {
    sport:              'nfl',
    playerName:         raw.playerName   || '',
    playerId:           raw.playerId,
    teamName:           raw.teamName     || '',
    opponentName:       raw.opponentName || '',
    propType:           raw.propType     || '',
    line:               raw.line         ?? 0,
    projection:         raw.projection,
    bayesianProjection: raw.projection,
    confidence:         raw.confidenceScore != null ? raw.confidenceScore / 100 : null,
    rawConfidence:      raw.rawConfidence ?? raw.confidenceScore,
    confidenceScore:    raw.confidenceScore,
    confidenceLevel:    raw.confidenceLevel,
    recommendation:     rec,
    pOver:              raw.pOver  ?? bm.pOver,
    pUnder:             raw.pUnder ?? bm.pUnder,
    sharpSummary:       raw.sharpSummary      || undefined,
    reasoning:          raw.reasoning         || undefined,
    tacticalBreakdown:  raw.tacticalBreakdown || undefined,
    keyFactors:         raw.keyFactors        ?? [],
    streakFlag:         raw.streakFlag        ?? '',
    priorSamples:       raw.sampleSize        ?? bm.sampleSize,
    priorMean:          raw.priorMean         ?? bm.priorMean,
    momentumMean:       raw.momentum          ?? bm.momentumMean,
    gameLogs,
    bayesianMetrics: {
      priorMean:    bm.priorMean    ?? raw.priorMean,
      momentumMean: bm.momentumMean ?? raw.momentum,
      sampleSize:   bm.sampleSize   ?? raw.sampleSize,
    },
  } as unknown as PredictionResult;
}

// ─── MLB ────────────────────────────────────────────────────────────────────

export const MLB_PROP_TYPES = [
  { value: 'hits',                  label: 'Hits' },
  { value: 'runs',                  label: 'Runs Scored' },
  { value: 'rbi',                   label: 'RBIs' },
  { value: 'home_runs',             label: 'Home Runs' },
  { value: 'total_bases',           label: 'Total Bases' },
  { value: 'strikeouts',            label: 'Strikeouts (Batter)' },
  { value: 'walks',                 label: 'Walks' },
  { value: 'hits_runs_rbis',        label: 'H + R + RBI' },
  { value: 'hitter_fantasy_points', label: 'Fantasy Pts (Hitter)' },
  { value: 'pitcher_strikeouts',    label: 'Strikeouts (Pitcher)' },
  { value: 'pitches_thrown',        label: 'Pitches Thrown' },
  { value: 'innings_pitched',       label: 'Innings Pitched' },
  { value: 'earned_runs',           label: 'Earned Runs' },
  { value: 'hits_allowed',          label: 'Hits Allowed' },
  { value: 'walks_allowed',         label: 'Walks Allowed' },
  { value: 'pitcher_fantasy_score', label: 'Fantasy Pts (Pitcher)' },
];

export interface MlbPlayer {
  id:         number;
  firstName:  string;
  lastName:   string;
  fullName?:  string;
  position:   string;
  team?:      { id: number; full_name: string; abbreviation: string } | null;
}

export async function searchMlbPlayers(query: string): Promise<MlbPlayer[]> {
  if (!query || query.length < 2) return [];
  const raw = await apiCall<any>(`/api/mlb/players/search?q=${encodeURIComponent(query)}`);
  const rows: any[] = Array.isArray(raw) ? raw : (raw?.players || raw?.results || []);
  return rows.map((p: any) => ({
    id:        p.id         ?? p.player_id ?? 0,
    firstName: p.first_name ?? '',
    lastName:  p.last_name  ?? '',
    fullName:  p.full_name  ?? `${p.first_name ?? ''} ${p.last_name ?? ''}`.trim(),
    position:  p.position   ?? '',
    team:      p.team       ?? null,
  }));
}

export interface MlbNextMatch {
  found:     boolean;
  gameId?:   number | null;
  date?:     string;
  venue?:    'home' | 'away';
  opponent?: { id: number | null; name: string; abbreviation?: string } | null;
}

export async function getMlbNextMatch(playerId: number): Promise<MlbNextMatch> {
  try {
    return await apiCall<MlbNextMatch>(`/api/mlb/next-match?player_id=${playerId}`);
  } catch {
    return { found: false };
  }
}

export async function mlbPredict(request: Record<string, unknown>, signal?: AbortSignal): Promise<PredictionResult> {
  const raw = await apiCall<any>('/api/mlb/predict', {
    method: 'POST',
    body:   JSON.stringify(request),
    signal,
  });
  if (raw.error) return { error: raw.error } as PredictionResult;
  const bm  = raw.bayesianMetrics || {};
  const rec = (raw.recommendation || '').toUpperCase() as 'OVER' | 'UNDER' | 'PASS';

  const gameLogs = (raw.gameLogs || []).map((g: any) => ({
    date:          g.date     ?? '',
    opponent:      g.opponent ?? '',
    venue:         g.venue    ?? '',
    score:         g.score    ?? null,
    value:         g[raw.propType] ?? g.value ?? null,
    minutes:       0,
    sport:         'mlb',
    hits:          g.hits      ?? null,
    runs:          g.runs      ?? null,
    rbi:           g.rbi       ?? null,
    homeRuns:      g.home_runs ?? null,
    totalBases:    g.total_bases ?? null,
    won:           g.won       ?? null,
    isHome:        g.isHome    ?? null,
    homeScore:     g.homeScore ?? null,
    awayScore:     g.awayScore ?? null,
  }));

  return {
    sport:              'mlb',
    playerName:         raw.playerName   || '',
    playerId:           raw.playerId,
    teamName:           raw.teamName     || '',
    opponentName:       raw.opponentName || '',
    propType:           raw.propType     || '',
    line:               raw.line         ?? 0,
    projection:         raw.projection,
    bayesianProjection: raw.projection,
    confidence:         raw.confidenceScore != null ? raw.confidenceScore / 100 : null,
    rawConfidence:      raw.rawConfidence ?? raw.confidenceScore,
    confidenceScore:    raw.confidenceScore,
    confidenceLevel:    raw.confidenceLevel,
    recommendation:     rec,
    pOver:              raw.pOver  ?? bm.pOver,
    pUnder:             raw.pUnder ?? bm.pUnder,
    sharpSummary:       raw.sharpSummary      || undefined,
    reasoning:          raw.reasoning         || undefined,
    tacticalBreakdown:  raw.tacticalBreakdown || undefined,
    keyFactors:         raw.keyFactors        ?? [],
    streakFlag:         raw.streakFlag        ?? '',
    priorSamples:       raw.priorSamples      ?? bm.sampleSize,
    priorMean:          raw.priorMean         ?? bm.priorMean,
    momentumMean:       raw.momentum          ?? bm.momentumMean,
    gameLogs,
    matchupOverview: raw.matchupOverview ? {
      homeTeam:         raw.matchupOverview.homeTeam,
      awayTeam:         raw.matchupOverview.awayTeam,
      playerIsHome:     raw.matchupOverview.playerIsHome,
      expectedGameType: raw.matchupOverview.expectedGameType,
      keyMatchupFactor: raw.matchupOverview.keyMatchupFactor,
      moneyline:        raw.matchupOverview.moneyline ?? raw.moneyline,
    } : (raw.moneyline ? { homeTeam: raw.teamName || '', awayTeam: raw.opponentName || '', playerIsHome: true, moneyline: raw.moneyline } : undefined),
    bayesianMetrics: raw.bayesianMetrics ?? {
      priorMean:    bm.priorMean    ?? raw.priorMean,
      momentumMean: bm.momentumMean ?? raw.momentum,
      sampleSize:   bm.sampleSize   ?? raw.sampleSize,
    },
  } as unknown as PredictionResult;
}

// ─── Sports Config ────────────────────────────────────────────────────────────

export interface SportConfig {
  sport:       string;
  displayName: string;
  icon:        string;
  label:       string | null;
  available:   boolean;
}

export async function getSportsConfig(): Promise<SportConfig[]> {
  try {
    return await apiCall<SportConfig[]>('/api/sports/config');
  } catch {
    return [
      { sport: 'soccer', displayName: 'Soccer',     icon: 'football',        label: null, available: true },
      { sport: 'mlb',    displayName: 'MLB',         icon: 'baseball',        label: null, available: true },
      { sport: 'cs2',    displayName: 'CS2',         icon: 'game-controller', label: null, available: true },
      { sport: 'wta',    displayName: 'WTA Tennis',  icon: 'tennisball',      label: null, available: false },
      { sport: 'nba',    displayName: 'NBA',         icon: 'basketball',      label: 'Off Season', available: false },
      { sport: 'nhl',    displayName: 'NHL',         icon: 'snow',            label: 'Off Season', available: false },
    ];
  }
}

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

// ─── Community Chat ────────────────────────────────────────────────────────────

export interface CommunityMessage {
  id: string;
  senderId: string;
  name: string;
  text: string;
  imageData?: string | null;
  mentions: string[];
  reactions: Record<string, string[]>;
  createdAt: string;
  pending?: boolean;
}

export async function fetchCommunityMessages(params?: {
  since?: string;
  before?: string;
  limit?: number;
}): Promise<CommunityMessage[]> {
  const qs = new URLSearchParams();
  if (params?.since) qs.set('since', params.since);
  if (params?.before) qs.set('before', params.before);
  if (params?.limit) qs.set('limit', String(params.limit));
  const q = qs.toString();
  return apiCall(`/api/community/messages${q ? `?${q}` : ''}`);
}

export async function sendCommunityMessage(payload: {
  email: string;
  text: string;
  imageData?: string | null;
  mentions?: string[];
}): Promise<CommunityMessage> {
  return apiCall('/api/community/messages', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function reactToCommunityMessage(
  messageId: string,
  email: string,
  emoji: string,
): Promise<{ reactions: Record<string, string[]> }> {
  return apiCall(`/api/community/messages/${messageId}/react`, {
    method: 'POST',
    body: JSON.stringify({ email, emoji }),
  });
}

export async function deleteCommunityMessage(messageId: string, email: string): Promise<void> {
  return apiCall(
    `/api/community/messages/${messageId}?email=${encodeURIComponent(email)}`,
    { method: 'DELETE' },
  );
}

export async function fetchCommunityParticipants(): Promise<
  Array<{ id: string; name: string }>
> {
  return apiCall('/api/community/participants');
}

// ─── In-App Notifications ──────────────────────────────────────────────────────

export interface AppNotification {
  notificationId: string;
  email: string;
  type: 'pick_settled' | 'mention' | string;
  title: string;
  body: string;
  data: Record<string, unknown>;
  read: boolean;
  createdAt: string;
}

export async function getNotifications(email: string, limit = 40): Promise<AppNotification[]> {
  return apiCall<AppNotification[]>(
    `/api/notifications?email=${encodeURIComponent(email)}&limit=${limit}`,
  );
}

export async function getNotificationsUnreadCount(email: string): Promise<{ count: number }> {
  return apiCall<{ count: number }>(
    `/api/notifications/unread-count?email=${encodeURIComponent(email)}`,
  );
}

export async function markNotificationsRead(email: string, notificationIds?: string[]): Promise<void> {
  await apiCall('/api/notifications/mark-read', {
    method: 'POST',
    body: JSON.stringify({ email, notificationIds: notificationIds ?? null }),
  });
}

// ─── Push Notifications ────────────────────────────────────────────────────────

export async function registerPushToken(payload: {
  email: string;
  token: string;
  platform: string;
}): Promise<void> {
  await apiCall('/api/push/register', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function unregisterPushToken(payload: {
  email: string;
  token?: string;
}): Promise<void> {
  await apiCall('/api/push/unregister', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ── User Profile ────────────────────────────────────────────────────────────────────────────

export async function getUserProfile(email: string): Promise<{
  email: string;
  username: string | null;
  displayName: string | null;
  profileImage?: string | null;
}> {
  return apiCall(`/api/users/me?email=${encodeURIComponent(email)}`);
}

export async function setUsername(email: string, username: string): Promise<{
  ok: boolean;
  username: string | null;
  message: string;
}> {
  return apiCall('/api/users/username', {
    method: 'POST',
    body: JSON.stringify({ email, username }),
  });
}

export async function setProfileImage(email: string, imageBase64: string): Promise<{
  ok: boolean;
  profileImage: string;
}> {
  return apiCall('/api/users/profile-image', {
    method: 'POST',
    body: JSON.stringify({ email, imageBase64 }),
  });
}

export async function searchUsers(q: string): Promise<{
  id: string;
  username: string | null;
  displayName: string | null;
  label: string;
}[]> {
  return apiCall(`/api/users/search?q=${encodeURIComponent(q)}`);
}

// ─── Direct Messages (Reverse Mail) ────────────────────────────────────────────────────────────────────

export interface DmMessage {
  id: string;
  senderId: string;
  senderName?: string;
  recipientId: string;
  text: string;
  read: boolean;
  createdAt: string;
}

export interface DmConversation {
  otherId: string;
  otherName: string;
  otherImage: string | null;
  lastMessage: string;
  lastAt: string;
  unreadCount: number;
}

export async function sendDm(senderEmail: string, recipientEmail: string, text: string): Promise<{
  ok: boolean;
  message: DmMessage;
}> {
  return apiCall('/api/dm/send', {
    method: 'POST',
    body: JSON.stringify({ senderEmail, recipientEmail, text }),
  });
}

export async function getDmInbox(email: string): Promise<DmConversation[]> {
  return apiCall(`/api/dm/inbox?email=${encodeURIComponent(email)}`);
}

export async function getDmThread(email: string, other: string): Promise<DmMessage[]> {
  return apiCall(`/api/dm/thread?email=${encodeURIComponent(email)}&other=${encodeURIComponent(other)}`);
}

export async function markDmRead(email: string, otherId: string): Promise<{ ok: boolean }> {
  return apiCall('/api/dm/read', {
    method: 'PATCH',
    body: JSON.stringify({ email, otherEmail: otherId }),
  });
}

export interface LiveEvent {
  id?: string;
  time: string;
  type: string;
  text?: string;
  elapsed?: number;
  extra?: number | null;
  team?: string;
  teamId?: number;
  playerName?: string;
  playerId?: number;
  assistName?: string;
  detail?: string;
  comments?: string | null;
}

export async function fetchFixtureEvents(fixtureId: number): Promise<{ fixtureId: number; events: LiveEvent[] }> {
  return apiCall(`/api/live/fixture-events?fixtureId=${fixtureId}`);
}

export interface PlayerAdvancedStats {
  playerId: number;
  season: number;
  appearances: number;
  minutes: number;
  minutesPerGame: number;
  xG: number;
  xA: number;
  goals: number;
  assists: number;
  shots: number;
  shotsOnTarget: number;
  keyPasses: number;
  passes: number;
  passAccuracy: number;
  tackles: number;
  dribbles: number;
  dribbleSuccess: number;
  fouls: number;
  yellowCards: number;
  redCards: number;
}

export async function getPlayerAdvancedStats(playerId: number, season?: number): Promise<PlayerAdvancedStats> {
  const url = season ? `/api/players/${playerId}/advanced-stats?season=${season}` : `/api/players/${playerId}/advanced-stats`;
  return apiCall(url);
}

export async function getTeamSeasonPossession(teamId: number, leagueId?: number, season?: number): Promise<{ teamId: number; avgPossession: number | null; count: number }> {
  let url = `/api/teams/${teamId}/season-possession`;
  const params = new URLSearchParams();
  if (leagueId) params.append('leagueId', String(leagueId));
  if (season) params.append('season', String(season));
  if (params.toString()) url += `?${params.toString()}`;
  return apiCall(url);
}

