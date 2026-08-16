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

// Endpoints that involve structured synthesis — give them a generous timeout.
// The core prediction path gets its own generous cold-cache window. A first
// request can populate verified fixture/history caches, so a short client
// timer creates the false "retry" pattern where the second attempt succeeds.
const LONG_TIMEOUT_PATHS   = ['/api/mlb/predict', '/api/wta/predict', '/api/scan-prop', '/api/chat/message', '/api/lissa/message', '/api/lissa/overview', '/api/prediction-explanation'];
const LISSA_TIMEOUT_MS = 22_000;  // Atlas pick load + AI generation can exceed 10 s
const PLAYER_SEARCH_PATH   = '/api/players/search';
const MEDIUM_TIMEOUT_PATHS = ['/api/players/', '/api/match-script', '/api/community/messages'];  // match-script hits a structured press-intensity call
const CS2_PREDICT_PATH     = '/api/cs2/predict';
const CORE_PREDICTION_TIMEOUT_MS = 120_000;
const isPredictionPath = (endpoint: string) =>
  endpoint === '/api/scan-prop' || (endpoint.startsWith('/api/') && endpoint.endsWith('/predict'));
const isCorePrediction = (endpoint: string) => endpoint === '/api/predict';
// Provider-backed player searches can take several seconds on mobile Safari,
// especially when the MLB/NFL provider has to warm its cache. Do not turn a
// slow provider into a false "no results" state in the universal search.
const PLAYER_SEARCH_TIMEOUT_MS = 10_000;
const LONG_TIMEOUT_MS      = 90_000;   // 90 s — soccer / MLB / scan
const MEDIUM_TIMEOUT_MS    = 15_000;
const CS2_TIMEOUT_MS       = 150_000;  // 150 s — CS2 first-call cold cache hits 20+ BDL endpoints
const SHORT_TIMEOUT_MS     = 15_000;   // 15 s — all other API calls
// Atlas can briefly take longer than a normal UI request on a cold connection.
// The endpoint returns the durable snapshot before settlement work, so this is
// a bounded database-read window rather than permission to wait on providers.
const PICKS_LIST_TIMEOUT_MS = 20_000;
const PICKS_SNAPSHOT_PREFIX = 'reversepicks:picks-snapshot:v2:';
const inMemoryPickSnapshots: Record<string, Record<string, unknown>[]> = {};
const inFlightPickRefreshes: Record<string, Promise<Record<string, unknown>[]>> = {};

function picksSnapshotKey(email: string) {
  return `${PICKS_SNAPSHOT_PREFIX}${email.trim().toLowerCase()}`;
}

function readPickSnapshot(email: string): Record<string, unknown>[] {
  const key = picksSnapshotKey(email);
  const memory = inMemoryPickSnapshots[key];
  if (Array.isArray(memory) && memory.length > 0) return memory;
  if (typeof window === 'undefined' || !window.localStorage) return [];
  try {
    const raw = window.localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writePickSnapshot(email: string, rows: Record<string, unknown>[]) {
  if (!rows.length) return;
  const key = picksSnapshotKey(email);
  inMemoryPickSnapshots[key] = rows;
  if (typeof window === 'undefined' || !window.localStorage) return;
  try {
    window.localStorage.setItem(key, JSON.stringify(rows));
  } catch {
    // The in-memory snapshot still protects the current session if storage is
    // unavailable or the browser quota is full.
  }
}

export function cacheSavedPick(email: string, pick: Record<string, unknown>, pickId?: string) {
  if (!email || !pick) return;
  const existing = readPickSnapshot(email);
  const optimistic = {
    ...pick,
    pickId: pickId || pick.pickId || pick.id,
    status: pick.status || 'live',
    result: pick.result || 'pending',
    timestamp: pick.timestamp || new Date().toISOString(),
  };
  const optimisticId = String(optimistic.pickId || '');
  const rows = [
    optimistic,
    ...existing.filter((row) => String(row.pickId || row.id || '') !== optimisticId),
  ].slice(0, 300);
  writePickSnapshot(email, rows);
}

function clearPickSnapshot(email: string) {
  const key = picksSnapshotKey(email);
  delete inMemoryPickSnapshots[key];
  if (typeof window === 'undefined' || !window.localStorage) return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Storage cleanup is best-effort.
  }
}

function isPickSessionError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return message.includes('Your session expired') || message.includes('Invalid session');
}

function startPickRefresh(
  email: string,
  token: string,
): Promise<Record<string, unknown>[]> {
  const key = picksSnapshotKey(email);
  const existing = inFlightPickRefreshes[key];
  if (existing) return existing;

  const request = (async () => {
    try {
      const resp = await apiCall<{
        picks: Record<string, unknown>[];
        settlementDelayed?: boolean;
        snapshotComplete?: boolean;
      }>('/api/picks/list', {
        method: 'POST',
        body: JSON.stringify({ email, token }),
      });
      if (!resp || !Array.isArray(resp.picks)) {
        const snapshot = readPickSnapshot(email);
        if (snapshot.length > 0) {
          console.warn('[picks] malformed refresh; showing last verified snapshot');
          return snapshot;
        }
        throw new Error('Saved picks response was malformed.');
      }
      const rows = resp.picks;
      // Never replace a known-good snapshot with an empty response while the
      // backend is explicitly reporting a delayed storage read.
      if (rows.length > 0) {
        // Keep the offline fallback bounded; the server response remains the
        // complete list, while a browser snapshot only needs recent picks.
        writePickSnapshot(email, rows.slice(0, 300));
      } else if (resp.snapshotComplete === true && resp.settlementDelayed !== true) {
        clearPickSnapshot(email);
      }
      return rows;
    } catch (error) {
      // Do not let an expired session silently render a stale private snapshot.
      if (isPickSessionError(error)) throw error;
      const snapshot = readPickSnapshot(email);
      if (snapshot.length > 0) {
        console.warn('[picks] refresh delayed; showing last verified snapshot');
        return snapshot;
      }
      throw error;
    }
  })();
  inFlightPickRefreshes[key] = request;
  void request.then(
    () => {
      if (inFlightPickRefreshes[key] === request) delete inFlightPickRefreshes[key];
    },
    () => {
      if (inFlightPickRefreshes[key] === request) delete inFlightPickRefreshes[key];
    },
  );
  return request;
}

// Keep recent MLB/NFL identities in the current session. Once a provider
// returns a result, extending the query filters it locally instead of making
// another provider request for every keystroke.
const PLAYER_SEARCH_CACHE: Record<'mlb' | 'nfl', Map<string, any[]>> = {
  mlb: new Map(),
  nfl: new Map(),
};
const normalizeSearchQuery = (query: string) =>
  query.trim().toLowerCase().replace(/\s+/g, ' ');

function cachedPlayerSearch(sport: 'mlb' | 'nfl', query: string): any[] {
  const tokens = normalizeSearchQuery(query).split(' ').filter(Boolean);
  if (!tokens.length) return [];
  const matches = new Map<string | number, any>();
  for (const rows of PLAYER_SEARCH_CACHE[sport].values()) {
    for (const player of rows) {
      const name = normalizeSearchQuery(
        player.fullName ||
        `${player.firstName || player.first_name || ''} ${player.lastName || player.last_name || ''}`,
      );
      if (tokens.every(token => name.includes(token))) {
        matches.set(player.id ?? name, player);
      }
    }
  }
  return Array.from(matches.values()).slice(0, 15);
}

function rememberPlayerSearch(sport: 'mlb' | 'nfl', query: string, rows: any[]) {
  const cache = PLAYER_SEARCH_CACHE[sport];
  cache.set(normalizeSearchQuery(query), rows);
  while (cache.size > 48) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}

export async function apiCall<T = unknown>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const base = getApiBase();
  const url = `${base}${endpoint}`;
  const isCs2Predict = endpoint.startsWith(CS2_PREDICT_PATH);
  const isPlayerSearch = endpoint.startsWith(PLAYER_SEARCH_PATH);
  const isLissa = endpoint.startsWith('/api/lissa/');
  const isPicksList = endpoint === '/api/picks/list';
  const isLong   = LONG_TIMEOUT_PATHS.some(p => endpoint.startsWith(p));
  const isMedium = MEDIUM_TIMEOUT_PATHS.some(p => endpoint.startsWith(p));
  const timeoutMs = isLissa
    ? LISSA_TIMEOUT_MS
    : isPicksList
      ? PICKS_LIST_TIMEOUT_MS
    : isPlayerSearch
      ? PLAYER_SEARCH_TIMEOUT_MS
      : isCorePrediction(endpoint)
        ? CORE_PREDICTION_TIMEOUT_MS
        : isCs2Predict
          ? CS2_TIMEOUT_MS
          : isLong
            ? LONG_TIMEOUT_MS
            : isMedium
              ? MEDIUM_TIMEOUT_MS
              : SHORT_TIMEOUT_MS;
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
      throw new Error(
        isCorePrediction(endpoint)
          ? 'Prediction timed out after 120 seconds. No result was lost — please try again.'
          : 'Request timed out. The server is taking too long — please try again.',
      );
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
      message = isPredictionPath(endpoint)
        ? 'Prediction temporarily unavailable while provider data is refreshing. Please try again in a moment.'
        : `Server error (${resp.status}). Please try again.`;
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

export interface LissaSummary {
  total: number;
  settled: number;
  hitRate: number | null;
  counts: Record<string, number>;
  sports: Record<string, number>;
}

export interface LissaResponse {
  assistant: 'Lissa';
  sessionId: string;
  response?: string;
  message?: string;
  readOnly: boolean;
  mode?: string;
  summary: LissaSummary;
}

export interface LissaContext {
  screen?: Record<string, unknown>;
  pick?: Record<string, unknown>;
  analysis?: Record<string, unknown>;
  factors?: Array<Record<string, unknown>>;
  ledger?: Record<string, unknown>;
}

export async function startLissa(email: string, token: string): Promise<LissaResponse> {
  return apiCall<LissaResponse>('/api/lissa/overview', {
    method: 'POST',
    body: JSON.stringify({ email, token }),
  });
}

export async function sendLissaMessage(
  email: string,
  token: string,
  sessionId: string,
  message: string,
  context?: LissaContext,
): Promise<LissaResponse> {
  return apiCall<LissaResponse>('/api/lissa/message', {
    method: 'POST',
    body: JSON.stringify({ email, token, session_id: sessionId, message, context }),
  });
}

export type PredictionExplanationSection = 'read' | 'form' | 'matchup';

export interface PredictionExplanationResponse {
  section: PredictionExplanationSection;
  text: string;
  source: 'gemini' | 'deterministic';
}

/** Generate one explanation section from the already-finalized prediction. */
export async function requestPredictionSectionExplanation(
  email: string,
  token: string,
  section: PredictionExplanationSection,
  prediction: Record<string, unknown>,
): Promise<PredictionExplanationResponse> {
  return apiCall<PredictionExplanationResponse>('/api/prediction-explanation', {
    method: 'POST',
    body: JSON.stringify({ email, token, section, prediction }),
  });
}

export interface LissaSpeakResponse {
  audio: string;   // base64-encoded raw PCM (L16) or MP3
  mimeType: string; // e.g. "audio/L16;rate=24000" or "audio/mp3"
}

