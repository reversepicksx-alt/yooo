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
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error((err as { detail?: string }).detail || 'Request failed');
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
      opponentName: ext.opponentName || opp.name,
      playerTeam: ext.playerTeam,
      teamName: res.teamName || ext.playerTeam,
      leagueId: ext.leagueId,
      playerId: res.id || res.playerId,
      teamId: res.teamId,
      opponentId: opp.id,
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
}

export interface PredictionResult {
  playerName?: string;
  teamName?: string;
  propType?: string;
  line?: number;
  projection?: number;
  confidence?: number;
  recommendation?: 'OVER' | 'UNDER' | 'PASS';
  reasoning?: string;
  tacticalBreakdown?: string;
  bayesianProjection?: number;
  edgeScore?: number;
  fixtureDate?: string;
  opponentName?: string;
  confidenceLevel?: string;
  confidenceInterval?: [number, number];
  priorMean?: number;
  momentumEffect?: number;
  momentumLabel?: string;
  streakFlag?: string;
  gameLogs?: GameLog[];
  homeAvg?: number;
  awayAvg?: number;
  sampleSize?: number;
  hitRates?: { overHits: number; underHits: number; overPct: number; underPct: number; total: number };
  error?: string;
}

interface RawPrediction {
  player?: { id?: number; name?: string; team?: string; position?: string };
  propType?: string;
  line?: number;
  projectedValue?: number;
  recommendation?: string;
  confidenceScore?: number;
  confidenceLevel?: string;
  confidenceInterval?: [number, number];
  reasoning?: string;
  tacticalBreakdown?: string;
  opponent?: string;
  bayesianMetrics?: {
    posteriorMean?: number;
    edgeZ?: number;
    momentumEffect?: number;
    momentumLabel?: string;
    priorMean?: number;
    streakFlag?: string;
    pOver?: number;
    pUnder?: number;
    reversalFlag?: string;
  };
  playerGameLogs?: {
    games?: Record<string, unknown>[];
    homeAvg?: number;
    awayAvg?: number;
    hitRates?: { overHits: number; underHits: number; overPct: number; underPct: number; total: number; summary?: string };
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
  const gameLogs: GameLog[] = statField
    ? rawGames
        .map(g => ({
          date: (g.date as string) || '',
          opponent: (g.opponent as string) || '',
          venue: (g.venue as string) || '',
          value: (g[statField] as number) ?? null,
          minutes: (g.minutes as number) || 0,
        }))
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
    recommendation: rec,
    reasoning: raw.tacticalBreakdown || raw.reasoning,
    confidenceLevel: raw.confidenceLevel,
    confidenceInterval: raw.confidenceInterval,
    bayesianProjection: bm.posteriorMean,
    edgeScore: bm.edgeZ,
    priorMean: bm.priorMean,
    momentumEffect: bm.momentumEffect,
    momentumLabel: bm.momentumLabel,
    streakFlag: bm.streakFlag,
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
  // Backend uses status:"live"|"settled" + result:"pending"|"won"|"lost"
  status?: string;
  result?: string;
  actualValue?: number;
  createdAt?: string;
  sport?: string;
  venue?: string;
  trackingId?: string;
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
    status: p.status as string,
    result: p.result as string,
    actualValue: p.actualValue as number,
    createdAt: (p.timestamp as string) || (p.createdAt as string),
    sport: p.sport as string,
    venue: p.venue as string,
    trackingId: p.trackingId as string,
  }));
}

export async function savePick(email: string, token: string, pick: Partial<Pick>) {
  return apiCall('/api/picks/save', {
    method: 'POST',
    body: JSON.stringify({
      email,
      token,
      pick: {
        playerName: pick.playerName,
        teamName: pick.teamName,
        opponentName: pick.opponentName,
        propType: pick.propType,
        line: pick.line,
        projectedValue: pick.projection,
        confidenceScore: pick.confidence,
        recommendation: (pick.recommendation || 'over').toLowerCase(),
        sport: pick.sport || 'soccer',
      },
    }),
  });
}

export async function deletePick(email: string, token: string, pickId: string) {
  return apiCall('/api/picks/delete', {
    method: 'POST',
    body: JSON.stringify({ email, token, pickId }),
  });
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
