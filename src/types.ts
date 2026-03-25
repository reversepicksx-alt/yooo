
export interface League {
  id: number;
  name: string;
  country?: string;
  logo?: string;
  type?: string;
}

export interface Team {
  id: number;
  name: string;
  logo: string;
}

export interface Player {
  id: number;
  name: string;
  firstname: string;
  lastname: string;
  age: number;
  nationality: string;
  height: string;
  weight: string;
  photo: string;
  teamId: number;
  teamName: string;
}

export type PropType = 'pass_attempts' | 'shots' | 'saves' | 'clearances' | 'tackles';

export interface PredictionRequest {
  leagueId: number;
  playerId: number;
  playerName: string;
  teamId: number;
  opponentId: number;
  opponentName: string;
  venue: 'home' | 'away';
  propType: PropType;
  line: number;
}

export interface PredictionResponse {
  player: {
    id: number;
    name: string;
    team: string;
    role: string;
    position: string;
  };
  opponent: string;
  league: string;
  propType: PropType;
  line: number;
  projectedValue: number;
  recommendation: 'over' | 'under';
  confidenceScore: number;
  confidenceLevel: 'Low' | 'Medium' | 'High' | 'Very High';
  confidenceInterval: [number, number];
  explanation: string;
  odds?: {
    home: number;
    draw: number;
    away: number;
  };
  gameType?: 'Dominance' | 'Fragment' | 'Pressure';
  matchContext?: {
    isKnockout: boolean;
    roundName: string;
    isDerby: boolean;
    rivalryContext?: string;
  };
  roleMultipliers?: {
    role: string;
    multiplier: number;
    reason: string;
  };
  recentSamples: {
    date: string;
    opponent: string;
    value: number;
    minutesPlayed: number;
    matchDifficulty: 'low' | 'medium' | 'high';
    blockType?: string; // e.g., "Interception", "Block", "Tackle"
  }[];
  tacticalAnalysis: {
    pressingStyle: string;
    possessionImpact: string;
    spaceAndTime: string;
    opponentShotProfile?: string;
    defensiveWorkload?: string;
  };
  bayesianMetrics: {
    priorMean: number;
    momentumEffect: number;
    covariateAdjustment: number;
    reversalFlag: 'upward_reversal_likely' | 'downward_reversal_likely' | 'stable';
  };
  probabilityCurve: { value: number; probability: number }[];
  tacticalAlerts?: {
    type: 'injury' | 'lineup' | 'tactical';
    message: string;
    severity: 'low' | 'medium' | 'high';
  }[];
  correlations?: {
    player: string;
    prop: string;
    effect: string;
    impact: number; // -1 to 1
  }[];
  tacticalInsights: string;
  reasoning: string;
}

export interface SavedPick extends PredictionResponse {
  id: string;
  timestamp: number;
  status: 'live' | 'settled';
  result?: 'win' | 'loss' | 'push' | 'pending';
  actualValue?: number;
  fixtureId?: number;
  excludedSampleIndices?: number[];
  liveStats?: {
    minutes: number;
    value: number;
    onTrack: boolean;
    lastUpdated: number;
  };
}
