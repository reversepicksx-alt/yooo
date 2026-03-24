
export interface League {
  id: number;
  name: string;
  country: string;
  logo: string;
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
  recentSamples: {
    date: string;
    opponent: string;
    value: number;
    minutesPlayed: number;
    matchDifficulty: 'low' | 'medium' | 'high';
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
}