/** Call the Gemini TTS endpoint — returns null if TTS is unavailable or fails. */
export async function callLissaSpeak(
  email: string,
  token: string,
  text: string,
  voice = 'Kore',
): Promise<LissaSpeakResponse | null> {
  try {
    const r = await fetch(`${getApiBase()}/api/lissa/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, token, text, voice }),
      signal: AbortSignal.timeout(20_000),
    });
    if (!r.ok) return null;
    return (await r.json()) as LissaSpeakResponse;
  } catch {
    return null;
  }
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
  revenueCatCustomerId: string,
): Promise<AuthResponse & { has_access?: boolean; message?: string }> {
  return apiCall('/api/auth/iap-signup', {
    method: 'POST',
    body: JSON.stringify({ email, revenuecat_customer_id: revenueCatCustomerId }),
  });
}

export async function verifySession(
  email: string,
  session_token: string,
): Promise<{ valid?: boolean; access_type?: string }> {
  return apiCall<{ valid?: boolean; access_type?: string }>('/api/auth/verify-session', {
    method: 'POST',
    body: JSON.stringify({ email, session_token }),
  });
}

export async function heartbeatSession(email: string, session_token: string): Promise<{ ok: boolean }> {
  return apiCall('/api/auth/heartbeat', {
    method: 'POST',
    body: JSON.stringify({ email, session_token }),
  });
}

export async function syncAppleAccess(
  email: string,
  session_token: string,
  revenueCatCustomerId: string,
): Promise<{ ok: boolean; access_type?: string }> {
  return apiCall('/api/auth/iap-grant', {
    method: 'POST',
    body: JSON.stringify({
      email,
      session_token,
      revenuecat_customer_id: revenueCatCustomerId,
    }),
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
  season?: number | null;
  line?: number;
  playerId?: number;
  teamId?: number;
  opponentId?: number;
  leagueId?: number;
  leagueName?: string;
  venue?: string;
  fixtureId?: number | string | null;
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
  fixtureId?: number | string | null;
  opponent?: string | null;
  venue: string;
  value: number | null;
  season?: number | null;
  minutes: number;
  minutesPlayed?: number | null;
  competitionName?: string | null;
  round?: string | null;
  stageClass?: string | null;
  tp?: number | null;
  score?: string;
  oppRank?: number | null;
  oppTier?: string | null;
  quality?: boolean;
  teamPossession?: number | null;
  opponentPossession?: number | null;
  /** Exact opponent team shots on target in this fixture; populated for soccer saves logs. */
  opponentShotsOnTarget?: number | null;
  /** Exact opponent team pass attempts in this fixture, when provider data exists. */
  opponentPassAttempts?: number | null;
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
  minutes?: number | null;
  minutesPlayed?: number | null;
  targetStat: number | null;
  opponent: string;
  teamPossession?: number | null;
  opponentPossession?: number | null;
}

export interface PredictionResult {
  playerName?: string;
  teamName?: string;
  propType?: string;
  venue?: string;
  playerIsHome?: boolean;
  line?: number;
  projection?: number;
  confidence?: number;
  rawConfidence?: number;
  recommendation?: 'OVER' | 'UNDER' | 'PASS';
  passLeaning?: 'OVER' | 'UNDER' | string;
  passReason?: string;
  skipReason?: string;
  qualityConfidenceCapped?: boolean;
  evidenceQuality?: Record<string, unknown>;
  skipDetails?: { direction?: string; hitRate?: number; sampleSize?: number; windowDays?: number };
  confidenceScore?: number;
  bayesianMetrics?: Record<string, unknown>;
  reasoning?: string;
  tacticalBreakdown?: string;
  playerGameLogs?: {
    games?: Record<string, unknown>[];
    allGames?: Record<string, unknown>[];
    historyContext?: {
      mode?: string;
      venue?: string;
      stage?: string | null;
      stageClass?: string | null;
      stageLabel?: string;
      competitionName?: string | null;
      label?: string;
      candidateCount?: number;
      includedCount?: number;
      excludedCount?: number;
      metadataRequired?: boolean;
    };
    homeAvg?: number;
    awayAvg?: number;
    tpHomeAvg?: number | null;
    tpAwayAvg?: number | null;
    tpHomeCount?: number;
    tpAwayCount?: number;
    last10Count?: number;
    venueHistory?: {
      selectedVenue?: 'home' | 'away' | null;
      target?: number | null;
      verifiedSampleSize?: number | null;
      status?: string | null;
      fallback?: string | null;
      modelScope?: string | null;
      modelSampleSize?: number | null;
    };
    modelHitRates?: { overHits: number; underHits: number; pushHits?: number; overPct: number; underPct: number; total: number };
    archiveHitRates?: { overHits: number; underHits: number; pushHits?: number; overPct: number; underPct: number; total: number };
    hitRates?: { overHits: number; underHits: number; overPct: number; underPct: number; total: number };
  };
  modelHitRates?: { overHits: number; underHits: number; pushHits?: number; overPct: number; underPct: number; total: number };
  archiveHitRates?: { overHits: number; underHits: number; pushHits?: number; overPct: number; underPct: number; total: number };
  venueHistory?: {
    selectedVenue?: 'home' | 'away' | null;
    target?: number | null;
    verifiedSampleSize?: number | null;
    status?: string | null;
    fallback?: string | null;
  };
  historyContext?: {
    mode?: string;
    venue?: string;
    stage?: string | null;
    stageClass?: string | null;
    label?: string;
    candidateCount?: number;
    includedCount?: number;
    excludedCount?: number;
    metadataRequired?: boolean;
  };
  matchupVolume?: {
    available?: boolean;
    status?: string;
    projectionAdjustmentStatus?: string;
    minimumRecommendedSample?: number;
    venue?: string;
    opponentVenue?: string;
    team?: string | null;
    opponent?: string | null;
    homeTeam?: string | null;
    awayTeam?: string | null;
    shotsOnTarget?: Record<string, unknown>;
    passes?: Record<string, unknown>;
    fixtureSplits?: Record<string, unknown>;
    playerPassInvolvement?: Record<string, unknown>;
    goalkeeperSaveRate?: Record<string, unknown>;
    recentMatchRows?: Record<string, unknown>[];
    opponentRecentMatchRows?: Record<string, unknown>[];
  };
  /** Explanation source. Active predictions use the deterministic model. */
  aiSource?: 'model' | 'deterministic_model' | string;
  blendNote?: string;
  aiProjection?: number;
  bayesianComponent?: number;
  bayesianProjection?: number;
  edgeScore?: number;
  fixtureDate?: string;
  opponentName?: string;
  opponent?: string;
  fixtureId?: number;
  fixtureOpponentId?: number;
  fixtureTeamId?: number;
  ownerPlayerPhoto?: string;
  ownerTeamLogo?: string;
  ownerOpponentLogo?: string;
  isHome?: boolean;
  confidenceLevel?: string;
  historyGameCount?: number;
  historySeasons?: number[];
  historyRange?: { min: number; max: number };
  confidenceInterval?: [number, number];
  distribution?: {
    mostLikelyValue?: number;
    range60?: [number, number];
    range80?: [number, number];
    landingBands?: Array<{
      label: string;
      lower?: number | null;
      upper?: number | null;
      probability: number;
    }>;
    distributionType?: string;
  };
  mostLikelyValue?: number;
  range60?: [number, number];
  range80?: [number, number];
  priorMean?: number;
  priorWeight?: number;
  momentumWeight?: number;
  covariateWeight?: number;
  lineDeviationBand?: string;
  lineDeviationPct?: number;
  lineDeviationHitRate?: number;
  lineDeviationHitRateN?: number;
  sport?: string;
  tacticalAlerts?: string[];
  tacticalContext?: {
    available?: boolean;
    position?: string | null;
    role?: string | null;
    propType?: string;
    playerTeam?: string | null;
    opponent?: string | null;
    venue?: string;
    expectedPossession?: number | null;
    opponentExpectedPossession?: number | null;
    possessionSource?: string | null;
    lineupStatus?: string;
    lineupFormation?: string | null;
    opponentFormation?: string | null;
    positionPassesReceived?: {
      status?: string;
      provider?: string;
      normalization?: string;
      targetTeam?: Record<string, { attempted?: number; completed?: number; per90?: number }>;
      opponent?: Record<string, { attempted?: number; completed?: number; per90?: number }>;
      opponentAllowedToTargetPositions?: Record<string, { attempted?: number; completed?: number; per90?: number }>;
      sampleMatches?: number;
      limitations?: string[];
      reason?: string;
    } | null;
    understatPressure?: {
      status?: string;
      source?: string | null;
      sourceUrl?: string | null;
      league?: string | null;
      season?: number | null;
      asOf?: string | null;
      venue?: string | null;
      projectionInfluence?: string;
      opponentPress?: {
        ppda?: number | null;
        label?: string | null;
        leaguePercentile?: number | null;
        sampleSize?: number | null;
        venue?: string | null;
      } | null;
      targetTeamOppPpda?: number | null;
      pressureRouteVerified?: boolean;
      reason?: string | null;
    } | null;
    recentOpponentBlockProfiles?: {
      status?: string;
      available?: boolean;
      sampleSize?: number;
      verifiedMatches?: number;
      ppdaMatches?: number;
      formationMatches?: number;
      source?: string | null;
      projectionInfluence?: string;
      profiles?: Array<{
        fixtureId?: number | string | null;
        date?: string | null;
        opponent?: string | null;
        venue?: string | null;
        playerValue?: number | null;
        minutes?: number | null;
        score?: string | null;
        status?: string;
        verified?: boolean;
        ppda?: number | null;
        ppdaStatus?: string | null;
        pressureByThird?: Record<string, number> | null;
        formation?: {
          status?: string;
          teamFormation?: string | null;
          opponentFormation?: string | null;
        } | null;
        blockProfile?: {
          label?: string;
          status?: string;
          confidence?: string;
          dominantPressureShare?: number;
        } | null;
        shadowWeight?: number;
        source?: {
          pressure?: string | null;
          formation?: string | null;
        } | null;
      }>;
      shadowWeighting?: {
        status?: string;
        projectionAdjustment?: number;
        weights?: Record<string, number>;
      };
      limitations?: string[];
    } | null;
    fbrefEnrichment?: {
      available?: boolean;
      status?: string;
      projectionInfluence?: string;
      pressure?: {
        status?: string;
        label?: string | null;
        ppda?: number | null;
        source?: string | null;
        method?: string | null;
        pressures?: number | null;
        pressureSuccessPct?: number | null;
      } | null;
      zones?: {
        status?: string;
        dominance?: string | null;
        defThirdSharePct?: number | null;
        midThirdSharePct?: number | null;
        attThirdSharePct?: number | null;
        progressivePasses?: number | null;
        progressiveCarries?: number | null;
      } | null;
    };
  };
  tacticalIntelligence?: TacticalIntelligence;
  matchScript?: MatchScript;
  positionalReality?: PositionalReality;
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
  tpHomeAvg?: number | null;
  tpAwayAvg?: number | null;
  tpHomeCount?: number;
  tpAwayCount?: number;
  last10Count?: number;
  sampleSize?: number;
  hitRates?: { overHits: number; underHits: number; overPct: number; underPct: number; total: number };
  h2hPlayerStats?: {
    matches: H2HMatch[];
    avgVsOpponent?: number;
    minVsOpponent?: number;
    maxVsOpponent?: number;
    sampleSize: number;
    targetProp?: string;
    teamMeetings?: number;
    teamMeetingsByVenue?: {
      home?: Array<{
        date?: string;
        score?: string;
        homeTeam?: string;
        awayTeam?: string;
        homePossession?: number | null;
        awayPossession?: number | null;
        possessionAvailable?: boolean;
      }>;
      away?: Array<{
        date?: string;
        score?: string;
        homeTeam?: string;
        awayTeam?: string;
        homePossession?: number | null;
        awayPossession?: number | null;
        possessionAvailable?: boolean;
      }>;
    };
    venueSplits?: {
      home?: {
        sampleSize?: number;
        average?: number;
        overHits?: number;
        underHits?: number;
        pushHits?: number;
        overPct?: number;
        underPct?: number;
        minutesAverage?: number;
      };
      away?: {
        sampleSize?: number;
        average?: number;
        overHits?: number;
        underHits?: number;
        pushHits?: number;
        overPct?: number;
        underPct?: number;
        minutesAverage?: number;
      };
    };
    seasonsCovered?: { min: number; max: number; range: string } | null;
    trendDirection?: 'improving' | 'declining' | 'stable';
    trendDelta?: number;
    venueHitRate?: { hits: number; total: number; pct: number; venue: string } | null;
    historySeasons?: number;
    searchedFixtureCount?: number;
  };
  positionComparison?: {
    targetPosition?: string;
    targetRole?: string;
    comparisonMode?: 'same-position' | 'same-role' | string;
    positionEvidenceType?: 'exact_position' | 'unavailable' | string;
    positionEvidenceNote?: string;
    positionShort?: string;
    opponent?: string;
    venue?: string;
    propType?: string;
    avgStatValue?: number;
    average?: number;
    weightedAverage?: number | null;
    sampleSize?: number;
     avgPossession?: number;
     avgOpponentPossession?: number;
     expectedPlayerPossession?: number;
     possessionSampleSize?: number;
     teamPossessionSampleSize?: number;
     opponentPossessionSampleSize?: number;
     possessionSource?: string;
     possessionStatus?: string;
    minimumRecommendedSample?: number;
    sampleStatus?: string;
    overHitRate?: number;
    underHitRate?: number;
    players?: Array<Record<string, unknown> & {
      name?: string;
      playerId?: number;
      statValue?: number | null;
      passAttempts?: number | null;
      crossPropStats?: Record<string, number>;
      matchPosition?: string | null;
      observedPosition?: string | null;
      position?: string | null;
       positionVerified?: boolean;
       positionSource?: string;
       roleInferred?: boolean;
    }>;
    sourceScope?: string;
    verdict?: {
      verdict?: string;
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
    effectiveSampleSize?: number;
  };
  opponentDefensiveProfile?: {
    opponent: string;
    propType: string;
    position: string;
    avgAllowed: number;
    sampleSize: number;
    vsPlayerSeasonAvg: number | null;
    isFavorable: boolean | null;
    playerSeasonAvg: number | null;
  };
  managerContext?: {
    coachName?: string;
    coachStartDate?: string | null;
    prevCoachName?: string | null;
    daysElapsed?: number | null;
    isRecent?: boolean;
    recentChange?: boolean;
    logSplitInfo?: {
      postCount: number;
      preCount: number;
      preAvg?: number | null;
      postAvg?: number | null;
      thinSample: boolean;
    };
    possessionDrift?: {
      seasonAvg: number;
      last5Avg: number;
      drift: number;
      isShift: boolean;
      direction: string;
      sampleSize?: number;
    };
  };
  expectedPossession?: { home: number; away: number };
  possessionStatus?: 'verified' | 'estimated' | 'unavailable' | string;
  possessionSource?: string | null;
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
  playerPositionSource?: string;
  playerRoleSource?: string;
  playerRoleConfidence?: string;
  playerRoleIsInferred?: boolean;
  positionEvidence?: {
    genericPosition?: string | null;
    specificPosition?: string | null;
    displayPosition?: string | null;
    role?: string | null;
    source?: string | null;
    status?: string | null;
    confidence?: string | null;
    evidence?: string[];
    decisionRule?: string;
  };
  leagueRoleBucket?: string;
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
  analysisFactors?: AnalysisFactor[];
  modelInputSnapshot?: Record<string, unknown>;
  factorLedger?: {
    version: string;
    factors: Array<{
      id: string;
      title: string;
      sequence?: number;
      status?: string;
      before?: number | null;
      after?: number | null;
      delta?: number | null;
      direction?: 'up' | 'down' | 'neutral' | string;
      multiplier?: number | null;
      sampleSize?: number | null;
      inputs?: Record<string, unknown>;
      reason?: string;
      kind?: 'confidence' | string;
    }>;
    final: {
      projectedValue?: number | null;
      line?: number | null;
      recommendation?: string;
      pOver?: number | null;
      pUnder?: number | null;
      confidenceScore?: number | null;
      confidenceLevel?: string;
      edge?: number | null;
      edgeRating?: string;
      edgeRatingReason?: string;
      safetyRating?: string;
    };
  };
  factorLedgerVersion?: string;
  factorLedgerFingerprint?: string;
  edgeRating?: 'SHARP EDGE' | 'EDGE' | 'MARGINAL' | 'NO EDGE';
  edgeRatingReason?: string;
  safetyRating?: 'SAFE' | 'MODERATE' | 'RISKY' | 'AVOID';
  propHistoricalRate?: number;
  propHistoricalN?: number;
  coinFlip?: boolean;
  prizePicksContext?: string;
  scenarioProbabilities?: { best: number; base: number; worst: number };
  /** Retained for compatibility with older saved responses; active predictions are complete. */
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
  player?: {
    id?: number;
    name?: string;
    team?: string;
    position?: string;
    role?: string;
    positionSource?: string;
    roleSource?: string;
    roleConfidence?: string;
    roleIsInferred?: boolean;
  };
  propType?: string;
  line?: number;
  projectedValue?: number;
  recommendation?: string;
  passLeaning?: string | null;
  passOutcome?: 'hit' | 'miss' | 'push' | null;
  isCalibrationOnly?: boolean;
  confidenceScore?: number;
  confidenceLevel?: string;
  edgeRating?: string;
  edgeRatingReason?: string;
  safetyRating?: string;
  possessionStatus?: 'verified' | 'estimated' | 'unavailable' | string;
  possessionSource?: string | null;
  confidenceInterval?: [number, number];
  distribution?: PredictionResult['distribution'];
  mostLikelyValue?: number;
  range60?: [number, number];
  range80?: [number, number];
  playerCandidates?: Array<{ playerId: number; playerName: string; teamName: string; position: string; leagueId?: number }>;
  reasoning?: string;
  tacticalBreakdown?: string;
  aiSource?: 'model' | 'deterministic_model' | string;
  sharpSummary?: string;
  tacticalAlerts?: string[];
  analysisFactors?: AnalysisFactor[];
  modelInputSnapshot?: Record<string, unknown>;
  factorLedger?: PredictionResult['factorLedger'];
  factorLedgerVersion?: string;
  factorLedgerFingerprint?: string;
  isWorldCup?: boolean;
  riskSignals?: PredictionResult['riskSignals'];
  congestion?: PredictionResult['congestion'];
  lineup?: PredictionResult['lineup'];
  blendNote?: string;
  aiProjection?: number;
  bayesianComponent?: number;
  opponent?: string;
  fixtureId?: number;
  fixtureDate?: string;
  fixtureOpponentId?: number;
  fixtureTeamId?: number;
  ownerPlayerPhoto?: string;
  ownerTeamLogo?: string;
  ownerOpponentLogo?: string;
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
    allGames?: Record<string, unknown>[];
    historyContext?: {
      mode?: string;
      venue?: string;
      stage?: string | null;
      stageClass?: string | null;
      stageLabel?: string;
      competitionName?: string | null;
      label?: string;
      candidateCount?: number;
      includedCount?: number;
      excludedCount?: number;
      metadataRequired?: boolean;
    };
    homeAvg?: number;
    awayAvg?: number;
    tpHomeAvg?: number | null;
    tpAwayAvg?: number | null;
    tpHomeCount?: number;
    tpAwayCount?: number;
    last10Count?: number;
    venueHistory?: {
      selectedVenue?: 'home' | 'away' | null;
      target?: number | null;
      verifiedSampleSize?: number | null;
      status?: string | null;
      fallback?: string | null;
      modelScope?: string | null;
      modelSampleSize?: number | null;
    };
    modelHitRates?: { overHits: number; underHits: number; pushHits?: number; overPct: number; underPct: number; total: number };
    archiveHitRates?: { overHits: number; underHits: number; pushHits?: number; overPct: number; underPct: number; total: number };
    hitRates?: { overHits: number; underHits: number; overPct: number; underPct: number; total: number; summary?: string };
  };
  matchupVolume?: PredictionResult['matchupVolume'];
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
    teamMeetings?: number;
    teamMeetingsByVenue?: {
      home?: Array<{
        date?: string;
        score?: string;
        homeTeam?: string;
        awayTeam?: string;
        homePossession?: number | null;
        awayPossession?: number | null;
        possessionAvailable?: boolean;
      }>;
      away?: Array<{
        date?: string;
        score?: string;
        homeTeam?: string;
        awayTeam?: string;
        homePossession?: number | null;
        awayPossession?: number | null;
        possessionAvailable?: boolean;
      }>;
    };
    venueSplits?: {
      home?: {
        sampleSize?: number;
        average?: number;
        overHits?: number;
        underHits?: number;
        pushHits?: number;
        overPct?: number;
        underPct?: number;
        minutesAverage?: number;
      };
      away?: {
        sampleSize?: number;
        average?: number;
        overHits?: number;
        underHits?: number;
        pushHits?: number;
        overPct?: number;
        underPct?: number;
        minutesAverage?: number;
      };
    };
  };
  matchDominance?: {
    applied?: boolean;
    multiplier?: number;
    expectedPoss?: number;
    teamSeasonAvg?: number;
    oppSeasonAvg?: number;
    notes?: string[];
    qualityGap?: {
      eligible?: boolean;
      applied?: boolean;
      multiplier?: number;
      deltaPct?: number;
      score?: number;
      direction?: string;
      competition?: Record<string, unknown>;
      signals?: Array<Record<string, unknown>>;
      possessionCorroborates?: boolean;
      reason?: string;
    };
  };
  matchupOverview?: {
    expectedPossession?: { home: number; away: number };
    possessionStatus?: 'verified' | 'estimated' | 'unavailable' | string;
    possessionSource?: string | null;
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
  coinFlip?: boolean;
  rawConfidence?: number;
  lineDeviationBand?: string;
  lineDeviationPct?: number;
  lineDeviationHitRate?: number;
  lineDeviationHitRateN?: number;
  gameScript?: Record<string, unknown>;
  error?: string;
}

export interface TacticalIntelligence {
  version?: string;
  mode?: 'shadow' | string;
  status?: 'strong' | 'limited' | 'unavailable' | string;
  sourcePolicy?: string;
  player?: {
    position?: string | null;
    role?: string | null;
    roleGroup?: string;
    providerGridPosition?: { x?: number | null; y?: number | null };
    positionSource?: string;
    roleSource?: string;
    roleConfidence?: string;
    roleEvidence?: string[];
    roleSampleSize?: number;
  };
  lineup?: {
    status?: string;
    shapeStatus?: string;
    formation?: string | null;
    opponentFormation?: string | null;
    playerTeam?: string | null;
    opponent?: string | null;
    playerCount?: number;
    opponentPlayerCount?: number;
  };
  marketGameScript?: {
    status?: string;
    playerTeamImpliedProbability?: number | null;
    opponentImpliedProbability?: number | null;
    classification?: string;
    direction?: string;
    source?: string | null;
  };
  possessionGameScript?: {
    status?: string;
    expectedPlayerTeamPossession?: number | null;
    classification?: string;
    source?: string | null;
  };
  matchScript?: MatchScript;
  positionalReality?: PositionalReality;
  propMechanism?: {
    propType?: string;
    roleGroup?: string;
    marketSupport?: string[];
    gameScriptEvidence?: string[];
    opponentEvidence?: string;
    opponentNote?: string | null;
    projectionAdjustment?: number;
    projectionAdjustmentStatus?: string;
  };
  opponentRoleComparison?: {
    targetPosition?: string | null;
    targetRoleGroup?: string;
    opponentRoleCounts?: Record<string, number>;
    opponentDefensiveCount?: number;
    relevantMechanism?: string;
    comparison?: string;
    directMarkingVerified?: boolean;
    sampleStatus?: string;
  };
  evidence?: {
    opponentAllowedAverage?: number | null;
    opponentAllowedSamples?: number;
    positionComparableSamples?: number;
    formationData?: string;
    marketData?: string;
    possessionData?: string;
  };
  tacticalConclusion?: string;
  playerOpponentHistory?: {
    overHits?: number;
    underHits?: number;
    overPct?: number | null;
    underPct?: number | null;
    sampleSize?: number;
    evidenceStatus?: string;
    opponent?: string;
  } | null;
  positionCohort?: {
    positionShort?: string;
    opponent?: string;
    venue?: string;
    average?: number | null;
    avgStatValue?: number | null;
    sampleSize?: number;
    minimumRecommendedSample?: number;
    sampleStatus?: string;
    overHits?: number;
    underHits?: number;
    overHitRate?: number | null;
    underHitRate?: number | null;
  } | null;
  limitations?: string[];
}

export interface MatchScript {
  classification?: string;
  label?: string;
  confidence?: number;
  confidenceLabel?: string;
  status?: string;
  sources?: string[];
  subjectTeamImpliedProbability?: number | null;
  expectedPossession?: number | null;
  scenarioDominant?: string | null;
  limitations?: string[];
}

export interface PositionalReality {
  version?: string;
  zone?: string;
  zoneSource?: string;
  zoneConfidence?: number;
  coordinates?: {
    x?: number | null;
    y?: number | null;
    attackingDirectionY?: number | null;
  };
  roleGroup?: string;
  role?: string | null;
  scriptBucket?: string;
  roleMechanism?: string;
  propSignal?: {
    propType?: string;
    shadowDirection?: string;
    shadowStrength?: number;
    shadowMultiplier?: number;
    rationale?: string;
    activationStatus?: string;
  };
  playerStyle?: {
    profile?: string;
    evidence?: string;
    sampleSize?: number;
  };
  robustEvidence?: {
    status?: string;
    sampleSize?: number;
    median?: number | null;
    weightedMean?: number | null;
    outlierCount?: number;
    outlierRate?: number;
    method?: string;
    policy?: string;
  };
  limitations?: string[];
  mode?: string;
  activationStatus?: string;
}

export interface AnalysisFactor {
  id: string;
  title: string;
  status: 'applied' | 'measured' | 'warning' | 'unavailable' | string;
  summary: string;
  value?: unknown;
  sampleSize?: number | null;
  impact?: 'projection' | 'confidence' | 'context' | string;
  direction?: 'up' | 'down' | 'neutral' | string;
  detail?: string;
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

const BROAD_POSITION_LABELS = new Set([
  'GK', 'G', 'GOALKEEPER',
  'DEF', 'D', 'DEFENDER',
  'MID', 'M', 'MIDFIELDER',
  'FWD', 'F', 'FW', 'FORWARD', 'ATTACKER',
]);

function customerRole(position: unknown, role: unknown): string | undefined {
  const normalizedPosition = String(position || '').trim().toUpperCase().replace(/[\s_-]+/g, '');
  if (BROAD_POSITION_LABELS.has(normalizedPosition)) return undefined;
  const value = String(role || '').trim();
  return value || undefined;
}

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
  // Soccer predictions have existed in a few response shapes across app
  // versions. Keep the live UI tolerant at this boundary instead of making
  // the chart depend on one backend field name.
  const rawGameSources = [
    raw.playerGameLogs?.allGames,
    raw.playerGameLogs?.games,
    (raw as any).gameLogs,
    (raw as any).recentSamples,
  ];
  const rawGames = (
    rawGameSources.find((source) => Array.isArray(source) && source.length > 0)
      || []
  ) as Record<string, unknown>[];
  const gameLogs: GameLog[] = rawGames.length > 0
    ? rawGames
        .map(g => {
          // Prefer the mapped field, fall back to backend-computed targetStat
          const mappedVal = statField ? (g[statField] as number | null | undefined) : undefined;
          const value = mappedVal != null
            ? mappedVal
            : (g.value as number | null | undefined)
              ?? (g.targetStat as number | null | undefined)
              ?? (g.statValue as number | null | undefined)
              ?? (g.stat as number | null | undefined)
              ?? (g.pass_attempts as number | null | undefined)
              ?? (g.passes_total as number | null | undefined)
              ?? null;
          return {
            date: (g.date as string) || '',
            fixtureId: (g.fixtureId as number | string | null) ?? null,
            opponent: (g.opponent as string) || '',
            venue: (g.venue as string) || '',
            value,
            minutes: (g.minutes as number) || 0,
            minutesPlayed: (g.minutesPlayed as number | null) ?? (g.minutes as number) ?? null,
            competitionName: (
              (g.competitionName as string | null)
              ?? (g.league as string | null)
              ?? (g.leagueName as string | null)
              ?? null
            ),
            round: (g.round as string | null) ?? (g.matchRound as string | null) ?? null,
            stageClass: (g.stageClass as string | null) ?? null,
            tp: (g.tp as number | null) ?? (g.teamPossession as number | null) ?? null,
            score: (g.score as string) || undefined,
            oppRank: (g.oppRank as number | null) ?? undefined,
            oppTier: (g.oppTier as string | null) ?? undefined,
            quality: (g.quality as boolean) ?? undefined,
            teamPossession: (g.teamPossession as number | null) ?? null,
            opponentPossession: (g.opponentPossession as number | null) ?? null,
             opponentShotsOnTarget: (g.opponentShotsOnTarget as number | null) ?? null,
            blocks: (g.tackles_blocks as number | null) ?? null,
            interceptions: (g.tackles_interceptions as number | null) ?? null,
            tackles: (g.tackles_total as number | null) ?? null,
            clearances: (g.tackles_clearances as number | null) ?? null,
            synthetic: !!(g.synthetic),
          };
        })
        .filter(g => g.value != null)
        // Always newest-first so the bar chart and Bayesian recency weights
        // see the most recent game at index 0 regardless of backend order.
        .sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')))
    : [];

  return {
    playerName: raw.player?.name || (request.playerName as string) || '',
    teamName: raw.player?.team || (request.teamName as string) || '',
    opponentName: raw.opponent || (request.opponentName as string) || '',
    propType: raw.propType || (request.propType as string) || '',
    line: raw.line ?? (request.line as number) ?? 0,
    fixtureId: raw.fixtureId,
    fixtureDate: raw.fixtureDate,
    fixtureOpponentId: raw.fixtureOpponentId,
    fixtureTeamId: raw.fixtureTeamId,
    venue: (raw as any).venue || (raw as any).playerVenue || undefined,
    playerIsHome: (raw as any).playerIsHome ?? (raw as any).matchupOverview?.playerIsHome,
    projection: raw.projectedValue,
    confidence: raw.confidenceScore,
    rawConfidence: raw.rawConfidence ?? raw.confidenceScore,
    recommendation: rec,
    passReason: (raw as any).passReason ?? undefined,
    skipReason: (raw as any).skipReason ?? undefined,
    skipDetails: (raw as any).skipDetails ?? undefined,
    reasoning: raw.reasoning || undefined,
    confidenceLevel: raw.confidenceLevel,
    confidenceInterval: raw.confidenceInterval,
    distribution: (raw as any).distribution ?? (bm as any).distribution,
    mostLikelyValue: (raw as any).mostLikelyValue ?? (bm as any).mostLikelyValue,
    range60: (raw as any).range60 ?? (bm as any).range60,
    range80: (raw as any).range80 ?? (bm as any).range80,
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
    matchupVolume: raw.matchupVolume ?? undefined,
    homeAvg: raw.playerGameLogs?.homeAvg,
    awayAvg: raw.playerGameLogs?.awayAvg,
    tpHomeAvg: raw.playerGameLogs?.tpHomeAvg,
    tpAwayAvg: raw.playerGameLogs?.tpAwayAvg,
    tpHomeCount: raw.playerGameLogs?.tpHomeCount,
    tpAwayCount: raw.playerGameLogs?.tpAwayCount,
    last10Count: raw.playerGameLogs?.last10Count,
    historyContext: raw.playerGameLogs?.historyContext,
    venueHistory: raw.playerGameLogs?.venueHistory,
    modelHitRates: raw.playerGameLogs?.modelHitRates,
    archiveHitRates: raw.playerGameLogs?.archiveHitRates,
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
    h2hPlayerStats: raw.h2hPlayerStats
      && (raw.h2hPlayerStats.matches?.length || raw.h2hPlayerStats.teamMeetings)
      ? {
          matches: (raw.h2hPlayerStats.matches ?? []).map(m => ({
            date: m.date || '',
            score: m.score || m.matchScore || '',
             venue: ['home', 'away'].includes(String(m.venue || '').toLowerCase())
               ? String(m.venue).toLowerCase()
               : '',
            minutes: m.minutesPlayed ?? m.minutes ?? null,
            minutesPlayed: m.minutesPlayed ?? m.minutes ?? null,
            targetStat: m.targetStat ?? null,
            opponent: m.opponent || '',
            teamPossession: (m.teamPossession as number | null) ?? null,
            opponentPossession: (m.opponentPossession as number | null) ?? null,
          })),
          avgVsOpponent: raw.h2hPlayerStats.avgVsOpponent,
          sampleSize: raw.h2hPlayerStats.sampleSize || 0,
          targetProp: raw.h2hPlayerStats.targetProp,
          teamMeetings: raw.h2hPlayerStats.teamMeetings,
          teamMeetingsByVenue: raw.h2hPlayerStats.teamMeetingsByVenue,
          venueSplits: raw.h2hPlayerStats.venueSplits,
        }
      : undefined,
    expectedPossession: raw.matchupOverview?.expectedPossession
      ?? (raw.matchDominance?.expectedPoss != null && raw.matchDominance.expectedPoss !== 50
        ? { home: raw.matchDominance.expectedPoss, away: 100 - raw.matchDominance.expectedPoss }
        : undefined),
    possessionStatus: raw.possessionStatus
      ?? raw.matchupOverview?.possessionStatus
      ?? (raw.possessionSource || raw.matchupOverview?.possessionSource
        ? 'estimated'
        : 'unavailable'),
    possessionSource: raw.possessionSource
      ?? raw.matchupOverview?.possessionSource
      ?? undefined,
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
    isHome: (raw as any).isHome,
    teamId: raw.fixtureTeamId || raw._request?.teamId || (request.teamId as number) || undefined,
    opponentId: raw.fixtureOpponentId || raw._request?.opponentId || (request.opponentId as number) || undefined,
    leagueId: raw._request?.leagueId || (request.leagueId as number) || undefined,
    playerId: raw._request?.playerId || raw.player?.id || undefined,
    playerPosition: raw.player?.position || undefined,
    playerRole: customerRole(raw.player?.position, raw.player?.role),
    playerPositionSource: raw.player?.positionSource || undefined,
    playerRoleSource: raw.player?.roleSource || undefined,
    playerRoleConfidence: raw.player?.roleConfidence || undefined,
    playerRoleIsInferred: raw.player?.roleIsInferred || undefined,
    positionEvidence: (raw as any).positionEvidence ?? undefined,
    leagueRoleBucket: (raw as any).leagueRoleBucket ?? undefined,
    sport: raw.sport || (request.sport as string) || undefined,
    tacticalAlerts: raw.tacticalAlerts || undefined,
    isWorldCup: (raw as any).isWorldCup || undefined,
    riskSignals: (raw as any).riskSignals ?? undefined,
    congestion: (raw as any).congestion ?? undefined,
    lineup: (raw as any).lineup ?? undefined,
    tacticalContext: (raw as any).tacticalContext ?? undefined,
    tacticalIntelligence: (raw as any).tacticalIntelligence ?? undefined,
    matchScript: (raw as any).matchScript ?? (raw as any).tacticalIntelligence?.matchScript ?? undefined,
    positionalReality: (raw as any).positionalReality ?? (raw as any).tacticalIntelligence?.positionalReality ?? undefined,
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
    lineDeviationHitRateN: raw.lineDeviationHitRateN ?? undefined,
    dataQuality: raw.dataQuality ? { level: raw.dataQuality.level, message: raw.dataQuality.message, gamesWithData: raw.dataQuality.gamesWithData, totalGames: raw.dataQuality.totalGames } : undefined,
    analysisSummary: raw.analysisSummary ?? undefined,
    analysisFactors: raw.analysisFactors ?? undefined,
    modelInputSnapshot: raw.modelInputSnapshot ?? undefined,
    factorLedger: raw.factorLedger ?? undefined,
    factorLedgerVersion: raw.factorLedgerVersion ?? undefined,
    factorLedgerFingerprint: raw.factorLedgerFingerprint ?? undefined,
    tacticalBreakdown: raw.tacticalBreakdown || undefined,
    aiSource: (raw as any).aiSource || undefined,
    keyFactors: Array.isArray((raw as any).keyFactors) ? ((raw as any).keyFactors as string[]) : undefined,
    qualityConfidenceCapped: (raw as any).qualityConfidenceCapped ?? undefined,
    evidenceQuality: (raw as any).evidenceQuality ?? undefined,
    qualitySignal: (raw as any).qualitySignal || undefined,
    currentOppTier: (raw as any).currentOppTier || undefined,
    currentOppRank: (raw as any).currentOppRank ?? undefined,
    blendNote: raw.blendNote || undefined,
    aiProjection: raw.aiProjection || undefined,
    bayesianComponent: raw.bayesianComponent || undefined,
    edgeRating: raw.edgeRating as PredictionResult['edgeRating'] ?? undefined,
    edgeRatingReason: raw.edgeRatingReason ?? undefined,
    safetyRating: raw.safetyRating as PredictionResult['safetyRating'] ?? undefined,
    propHistoricalRate: (raw as any).propHistoricalRate ?? undefined,
    propHistoricalN: (raw as any).propHistoricalN ?? undefined,
    coinFlip: raw.coinFlip ?? undefined,
    playerCandidates: raw.playerCandidates ?? undefined,
    prizePicksContext: (raw as any).prizePicksContext ?? undefined,
    aiPending: (raw as any).aiPending ?? undefined,
     managerContext: (raw as any).managerContext ?? undefined,
     ownerPlayerPhoto: raw.ownerPlayerPhoto || undefined,
     ownerTeamLogo: raw.ownerTeamLogo || undefined,
     ownerOpponentLogo: raw.ownerOpponentLogo || undefined,
  };
}


export interface Pick {
  _id?: string;
  id?: string;
  pickId?: string;
  playerName: string;
  playerId?: number;
  teamName?: string;
  teamId?: number;
  opponentName?: string;
  opponentId?: number;
  propType: string;
  line: number;
  projection?: number;
  recommendation?: string;
  passLeaning?: string | null;
  passOutcome?: 'hit' | 'miss' | 'push' | null;
  isCalibrationOnly?: boolean;
  confidence?: number;
  confidenceLevel?: string;
  projectedValue?: number;
  status?: string;
  result?: string;
  settlementReview?: {
    reason?: string;
    actualValue?: number | null;
    previousResult?: string | null;
    markedBy?: string;
    markedAt?: string;
  } | null;
  actualValue?: number | null;
  settlementSource?: {
    provider?: string;
    fixtureId?: number | string | null;
    playerId?: number | string | null;
    propType?: string;
    statPath?: string;
    fixtureStatus?: string | null;
    verified?: boolean;
    verificationMethod?: string;
    recordedAt?: string;
  } | null;
  minutesPlayed?: number | null;
  voidReason?: string | null;
  currentValue?: number | null;
  pace?: number | null;
  hitPct?: number | null;
  liveGaussian?: {
    available?: boolean;
    model?: string;
    elapsed?: number;
    currentValue?: number;
    remainingMinutes?: number;
    preMatchMean?: number;
    preMatchStd?: number;
    observedRate?: number;
    adjustedRate?: number;
    drift?: string;
    driftRatio?: number;
    uncertainty?: string;
    projectedValue?: number;
    remainingProjection?: number;
    std?: number;
    pOver?: number;
    pUnder?: number;
    recommendationProbability?: number;
    range60?: [number, number];
    range80?: [number, number];
  } | null;
  paceMismatch?: boolean | null;
  paceWarning?: string | null;
  liveConfidenceScore?: number | null;
  liveConfidenceLevel?: string | null;
  preMatchProjection?: number | null;
  preMatchConfidenceScore?: number | null;
  elapsed?: number | null;
  period?: string;
  matchStatus?: string;
  fixtureId?: number | null;
  fixtureDate?: string;
  createdAt?: string;
  settledAt?: string;
  sport?: string;
  venue?: string;
  playerIsHome?: boolean;
  homeTeam?: string;
  awayTeam?: string;
  count?: number;
  hits?: number;
  misses?: number;
  pushes?: number;
  dnps?: number;
  winRate?: number;
  trackingId?: string;
  position?: string;
  role?: string;
  roleEvidence?: Record<string, unknown> | string[];
  leagueId?: number;
  leagueName?: string;
  coinFlip?: boolean;
  matchScore?: string;
  finalHomeGoals?: number | null;
  finalAwayGoals?: number | null;
  homePoss?: number | null;
  awayPoss?: number | null;
  projHomePoss?: number | null;
  projAwayPoss?: number | null;
  oppAvgPoss?: number | null;
  // Deterministic model explanation fields (persisted for offline analysis modal)
  sharpSummary?: string;
  reasoning?: string;
  tacticalBreakdown?: string;
  playerGameLogs?: {
    games?: Record<string, unknown>[];
    homeAvg?: number;
    awayAvg?: number;
    tpHomeAvg?: number | null;
    tpAwayAvg?: number | null;
    tpHomeCount?: number;
    tpAwayCount?: number;
    last10Count?: number;
    hitRates?: { overHits: number; underHits: number; overPct: number; underPct: number; total: number };
  };
  aiSource?: 'model' | 'deterministic_model' | string;
  tacticalAlerts?: string[];
  gameScript?: Record<string, unknown>;
  matchScript?: MatchScript;
  positionalReality?: PositionalReality;
  analysisFactors?: AnalysisFactor[];
  modelInputSnapshot?: Record<string, unknown>;
  bayesianMetrics?: Record<string, unknown>;
  // Manager / coaching change context (persisted so badge shows without re-predicting)
  managerContext?: {
    isRecent?: boolean;
    coachName?: string;
    prevCoachName?: string | null;
    recentChange?: boolean;
  };
  // Owner-only media fields (player photos + team crests from API-Football cache)
  ownerPlayerPhoto?: string;
  ownerTeamLogo?: string;
  ownerOpponentLogo?: string;
  // Post-save coaching change flag — set by background job when the team's
  // coach changed AFTER this pick was saved.  Triggers the RE-RUN SUGGESTED badge.
  managerChangedAfterPick?: boolean;
  managerChangeCoachName?: string;
  managerChangeDate?: string;
  // Evidence-quality gate — set when the model capped confidence due to limited data
  qualityConfidenceCapped?: boolean;
  passReason?: string;
}

export async function listPicks(email: string, token: string): Promise<Pick[]> {
  const normalizeRows = (rows: Record<string, unknown>[]) => rows.map(p => ({
    pickId: p.pickId as string,
    _id: (p.pickId as string) || (p._id as string),
    id: (p.pickId as string) || (p.id as string),
    playerName: (p.playerName as string) || '',
    playerId: (() => {
      const value = p.playerId;
      const numeric = typeof value === 'number' ? value : Number(value);
      return Number.isFinite(numeric) && numeric > 0 ? numeric : undefined;
    })(),
    teamName: p.teamName as string,
     teamId: (() => {
       const value = p.teamId;
       const numeric = typeof value === 'number' ? value : Number(value);
       return Number.isFinite(numeric) && numeric > 0 ? numeric : undefined;
     })(),
    opponentName: p.opponentName as string,
     opponentId: (() => {
       const value = p.opponentId;
       const numeric = typeof value === 'number' ? value : Number(value);
       return Number.isFinite(numeric) && numeric > 0 ? numeric : undefined;
     })(),
    propType: (p.propType as string) || '',
    line: (p.line as number) || 0,
    // normalize projectedValue → projection
    projection: (p.projectedValue as number) ?? (p.projection as number),
    // normalize to uppercase OVER/UNDER
    recommendation: ((p.recommendation as string) || '').toUpperCase() || undefined,
    passLeaning: (p.passLeaning as string) || null,
    passOutcome: (p.passOutcome as Pick['passOutcome']) || null,
    isCalibrationOnly: p.isCalibrationOnly as boolean | undefined,
    // normalize confidenceScore → confidence
    confidence: (p.confidenceScore as number) ?? (p.confidence as number),
    confidenceLevel: p.confidenceLevel as string,
    projectedValue: p.projectedValue as number,
    status: p.status as string,
    result: p.result as string,
    settlementReview: (p.settlementReview as Pick['settlementReview']) || null,
    actualValue: p.actualValue as number ?? null,
    settlementSource: (p.settlementSource as Pick['settlementSource']) || null,
    minutesPlayed: (p.minutesPlayed as number) ?? null,
    voidReason: (p.voidReason as string) || null,
    currentValue: (p.currentValue as number) ?? null,
    pace: (p.pace as number) ?? null,
    hitPct: (p.hitPct as number) ?? null,
    liveGaussian: (p.liveGaussian as Pick['liveGaussian']) || null,
    paceMismatch: (p.paceMismatch as boolean) ?? null,
    paceWarning: (p.paceWarning as string) ?? null,
    liveConfidenceScore: (p.liveConfidenceScore as number) ?? null,
    liveConfidenceLevel: (p.liveConfidenceLevel as string) ?? null,
    preMatchProjection: (p.preMatchProjection as number) ?? null,
    preMatchConfidenceScore: (p.preMatchConfidenceScore as number) ?? null,
    elapsed: (p.elapsed as number) ?? null,
    period: p.period as string,
    matchStatus: p.matchStatus as string,
    fixtureId: (p.fixtureId as number) ?? null,
    fixtureDate: (p.fixtureDate as string) || undefined,
    leagueId: (p.leagueId as number) ?? undefined,
    createdAt: (p.timestamp as string) || (p.createdAt as string),
    sport: p.sport as string,
    venue: p.venue as string,
    trackingId: p.trackingId as string,
    position: (p.position as string) || undefined,
    role: customerRole(p.position, p.role),
    roleEvidence: (p.roleEvidence as Pick['roleEvidence']) || undefined,
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
    // Structured analysis fields persisted on pick
    sharpSummary: (p.sharpSummary as string) || undefined,
    reasoning: (p.reasoning as string) || undefined,
    tacticalBreakdown: (p.tacticalBreakdown as string) || undefined,
    playerGameLogs: (p.playerGameLogs as Pick['playerGameLogs']) || undefined,
    aiSource: (p.aiSource as string) || undefined,
    tacticalAlerts: (p.tacticalAlerts as string[]) || undefined,
    gameScript: (p.gameScript as Record<string, unknown>) || undefined,
    matchScript: (p.matchScript as MatchScript) || undefined,
    positionalReality: (p.positionalReality as PositionalReality) || undefined,
    analysisFactors: (p.analysisFactors as AnalysisFactor[]) || undefined,
    modelInputSnapshot: (p.modelInputSnapshot as Record<string, unknown>) || undefined,
    bayesianMetrics: (p.bayesianMetrics as Record<string, unknown>) || undefined,
    // Owner-only media fields pass-through
    ownerPlayerPhoto: (p.ownerPlayerPhoto as string) || undefined,
    ownerTeamLogo: (p.ownerTeamLogo as string) || undefined,
    ownerOpponentLogo: (p.ownerOpponentLogo as string) || undefined,
    // Manager change badge fields — persisted at save time so the badge shows
    // without re-running the prediction.
    managerContext: (p.managerContext as Pick['managerContext']) || undefined,
    // Post-save coaching change flag — set by background job
    managerChangedAfterPick: (p.managerChangedAfterPick as boolean) || undefined,
    managerChangeCoachName: (p.managerChangeCoachName as string) || undefined,
    managerChangeDate: (p.managerChangeDate as string) || undefined,
    // Evidence-quality gate fields persisted at save time
    qualityConfidenceCapped: (p.qualityConfidenceCapped as boolean) || undefined,
    passReason: (p.passReason as string) || undefined,
  }));

  const snapshot = readPickSnapshot(email);
  if (snapshot.length > 0) {
    // Paint the last successful list immediately. The server refresh is
    // deduplicated and the next poll picks up any new settlement.
    void startPickRefresh(email, token);
    return normalizeRows(snapshot);
  }
  return normalizeRows(await startPickRefresh(email, token));
}

export async function getMatchups(email: string, token: string): Promise<{
  picks: Pick[];
  options: {
    players: string[];
    opponents: string[];
    venues: string[];
    positions: string[];
    propTypes: string[];
    leagues: string[];
    results: string[];
  };
}> {
  const resp = await apiCall<{ picks: Record<string, unknown>[]; options: Record<string, string[]> }>('/api/picks/matchups', {
    method: 'POST',
    body: JSON.stringify({ email, token }),
  });
  const picks = (resp.picks || []).map(p => ({
    pickId: (p.pickId as string) || '',
    playerName: (p.playerName as string) || '',
    playerId: (() => {
      const value = p.playerId;
      const numeric = typeof value === 'number' ? value : Number(value);
      return Number.isFinite(numeric) && numeric > 0 ? numeric : undefined;
    })(),
    teamName: p.teamName as string,
    teamId: (p.teamId as number) ?? undefined,
    opponentName: p.opponentName as string,
    opponentId: (p.opponentId as number) ?? undefined,
    propType: (p.propType as string) || '',
    line: (p.line as number) || 0,
    position: (p.position as string) || undefined,
    role: customerRole(p.position, p.role),
    recommendation: (p.recommendation as string) || undefined,
    result: (p.result as string) || undefined,
    actualValue: (p.actualValue as number) ?? null,
    sport: p.sport as string,
    leagueId: (p.leagueId as number) ?? undefined,
    leagueName: p.leagueName as string,
    matchScore: (p.matchScore as string) || undefined,
    playerIsHome: p.playerIsHome as boolean | undefined,
    homeTeam: p.homeTeam as string,
    awayTeam: p.awayTeam as string,
    settledAt: (p.settledAt as string) || undefined,
    count: (p.count as number) || 1,
    hits: (p.hits as number) || 0,
    misses: (p.misses as number) || 0,
    pushes: (p.pushes as number) || 0,
    dnps: (p.dnps as number) || 0,
    winRate: (p.winRate as number) || 0,
    ownerPlayerPhoto: (p.ownerPlayerPhoto as string) || undefined,
    ownerTeamLogo: (p.ownerTeamLogo as string) || undefined,
    ownerOpponentLogo: (p.ownerOpponentLogo as string) || undefined,
  }));
  const options = resp.options || {};
  return {
    picks,
    options: {
      players: options.players || [],
      opponents: options.opponents || [],
      venues: options.venues || ["Home", "Away"],
      positions: options.positions || [],
      propTypes: options.propTypes || [],
      leagues: options.leagues || [],
      results: options.results || ["Hit", "Miss", "Push", "DNP"],
    },
  };
}

export async function savePick(email: string, token: string, pick: Record<string, unknown>) {
  const result = await apiCall<{ success?: boolean; pickId?: string; trackingId?: string }>('/api/picks/save', {
    method: 'POST',
    body: JSON.stringify({ email, token, pick }),
  });
  // Make a newly saved pick visible immediately, even when the user navigates
  // to My Picks before the next durable list refresh completes.
  cacheSavedPick(email, pick, result.pickId);
  return result;
}

export async function autoPostPickToCommunity(
  email: string,
  token: string,
  pickId: string,
  imageData: string,
) {
  return apiCall('/api/community/auto-post-pick', {
    method: 'POST',
    body: JSON.stringify({ email, token, pickId, imageData }),
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

export async function refreshPickAnalysis(
  email: string,
  token: string,
  pickId: string,
): Promise<{ ok: boolean; text: string; source: string }> {
  return apiCall<{ ok: boolean; text: string; source: string }>(
    `/api/picks/${encodeURIComponent(pickId)}/refresh-analysis`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, token }),
    },
  );
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
  total?: number;
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

export async function searchTeams(query: string, leagueId?: number, signal?: AbortSignal): Promise<{ results: TeamSearchResult[] }> {
  const params = new URLSearchParams({ q: query });
  if (leagueId) params.set('league_id', String(leagueId));
  return apiCall(`/api/search/teams?${params.toString()}`, { signal });
}

export interface LeagueSearchResult {
  id: number;
  name: string;
  country: string;
  logo?: string;
}

export async function searchLeagues(query: string, signal?: AbortSignal): Promise<{ leagues: LeagueSearchResult[] }> {
  const params = new URLSearchParams({ search: query });
  return apiCall(`/api/leagues/search?${params.toString()}`, { signal });
}

export interface PlayerSearchResult {
  playerId: number;
  playerName: string;
  teamId: number;
  teamName: string;
  leagueId: number;
  position?: string;
  ownerPlayerPhoto?: string;
  ownerTeamLogo?: string;
}

export async function searchPlayersQuick(
  query: string,
  leagueId?: number,
  session?: { email: string; token: string },
  signal?: AbortSignal,
): Promise<{ players: PlayerSearchResult[] }> {
  return apiCall('/api/players/search', {
    method: 'POST',
    body: JSON.stringify({
      query,
      league_id: leagueId,
      ...(session ? { email: session.email, token: session.token } : {}),
    }),
    signal,
  });
}

export interface PlayerContext {
  teamId: number;
  teamName: string;
  leagueId: number;
  isNational: boolean;
  verified?: boolean;
  lastKnown?: boolean;
}

export interface NextMatchData {
  found: boolean;
  isHome?: boolean;
  rawIsHome?: boolean;
  playerTeam?: { id: number; name: string };
  opponent?: { id: number; name: string };
  homeTeam?: { id: number; name: string };
  awayTeam?: { id: number; name: string };
  leagueId?: number;
  leagueName?: string;
  date?: string;
  fixtureId?: number;
}

export async function getPlayerContexts(playerId: number): Promise<{
  contexts: PlayerContext[];
  teamVerified?: boolean;
  verificationStatus?: 'verified' | 'last_known' | 'unavailable';
  lastKnownClub?: {
    teamId: number;
    teamName: string;
    leagueId: number;
    verifiedSeason?: number;
  } | null;
}> {
  return apiCall(`/api/players/${playerId}/contexts`);
}

export interface PlayerRoleResult {
  position: string;
  role: string;
  source?: string;
  roleIsInferred?: boolean;
  confidence?: string;
  evidence?: string[];
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

export interface ProbabilityMetrics {
  n: number;
  logLoss: number | null;
  brierScore: number | null;
}

export interface CalibrationBin {
  label: string;
  n: number;
  predictedPct: number;
  observedPct: number;
  gapPp: number;
}

export interface ProjectionMetrics {
  n: number;
  mae: number | null;
  rmse: number | null;
  meanError: number | null;
}

export interface ProjectionGroupMetrics extends ProjectionMetrics {
  sport: string;
  propType: string;
}

export interface ModelScorecard {
  n: number;
  rawN?: number;
  scoredN?: number;
  duplicateRowsRemoved?: number;
  resultCounts?: Record<string, number>;
  calibrationOnlyN?: number;
  passCalibration?: {
    n: number;
    hits: number;
    misses: number;
    pushes: number;
    winPct: number;
    byDirection: Record<string, { hit?: number; miss?: number; push?: number }>;
  };
  dateRange: { from: string | null; to: string | null };
  classification: {
    finalConfidence: ProbabilityMetrics;
    rawConfidence: ProbabilityMetrics;
    calibration: CalibrationBin[];
  };
  projection: {
    overall: ProjectionMetrics;
    byProp: ProjectionGroupMetrics[];
    unitsNote: string;
  };
  chronologicalHoldout: {
    description: string;
    n: number;
    dateRange: { from: string | null; to: string | null };
    classification: ProbabilityMetrics;
    projection: ProjectionMetrics;
  };
  byDirection?: Record<string, {
    n: number;
    hits: number;
    misses: number;
    hitRate: number | null;
    logLoss: number | null;
    brierScore: number | null;
  }>;
}

export interface AnalyticsData {
  overall: {
    hits: number;
    misses: number;
    total: number;
    winPct: number;
    pushes?: number;
    dnps?: number;
    calibrationOnly?: number;
    actionable?: number;
    outcomeCounts?: {
      hit: number;
      miss: number;
      push: number;
      dnp: number;
      unknown?: number;
    };
    passCalibration?: {
      n: number;
      hits: number;
      misses: number;
      pushes: number;
      winPct: number;
      byDirection: Record<string, { hit?: number; miss?: number; push?: number }>;
    };
  };
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
  scorecard: ModelScorecard;
  walkForwardReplay?: {
    eligibleSamples: number;
    evaluatedSamples: number;
    leakageViolations: number;
    byDirection: Record<string, {
      n: number;
      hits: number;
      misses: number;
      hitRate: number | null;
      logLoss: number | null;
      brierScore: number | null;
    }>;
    bySport: Array<{
      sport: string;
      classification: { n: number; logLoss: number | null; brierScore: number | null };
      projection: { n: number; mae: number | null; rmse: number | null; meanError: number | null };
    }>;
    byProp: Array<{
      sport: string;
      propType: string;
      n: number;
      mae: number | null;
      rmse: number | null;
      meanError: number | null;
    }>;
    trends?: {
      periods: {
        all: Array<{ sport: string; n: number; logLoss: number | null; brierScore: number | null; mae: number | null }>;
        '30d': Array<{ sport: string; n: number; logLoss: number | null; brierScore: number | null; mae: number | null }>;
        '7d': Array<{ sport: string; n: number; logLoss: number | null; brierScore: number | null; mae: number | null }>;
      };
      n: { all: number; '30d': number; '7d': number };
    };
  };
  insights?: SystemInsights;
  scope?: {
    access: 'owner';
    dataset: 'all_users';
    sport: 'soccer';
    period?: 'all' | 'today' | '30d' | '7d';
    rawSettled: number;
    settled: number;
    duplicateRowsRemoved: number;
  };
  /** All-sports dedup counts — covers the full pick history across every sport. */
  allSportsDedup?: {
    rawN: number;
    n: number;
    scoredN: number;
    duplicateRowsRemoved: number;
    sports: string[];
  };
  passingDiagnostics?: PassingDiagnostics;
}

export interface PassingMetricSummary {
  n: number;
  hits: number;
  misses: number;
  hitRate: number | null;
  under: { n: number; hits: number; misses: number; hitRate: number | null };
  over: { n: number; hits: number; misses: number; hitRate: number | null };
  meanProjectionError: number | null;
  projectionN: number;
}

export interface PassingDiagnosticBucket extends PassingMetricSummary {
  label: string;
}

export interface PassingReplayBucket {
  label: string;
  n: number;
  evaluatedN: number;
  leakageViolations: number;
  missingPriorDataEvents: number;
  classification: { n: number; logLoss: number | null; brierScore: number | null };
  projection: { n: number; mae: number | null; rmse: number | null; meanError: number | null };
  byDirection: Record<string, { n: number; hits: number; misses: number; hitRate: number | null }>;
}

export interface PassingDiagnostics {
  scope: {
    propTypes: string[];
    rawRows: number;
    uniqueEvents: number;
    fixtures: number;
    scoredEvents: number;
  };
  correlationSummary: {
    correlatedEvents: number;
    independentEvents: number;
    fixtureIdentityUnavailableEvents: number;
    correlatedFixtures: number;
    independentFixtures: number;
    correlated: PassingMetricSummary;
    independent: PassingMetricSummary;
  };
  sourceAudit: {
    verifiedSourceEvents: number;
    exactFixtureSourceEvents: number;
    missingFixtureEvents: number;
    statPaths: Array<{ path: string; n: number }>;
  };
  dimensions: {
    league: PassingDiagnosticBucket[];
    competition: PassingDiagnosticBucket[];
    position: PassingDiagnosticBucket[];
    possessionBand: PassingDiagnosticBucket[];
    correlation: PassingDiagnosticBucket[];
  };
  walkForward: {
    overall: PassingReplayBucket;
    byLeague: PassingReplayBucket[];
    byCompetition: PassingReplayBucket[];
    byPosition: PassingReplayBucket[];
    byPossessionBand: PassingReplayBucket[];
    byCorrelation: PassingReplayBucket[];
    method: string;
  };
  note: string;
}

export interface SystemInsightDimension {
  label: string;
  total: number;
  rate: number;
  overRate: number;
  underRate: number;
}

export interface SystemInsights {
  total: number;
  settled: number;
  hits: number;
  misses: number;
  pushes: number;
  winRate: number;
  currentStreak: number;
  overHit: number;
  underHit: number;
  overTotal: number;
  underTotal: number;
  tiers: { tier: string; hit: number; total: number; rate: number }[];
  trend: { date: string; rate: number; total: number }[];
  byLeague: SystemInsightDimension[];
  byProp: SystemInsightDimension[];
  bySport: SystemInsightDimension[];
  bestLeagues: SystemInsightDimension[];
  worstLeagues: SystemInsightDimension[];
}

function ownerAnalyticsCacheKey(
  email: string,
  period: 'all' | 'today' | '30d' | '7d',
  sport?: string | null,
) {
  return `reversepicks:owner-analytics:v2:${email.toLowerCase()}:${period}:${sport || 'all'}`;
}

export function getCachedOwnerAnalytics(
  email: string,
  period: 'all' | 'today' | '30d' | '7d' = 'all',
  sport?: string | null,
): AnalyticsData | null {
  if (typeof window === 'undefined' || !window.localStorage) return null;
  try {
    const cached = JSON.parse(
      window.localStorage.getItem(ownerAnalyticsCacheKey(email, period, sport)) || 'null',
    );
    return cached && typeof cached === 'object' ? cached as AnalyticsData : null;
  } catch {
    return null;
  }
}

export async function getOwnerAnalytics(
  email: string,
  token: string,
  period: 'all' | 'today' | '30d' | '7d' = 'all',
  sport?: string | null,
): Promise<AnalyticsData> {
  const key = ownerAnalyticsCacheKey(email, period, sport);
  try {
    const result = await apiCall<AnalyticsData>('/api/admin/analytics', {
      method: 'POST',
      body: JSON.stringify({ email, token, period, sport: sport ?? null }),
    });
    if (typeof window !== 'undefined' && window.localStorage) {
      try { window.localStorage.setItem(key, JSON.stringify(result)); } catch {}
    }
    return result;
  } catch (error) {
    // Analytics is an owner dashboard, not the source of truth for picks. Keep
    // the last verified report visible through a slow Atlas/replay refresh.
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes('session expired') || message.includes('Invalid session')) throw error;
    const cached = getCachedOwnerAnalytics(email, period, sport);
    if (cached) {
      console.warn('[analytics] refresh delayed; showing last verified report');
      return cached;
    }
    throw error;
  }
}

export interface StorageCollectionStat {
  dataMb: number;
  storageMb: number;
  count: number;
}

export interface StorageHealth {
  dataMb: number | null;
  storageMb: number | null;
  indexMb: number | null;
  totalMb: number | null;
  limitMb: number;
  usedPct: number | null;
  degraded: boolean | null;
  warning: boolean | null;
  status: 'OK' | 'WARNING' | 'DEGRADED' | 'UNKNOWN';
  collections: Record<string, StorageCollectionStat | null>;
  error?: string;
}

export async function getStorageHealth(
  email: string,
  token: string,
): Promise<StorageHealth> {
  try {
    return await apiCall(`/api/admin/storage-health?email=${encodeURIComponent(email)}&token=${encodeURIComponent(token)}`, {
      method: 'GET',
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes('session expired') || message.includes('Invalid session')) throw error;
    return {
      dataMb: null,
      storageMb: null,
      indexMb: null,
      totalMb: null,
      limitMb: 512,
      usedPct: null,
      degraded: null,
      warning: null,
      status: 'UNKNOWN',
      collections: {},
      error: 'Storage status is still refreshing. Your saved data is not affected.',
    };
  }
}

export async function triggerStorageCleanup(
  email: string,
  token: string,
): Promise<{ success: boolean; deleted: Record<string, number | string>; totalDeleted: number }> {
  return apiCall('/api/admin/trigger-cleanup', {
    method: 'POST',
    body: JSON.stringify({ email, token }),
  });
}

export interface KnowledgeStats {
  teamsTotal: number;
  teamsFresh: number;
  playersTotal: number;
  playersFresh: number;
  heuristicsTotal: number;
  kbMisses: number;
  ttlHours: number;
  error?: string;
}

export async function getKnowledgeStats(
  email: string,
  token: string,
): Promise<KnowledgeStats> {
  try {
    return await apiCall(`/api/admin/knowledge/stats?email=${encodeURIComponent(email)}&token=${encodeURIComponent(token)}`, {
      method: 'GET',
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes('session expired') || message.includes('Invalid session')) throw error;
    return {
      teamsTotal: 0,
      teamsFresh: 0,
      playersTotal: 0,
      playersFresh: 0,
      heuristicsTotal: 0,
      kbMisses: 0,
      ttlHours: 0,
      error: 'Knowledge status is still refreshing.',
    };
  }
}

export async function refreshKnowledge(
  email: string,
  token: string,
  opts: { teamId?: number; playerId?: number; leagueId?: number } = {},
): Promise<{ success: boolean; results: Record<string, unknown> }> {
  return apiCall('/api/admin/knowledge/refresh', {
    method: 'POST',
    body: JSON.stringify({ email, token, ...opts }),
  });
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
  { key: 'weekly',  name: 'Weekly',  price: '$14.99/week' },
  { key: 'monthly', name: 'Monthly', price: '$39.99/month' },
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

export async function searchCs2Players(query: string, signal?: AbortSignal): Promise<Cs2Player[]> {
  if (!query || query.length < 2) return [];
  return apiCall<Cs2Player[]>(`/api/cs2/players/search?q=${encodeURIComponent(query)}`, { signal });
}

export interface Cs2Team {
  id: number;
  name: string;
  shortName?: string | null;
}

export async function searchCs2Teams(query: string, signal?: AbortSignal): Promise<Cs2Team[]> {
  if (!query || query.length < 2) return [];
  return apiCall<Cs2Team[]>(`/api/cs2/teams/search?q=${encodeURIComponent(query)}`, { signal });
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

export async function searchWtaPlayers(query: string, signal?: AbortSignal): Promise<WtaPlayer[]> {
  if (!query || query.length < 2) return [];
  return apiCall<WtaPlayer[]>(`/api/wta/players/search?q=${encodeURIComponent(query)}`, { signal });
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

export async function searchNbaPlayers(query: string, signal?: AbortSignal): Promise<NbaPlayer[]> {
  if (!query || query.length < 2) return [];
  const raw = await apiCall<any>(`/api/nba/players/search?q=${encodeURIComponent(query)}`, { signal });
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

export async function searchNhlPlayers(query: string, signal?: AbortSignal): Promise<NhlPlayer[]> {
  if (!query || query.length < 2) return [];
  const raw = await apiCall<any>(`/api/nhl/players/search?q=${encodeURIComponent(query)}`, { signal });
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

export async function searchNflPlayers(query: string, signal?: AbortSignal): Promise<NflPlayer[]> {
  if (!query || query.length < 2) return [];
  const cached = cachedPlayerSearch('nfl', query);
  if (cached.length) return cached as NflPlayer[];
  const raw = await apiCall<any>(`/api/nfl/players/search?q=${encodeURIComponent(query)}`, { signal });
  const rows: any[] = Array.isArray(raw) ? raw : (raw?.players || raw?.results || []);
  const mapped = rows.map((p: any) => ({
    id:        p.id        ?? 0,
    firstName: p.firstName ?? p.first_name  ?? '',
    lastName:  p.lastName  ?? p.last_name   ?? '',
    fullName:  p.fullName  ?? `${p.firstName ?? p.first_name ?? ''} ${p.lastName ?? p.last_name ?? ''}`.trim(),
    position:  p.position  ?? '',
    team:      p.team      ?? null,
    jersey:    p.jersey    ?? null,
    college:   p.college   ?? null,
  }));
  rememberPlayerSearch('nfl', query, mapped);
  return mapped;
}

export interface NflNextMatch {
  found:     boolean;
  gameId?:   number | null;
  date?:     string;
  venue?:    'home' | 'away';
  opponent?: { id: number | null; name: string; abbreviation?: string } | null;
  seasonType?: 'preseason' | 'regular' | 'postseason' | string;
  source?: string;
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
    season:          g.season          ?? null,
    sport:           'nfl',
    passing_yards:   g.passing_yards   ?? null,
    rushing_yards:   g.rushing_yards   ?? null,
    receiving_yards: g.receiving_yards ?? null,
    receptions:      g.receptions      ?? null,
    score:           g.score           ?? null,
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
    historyGameCount:   raw.historyGameCount ?? undefined,
    historySeasons:     raw.historySeasons ?? undefined,
    historyRange:       raw.historyRange ?? undefined,
    matchupOverview: raw.matchupOverview ? {
      homeTeam:         raw.matchupOverview.homeTeam,
      awayTeam:         raw.matchupOverview.awayTeam,
      playerIsHome:     raw.matchupOverview.playerIsHome,
      expectedGameType: raw.matchupOverview.expectedGameType,
      keyMatchupFactor: raw.matchupOverview.keyMatchupFactor,
      moneyline:        raw.matchupOverview.moneyline ?? raw.moneyline,
    } : undefined,
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

export async function searchMlbPlayers(query: string, signal?: AbortSignal): Promise<MlbPlayer[]> {
  if (!query || query.length < 2) return [];
  const cached = cachedPlayerSearch('mlb', query);
  if (cached.length) return cached as MlbPlayer[];
  const raw = await apiCall<any>(`/api/mlb/players/search?q=${encodeURIComponent(query)}`, { signal });
  const rows: any[] = Array.isArray(raw) ? raw : (raw?.players || raw?.results || []);
  const mapped = rows.map((p: any) => ({
    id:        p.id          ?? p.player_id  ?? 0,
    firstName: p.firstName   ?? p.first_name ?? '',
    lastName:  p.lastName    ?? p.last_name  ?? '',
    fullName:  p.fullName    ?? p.full_name  ?? `${p.firstName ?? p.first_name ?? ''} ${p.lastName ?? p.last_name ?? ''}`.trim(),
    position:  p.position    ?? '',
    team:      p.team        ?? null,
  }));
  rememberPlayerSearch('mlb', query, mapped);
  return mapped;
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
    const result = await apiCall<MlbNextMatch>(`/api/mlb/next-match?player_id=${playerId}`);
    // Defense-in-depth for stale deployed/cache responses: a past MLB game
    // must never be shown as the auto-filled next matchup.
    if (result.found && result.date) {
      const todayUtc = new Date().toISOString().slice(0, 10);
      if (result.date.slice(0, 10) < todayUtc) return { found: false };
    }
    return result;
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
    season:        g.season   ?? null,
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
    historyGameCount:   raw.historyGameCount ?? undefined,
    historySeasons:     raw.historySeasons ?? undefined,
    historyRange:       raw.historyRange ?? undefined,
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
  includeImages?: boolean;
}): Promise<CommunityMessage[]> {
  const qs = new URLSearchParams();
  if (params?.since) qs.set('since', params.since);
  if (params?.before) qs.set('before', params.before);
  if (params?.limit) qs.set('limit', String(params.limit));
  if (params?.includeImages !== undefined) qs.set('include_images', String(params.includeImages));
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

export async function sharePickToCommunity(
  email: string, pick: Pick, imageData?: string,
): Promise<CommunityMessage> {
  return apiCall('/api/community/share-pick', {
    method: 'POST',
    body: JSON.stringify({ email, pick, imageData: imageData || null }),
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

export async function fetchCommunityOnlineCount(
  email: string,
  token: string,
): Promise<{ count: number }> {
  return apiCall('/api/community/online', {
    method: 'POST',
    body: JSON.stringify({ email, token }),
  });
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

export type UserProfile = {
  email: string;
  username: string | null;
  displayName: string | null;
  profileImage?: string | null;
};

function userProfileCacheKey(email: string) {
  return `reversepicks:user-profile:v1:${email.toLowerCase()}`;
}

export function getCachedUserProfile(email: string): UserProfile | null {
  if (typeof window === 'undefined' || !window.localStorage) return null;
  try {
    const cached = JSON.parse(window.localStorage.getItem(userProfileCacheKey(email)) || 'null');
    return cached && typeof cached === 'object' ? cached as UserProfile : null;
  } catch {
    return null;
  }
}

export async function getUserProfile(email: string): Promise<UserProfile> {
  try {
    const result = await apiCall<UserProfile>(`/api/users/me?email=${encodeURIComponent(email)}`);
    if (typeof window !== 'undefined' && window.localStorage) {
      try { window.localStorage.setItem(userProfileCacheKey(email), JSON.stringify(result)); } catch {}
    }
    return result;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes('session expired') || message.includes('Invalid session')) throw error;
    const cached = getCachedUserProfile(email);
    if (cached) {
      console.warn('[profile] refresh delayed; showing last verified profile');
      return cached;
    }
    throw error;
  }
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

export async function deleteDmConversation(email: string, otherId: string): Promise<{ ok: boolean; deleted: number }> {
  return apiCall(`/api/dm/conversation?email=${encodeURIComponent(email)}&other=${encodeURIComponent(otherId)}`, {
    method: 'DELETE',
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

export interface ReplayCalibrationBin {
  label: string;
  n: number;
  prospectiveN: number;
  priorPredictedPct: number | null;
  observedPct: number | null;
  gapPp: number | null;
  finalObservedPct: number | null;
  note?: string;
}


export interface WalkForwardReplay {
  description: string;
  eligibleSamples: number;
  evaluatedSamples: number;
  missingPriorDataEvents: number;
  leakageViolations: number;
  dateRange: { from: string | null; to: string | null };
  classification: ReplayClassification;
  prospectiveCalibration: ReplayCalibrationBin[];
  projection: ReplayProjection;
  bySport: ReplaySportEntry[];
  byProp: ReplayPropEntry[];
}

export interface ReplayProjection {
  n: number;
  mae: number | null;
  rmse: number | null;
  meanError: number | null;
}

export async function runModelReplay(
  email: string,
  token: string,
  sport: string = '',
): Promise<ModelReplayResult> {
  return apiCall('/api/admin/model-replay', {
    method: 'POST',
    body: JSON.stringify({ email, token, sport }),
  });
}

export interface ReplayClassification {
  n: number;
  logLoss: number | null;
  brierScore: number | null;
}

export interface ReplayPropEntry {
  sport: string;
  propType: string;
  n: number;
  mae: number | null;
  rmse: number | null;
  meanError: number | null;
}

export interface ModelReplayResult {
  success: boolean;
  n: number;
  sport: string;
  generatedAt?: string;
  observations: string[];
  descriptiveScorecard: DescriptiveScorecardResult | null;
  walkForwardReplay: WalkForwardReplay | null;
}

export interface DescriptiveScorecardResult {
  description: string;
  n: number;
  rawN: number;
  scoredN?: number;
  duplicateRowsRemoved: number;
  resultCounts: Record<string, number>;
  calibrationOnlyN: number;
  dateRange: { from: string | null; to: string | null };
  classification: {
    finalConfidence: ReplayClassification;
    rawConfidence: ReplayClassification;
    calibration: Array<{
      label: string;
      n: number;
      predictedPct: number;
      observedPct: number;
      gapPp: number;
    }>;
  };
  projection: {
    overall: ReplayProjection;
    byProp: ReplayPropEntry[];
    unitsNote: string;
  };
  chronologicalHoldout: {
    description: string;
    n: number;
    dateRange: { from: string | null; to: string | null };
    classification: ReplayClassification;
    projection: ReplayProjection;
  };
}

export interface ReplaySportEntry {
  sport: string;
  classification: ReplayClassification;
  projection: ReplayProjection;
}

// ─── Position backfill & repair ─────────────────────────────────────────────

export interface StalePickPosition {
  collection: string;
  pickId: string;
  playerName: string;
  playerId: string | number;
  storedPosition: string;
  newPosition: string;
  sport?: string;
  propType?: string;
}

export interface PositionBackfillResult {
  startedAt: string;
  finishedAt: string;
  scanned: number;
  updated: number;
  changed: number;
  alreadyTrusted: number;
  noFixtureHistory: number;
  noExactLineupEvidence: number;
  insufficientRepeatedEvidence: number;
  categoryMismatch: number;
  errors: number;
  changedProfiles: Array<{
    playerId: string;
    playerName: string;
    previousPosition?: string;
    newPosition: string;
  }>;
  stalePickPositions: StalePickPosition[];
}

export async function runPositionBackfill(
  email: string,
  token: string,
): Promise<PositionBackfillResult> {
  return apiCall('/api/admin/positions/backfill-api-sports', {
    method: 'POST',
    body: JSON.stringify({ email, token }),
  });
}

export async function repairStalePickPositions(
  email: string,
  token: string,
  stalePickPositions: StalePickPosition[],
): Promise<{ success: boolean; updated: number; skipped: number; errors: number; message: string }> {
  return apiCall('/api/admin/positions/repair-stale-pick-positions', {
    method: 'POST',
    body: JSON.stringify({ email, token, stalePickPositions }),
  });
}
