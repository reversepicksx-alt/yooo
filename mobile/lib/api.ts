import { Platform } from 'react-native';

const getApiBase = (): string => {
  if (Platform.OS === 'web' && typeof window !== 'undefined') {
    return '';
  }
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
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    const detail = (err as { detail?: string | Array<{ msg?: string }> }).detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg || 'Validation error').join(', ')
      : (typeof detail === 'string' ? detail : 'Request failed');
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

export interface PredictionResult {
  playerName?: string;
  teamName?: string;
  opponentName?: string;
  propType?: string;
  line?: number;
  projection?: number;
  bayesianProjection?: number;
  confidence?: number;
  recommendation?: 'OVER' | 'UNDER' | 'PASS';
  reasoning?: string;
  confidenceLevel?: string;
  confidenceInterval?: [number, number];
  bayesianMetrics?: {
    posteriorMean?: number;
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
  playerGameLogs?: { games?: Record<string, unknown>[]; homeAvg?: number; awayAvg?: number; hitRates?: unknown };
  matchContext?: { league?: string; round?: string; date?: string };
  matchupOverview?: {
    expectedPossession?: { home: number; away: number };
    homeTeam?: string;
    awayTeam?: string;
    moneyline?: { home: string; draw: string; away: string };
    favorite?: string;
    expectedGameType?: string;
    keyMatchupFactor?: string;
  };
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
  dataQuality?: { level?: string; message?: string; gamesWithData?: number; totalGames?: number };
  error?: string;
}

export interface Pick {
  _id?: string;
  id?: string;
  pickId?: string;
  playerName?: string;
  teamName?: string;
  opponentName?: string;
  propType?: string;
  line?: number;
  recommendation?: string;
  venue?: string;
  status?: string;
  result?: string;
  trackingId?: string;
  projection?: number;
  actualValue?: number | null;
  currentValue?: number | null;
  pace?: number | null;
  hitPct?: number | null;
  elapsed?: number | null;
  period?: string;
  fixtureId?: number | null;
  playerId?: number | null;
  createdAt?: string;
}

export const PROP_TYPES = [
  { label: 'Pass Attempts', value: 'pass_attempts' },
  { label: 'Shots', value: 'shots' },
  { label: 'Shots on Target', value: 'shots_on_target' },
  { label: 'Goals', value: 'goals' },
  { label: 'Assists', value: 'assists' },
  { label: 'Key Passes', value: 'key_passes' },
  { label: 'Tackles', value: 'tackles' },
  { label: 'Saves', value: 'saves' },
  { label: 'Dribbles', value: 'dribbles' },
  { label: 'Crosses', value: 'crosses' },
  { label: 'Interceptions', value: 'interceptions' },
  { label: 'Blocks', value: 'blocks' },
  { label: 'Fouls Drawn', value: 'fouls_drawn' },
  { label: 'Fouls Committed', value: 'fouls_committed' },
  { label: 'Clearances', value: 'clearances' },
  { label: 'Yellow Cards', value: 'yellow_cards' },
  { label: 'Duels Won', value: 'duels_won' },
  { label: 'Shot Assists', value: 'shots_assisted' },
  { label: 'Passes', value: 'passes' },
] as const;

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
  const raw = await apiCall<PredictionResult>('/api/predict', {
    method: 'POST',
    body: JSON.stringify(request),
  });
  return raw;
}
