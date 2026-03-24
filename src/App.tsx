/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useMemo } from 'react';
import { 
  Search, 
  Zap, 
  History, 
  BarChart3, 
  ChevronRight, 
  Plus, 
  Filter, 
  TrendingUp, 
  TrendingDown, 
  Info,
  RefreshCw,
  ArrowLeft,
  CheckCircle2,
  XCircle,
  Clock,
  Activity,
  Target,
  ShieldAlert
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  SUPPORTED_LEAGUES, 
  searchPlayers, 
  getTeamsByLeague, 
  getFixtures, 
  getPlayerStats,
  getUpcomingFixtures
} from './services/apiFootball';
import { generateProjection, parseNaturalLanguageQuery } from './services/geminiService';
import { Player, Team, PredictionRequest, PredictionResponse, SavedPick, PropType } from './types';

// --- Components ---

const Badge = ({ children, variant = 'default' }: { children: React.ReactNode, variant?: 'default' | 'neon' | 'danger' | 'warning' }) => {
  const variants = {
    default: 'bg-zinc-800 text-zinc-400',
    neon: 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20',
    danger: 'bg-rose-500/10 text-rose-400 border border-rose-500/20',
    warning: 'bg-amber-500/10 text-amber-400 border border-amber-500/20',
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${variants[variant]}`}>
      {children}
    </span>
  );
};

const Card = ({ children, className = '', ...props }: { children: React.ReactNode, className?: string, [key: string]: any }) => (
  <div className={`bg-zinc-900/50 border border-zinc-800 rounded-2xl overflow-hidden backdrop-blur-sm ${className}`} {...props}>
    {children}
  </div>
);

// --- Main App ---

export default function App() {
  const [activeTab, setActiveTab] = useState<'wizard' | 'tracking'>('wizard');
  const [trackingView, setTrackingView] = useState<'live' | 'history'>('live');
  
  const [wizardStep, setWizardStep] = useState(1);
  const [wizardData, setWizardData] = useState<Partial<PredictionRequest>>({});
  const [teams, setTeams] = useState<Team[]>([]);
  
  const [projection, setProjection] = useState<PredictionResponse | null>(null);
  const [isProjecting, setIsProjecting] = useState(false);
  const [excludedSampleIndices, setExcludedSampleIndices] = useState<number[]>([]);
  
  const [savedPicks, setSavedPicks] = useState<SavedPick[]>([]);
  const [selectedPick, setSelectedPick] = useState<SavedPick | null>(null);

  // Load saved picks from local storage
  useEffect(() => {
    const saved = localStorage.getItem('reverse_picks');
    if (saved) setSavedPicks(JSON.parse(saved));
  }, []);

  useEffect(() => {
    localStorage.setItem('reverse_picks', JSON.stringify(savedPicks));
  }, [savedPicks]);

  const [isTeamsLoading, setIsTeamsLoading] = useState(false);
  const [wizardPlayers, setWizardPlayers] = useState<Player[]>([]);
  const [isWizardPlayersLoading, setIsWizardPlayersLoading] = useState(false);

  const handleWizardLeagueSelect = async (leagueId: number) => {
    setWizardData({ ...wizardData, leagueId });
    setWizardStep(2);
    setIsTeamsLoading(true);
    try {
      const leagueTeams = await getTeamsByLeague(leagueId);
      setTeams(leagueTeams);
    } finally {
      setIsTeamsLoading(false);
    }
  };

  const handleWizardPlayerSearch = async (query: string) => {
    if (query.length < 3) return;
    setIsWizardPlayersLoading(true);
    try {
      const results = await searchPlayers(query, wizardData.leagueId);
      // Filter by league if needed, but for now just show results
      setWizardPlayers(results);
    } finally {
      setIsWizardPlayersLoading(false);
    }
  };

  const handleWizardPlayerSelect = (player: Player) => {
    setWizardData({ ...wizardData, playerId: player.id, playerName: player.name, teamId: player.teamId });
    setWizardStep(3);
  };

  const handleWizardOpponentSelect = (team: Team) => {
    setWizardData({ ...wizardData, opponentId: team.id, opponentName: team.name });
    setWizardStep(4);
  };

  const runProjection = async (data: PredictionRequest) => {
    setIsProjecting(true);
    setActiveTab('wizard'); // Ensure we stay on wizard to show results
    try {
      // Fetch historical data for context
      const playerStats = await getPlayerStats(data.playerId);
      
      // If teamId is 0 (from global search fallback), extract it from playerStats
      let actualTeamId = data.teamId;
      if (actualTeamId === 0 && playerStats && playerStats.statistics && playerStats.statistics.length > 0) {
        actualTeamId = playerStats.statistics[0].team?.id || 0;
      }
      
      const teamStats = await getFixtures(actualTeamId, 30);
      const historicalData = { playerStats, teamStats };
      
      const result = await generateProjection(data, historicalData);
      setProjection(result);
      setExcludedSampleIndices([]);
    } catch (error) {
      console.error(error);
    } finally {
      setIsProjecting(false);
    }
  };

  const savePick = () => {
    if (!projection) return;
    const newPick: SavedPick = {
      ...projection,
      id: Math.random().toString(36).substr(2, 9),
      timestamp: Date.now(),
      status: 'live',
      result: 'pending',
      excludedSampleIndices
    };
    setSavedPicks([newPick, ...savedPicks]);
    setProjection(null);
    setExcludedSampleIndices([]);
    setWizardStep(1);
    setWizardData({});
    setActiveTab('tracking');
  };

  return (
    <div className="min-h-screen bg-black text-zinc-100 font-sans selection:bg-emerald-500/30">
      {/* Header */}
      <header className="sticky top-0 z-50 bg-black/80 backdrop-blur-xl border-b border-zinc-800 px-6 pt-[calc(1rem+env(safe-area-inset-top))] pb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 bg-emerald-500 rounded-lg flex items-center justify-center shadow-[0_0_15px_rgba(16,185,129,0.4)]">
            <Zap className="w-5 h-5 text-black fill-current" />
          </div>
          <h1 className="text-xl font-black tracking-tighter uppercase italic">
            Reverse<span className="text-emerald-400">Picks</span>
          </h1>
        </div>
        <div className="flex items-center gap-4">
          <Badge variant="neon">v1.2.4-BETA</Badge>
          <button 
            onClick={() => window.location.reload()}
            className="p-2 hover:bg-zinc-800 rounded-full transition-colors"
          >
            <RefreshCw className="w-5 h-5 text-zinc-400" />
          </button>
        </div>
      </header>

      <main className="pb-32 pt-6 px-6 max-w-md mx-auto">
        <AnimatePresence mode="wait">
          {activeTab === 'wizard' && (
            <motion.div
              key="wizard"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              className="space-y-6"
            >
              {!projection && !isProjecting && (
                <>
                  <div className="flex items-center justify-between">
                    <div className="space-y-1">
                      <h2 className="text-2xl font-bold tracking-tight">Predict</h2>
                      <p className="text-zinc-500 text-sm">Step {wizardStep} of 6</p>
                    </div>
                    {wizardStep > 1 && (
                      <button onClick={() => setWizardStep(wizardStep - 1)} className="text-zinc-400 text-sm flex items-center gap-1">
                        <ArrowLeft className="w-4 h-4" /> Back
                      </button>
                    )}
                  </div>

                  {wizardStep === 1 && (
                    <div className="grid grid-cols-1 gap-3">
                      {SUPPORTED_LEAGUES.map((league) => (
                        <Card key={league.id} className="p-4 hover:bg-zinc-800/50 cursor-pointer transition-colors" onClick={() => handleWizardLeagueSelect(league.id)}>
                          <div className="flex items-center justify-between">
                            <span className="font-bold">{league.name}</span>
                            <ChevronRight className="w-4 h-4 text-zinc-600" />
                          </div>
                        </Card>
                      ))}
                    </div>
                  )}

                  {wizardStep === 2 && (
                    <div className="space-y-4">
                      <div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
                        <input 
                          type="text" 
                          placeholder="Search player name..." 
                          className="w-full bg-zinc-900 border border-zinc-800 rounded-xl py-3 pl-10 pr-4 focus:outline-none focus:border-emerald-500/50"
                          onChange={(e) => handleWizardPlayerSearch(e.target.value)}
                        />
                        {isWizardPlayersLoading && (
                          <RefreshCw className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-emerald-400 animate-spin" />
                        )}
                      </div>
                      
                      <div className="space-y-2 max-h-[400px] overflow-y-auto pr-1">
                        {wizardPlayers.map((player) => (
                          <Card key={player.id} className="p-3 hover:bg-zinc-800/50 cursor-pointer transition-colors" onClick={() => handleWizardPlayerSelect(player)}>
                            <div className="flex items-center gap-3">
                              <img src={player.photo} alt={player.name} className="w-8 h-8 rounded-full" referrerPolicy="no-referrer" />
                              <div>
                                <div className="text-sm font-bold">{player.name}</div>
                                <div className="text-[10px] text-zinc-500">{player.teamName}</div>
                              </div>
                            </div>
                          </Card>
                        ))}
                        {wizardPlayers.length === 0 && !isWizardPlayersLoading && (
                          <div className="text-center py-8 text-zinc-500 text-sm">
                            Type at least 3 characters to search players in this league.
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {wizardStep === 3 && (
                    <div className="space-y-4">
                      <div className="text-xs font-bold text-zinc-500 uppercase tracking-widest">Select Opponent</div>
                      {isTeamsLoading ? (
                        <div className="flex justify-center py-12">
                          <RefreshCw className="w-8 h-8 text-emerald-500 animate-spin" />
                        </div>
                      ) : teams.length > 0 ? (
                        <div className="grid grid-cols-2 gap-3">
                          {teams.map((team) => (
                            <Card key={team.id} className="p-4 flex flex-col items-center gap-3 hover:bg-zinc-800/50 cursor-pointer transition-colors" onClick={() => handleWizardOpponentSelect(team)}>
                              <img src={team.logo} alt={team.name} className="w-12 h-12 object-contain" referrerPolicy="no-referrer" />
                              <span className="text-xs font-bold text-center">{team.name}</span>
                            </Card>
                          ))}
                        </div>
                      ) : (
                        <div className="text-center py-8 text-zinc-500 text-sm">
                          No teams found for this player's league. Please go back and select a league manually.
                          <button onClick={() => setWizardStep(1)} className="mt-4 px-4 py-2 bg-zinc-800 rounded-lg text-white hover:bg-zinc-700 block mx-auto">
                            Go to League Selection
                          </button>
                        </div>
                      )}
                    </div>
                  )}

                  {wizardStep === 4 && (
                    <div className="grid grid-cols-2 gap-4">
                      <button onClick={() => { setWizardData({ ...wizardData, venue: 'home' }); setWizardStep(5); }} className={`p-6 rounded-2xl border-2 transition-all ${wizardData.venue === 'home' ? 'border-emerald-500 bg-emerald-500/10' : 'border-zinc-800 bg-zinc-900'}`}>
                        <span className="block text-lg font-bold">Home</span>
                        <span className="text-xs text-zinc-500 uppercase">Venue</span>
                      </button>
                      <button onClick={() => { setWizardData({ ...wizardData, venue: 'away' }); setWizardStep(5); }} className={`p-6 rounded-2xl border-2 transition-all ${wizardData.venue === 'away' ? 'border-emerald-500 bg-emerald-500/10' : 'border-zinc-800 bg-zinc-900'}`}>
                        <span className="block text-lg font-bold">Away</span>
                        <span className="text-xs text-zinc-500 uppercase">Venue</span>
                      </button>
                    </div>
                  )}

                  {wizardStep === 5 && (
                    <div className="space-y-3">
                      {(['pass_attempts', 'shots', 'saves', 'clearances', 'tackles'] as PropType[]).map((type) => (
                        <Card key={type} className={`p-4 cursor-pointer transition-all ${wizardData.propType === type ? 'border-emerald-500 bg-emerald-500/10' : ''}`} onClick={() => { setWizardData({ ...wizardData, propType: type }); setWizardStep(6); }}>
                          <div className="flex items-center justify-between">
                            <span className="font-bold capitalize">{type.replace('_', ' ')}</span>
                            <ChevronRight className="w-4 h-4 text-zinc-600" />
                          </div>
                        </Card>
                      ))}
                    </div>
                  )}

                  {wizardStep === 6 && (
                    <div className="space-y-6">
                      <div className="text-center space-y-2">
                        <label className="text-zinc-500 text-xs uppercase font-bold tracking-widest">Set Prop Line</label>
                        <div className="flex items-center justify-center gap-6">
                          <button onClick={() => setWizardData({ ...wizardData, line: Math.max(0, (wizardData.line || 0) - 0.5) })} className="w-12 h-12 rounded-full bg-zinc-900 border border-zinc-800 flex items-center justify-center text-2xl font-bold">-</button>
                          <input 
                            type="number" 
                            step="0.5" 
                            placeholder="0.0"
                            value={wizardData.line === 0 ? '' : wizardData.line} 
                            onChange={(e) => setWizardData({ ...wizardData, line: parseFloat(e.target.value) || 0 })}
                            className="text-5xl font-black text-emerald-400 bg-transparent text-center w-32 outline-none appearance-none"
                            style={{ WebkitAppearance: 'none', MozAppearance: 'textfield' }}
                          />
                          <button onClick={() => setWizardData({ ...wizardData, line: (wizardData.line || 0) + 0.5 })} className="w-12 h-12 rounded-full bg-zinc-900 border border-zinc-800 flex items-center justify-center text-2xl font-bold">+</button>
                        </div>
                      </div>
                      <button 
                        onClick={() => runProjection(wizardData as PredictionRequest)}
                        className="w-full bg-emerald-500 hover:bg-emerald-400 text-black font-black py-5 rounded-2xl shadow-[0_0_30px_rgba(16,185,129,0.3)] transition-all flex items-center justify-center gap-2"
                      >
                        <Zap className="w-5 h-5 fill-current" />
                        GENERATE PROJECTION
                      </button>
                    </div>
                  )}
                </>
              )}

              {isProjecting && (
                <div className="flex flex-col items-center justify-center py-24 space-y-6">
                  <div className="relative">
                    <div className="w-20 h-20 border-4 border-emerald-500/20 border-t-emerald-500 rounded-full animate-spin" />
                    <Zap className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-8 h-8 text-emerald-400 animate-pulse" />
                  </div>
                  <div className="text-center space-y-2">
                    <h3 className="text-xl font-bold">Analyzing Matchup...</h3>
                    <p className="text-zinc-500 text-sm max-w-[200px]">Running data-driven projections for {wizardData.playerName}</p>
                  </div>
                </div>
              )}

              {projection && (
                <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="space-y-6">
                  <Card className="p-6 border-emerald-500/50 bg-emerald-500/5">
                    <div className="flex justify-between items-start mb-6">
                      <div>
                        <Badge variant="neon">Projection Ready</Badge>
                        <h3 className="text-3xl font-black mt-2 tracking-tighter">{projection.player.name}</h3>
                        <p className="text-zinc-400 text-sm">{projection.player.team} vs {projection.opponent}</p>
                      </div>
                      <div className="text-right">
                        <div className="text-xs text-zinc-500 uppercase font-bold">Confidence</div>
                        <div className="text-2xl font-black text-emerald-400">{projection.confidenceScore}%</div>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4 mb-8">
                      <div className="bg-zinc-900/80 p-4 rounded-xl border border-zinc-800 relative group cursor-text">
                        <div className="text-[10px] text-zinc-500 uppercase font-bold mb-1">Prop Line</div>
                        <div className="flex items-baseline gap-1">
                          <input 
                            type="number" 
                            step="0.5"
                            value={projection.line}
                            onChange={(e) => {
                              const newLine = parseFloat(e.target.value);
                              if (!isNaN(newLine)) {
                                setProjection({
                                  ...projection,
                                  line: newLine,
                                  recommendation: projection.projectedValue > newLine ? 'over' : 'under'
                                });
                              }
                            }}
                            className="text-2xl font-black bg-transparent border-none outline-none w-16 p-0 focus:ring-0 text-white"
                          />
                          <span className="text-sm text-zinc-500 font-medium capitalize">{projection.propType}</span>
                        </div>
                        <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
                          <span className="text-[8px] text-zinc-500 uppercase bg-zinc-800 px-1.5 py-0.5 rounded">Edit</span>
                        </div>
                      </div>
                      <div className="bg-zinc-900/80 p-4 rounded-xl border border-zinc-800">
                        <div className="text-[10px] text-zinc-500 uppercase font-bold mb-1">Projected</div>
                        <div className="text-2xl font-black text-emerald-400">{projection.projectedValue}</div>
                      </div>
                    </div>

                    <div className={`p-4 rounded-xl flex items-center justify-between mb-8 ${projection.recommendation === 'over' ? 'bg-emerald-500/20 border border-emerald-500/30' : 'bg-rose-500/20 border border-rose-500/30'}`}>
                      <div className="flex items-center gap-3">
                        {projection.recommendation === 'over' ? <TrendingUp className="w-6 h-6 text-emerald-400" /> : <TrendingDown className="w-6 h-6 text-rose-400" />}
                        <span className="font-black text-xl uppercase tracking-tight">Recommend: {projection.recommendation}</span>
                      </div>
                      <Badge variant={projection.recommendation === 'over' ? 'neon' : 'danger'}>{projection.confidenceLevel}</Badge>
                    </div>

                    {projection.recentSamples && projection.recentSamples.length > 0 && (
                      <div className="mb-8">
                        <div className="flex items-center justify-between mb-3">
                          <h4 className="text-xs font-bold text-zinc-500 uppercase tracking-widest flex items-center gap-2">
                            <Activity className="w-3 h-3" /> Recent Form
                          </h4>
                          <div className="text-[10px] font-bold text-zinc-400 bg-zinc-900 px-2 py-1 rounded-md border border-zinc-800">
                            {projection.recentSamples.filter((s, idx) => !excludedSampleIndices.includes(idx) && (projection.recommendation === 'over' ? s.value > projection.line : s.value < projection.line)).length} / {projection.recentSamples.filter((_, idx) => !excludedSampleIndices.includes(idx)).length} HIT RATE
                          </div>
                        </div>
                        <div className="grid grid-cols-5 gap-2">
                          {projection.recentSamples.map((sample, idx) => {
                            const isExcluded = excludedSampleIndices.includes(idx);
                            const isHit = projection.recommendation === 'over' ? sample.value > projection.line : sample.value < projection.line;
                            const isPush = sample.value === projection.line;
                            const diffColor = sample.matchDifficulty === 'high' ? 'bg-rose-500' : sample.matchDifficulty === 'medium' ? 'bg-amber-500' : 'bg-emerald-500';
                            
                            return (
                              <div 
                                key={idx} 
                                onClick={() => {
                                  if (isExcluded) {
                                    setExcludedSampleIndices(excludedSampleIndices.filter(i => i !== idx));
                                  } else {
                                    setExcludedSampleIndices([...excludedSampleIndices, idx]);
                                  }
                                }}
                                className={`relative p-2 rounded-lg border transition-all hover:scale-105 active:scale-95 cursor-pointer flex flex-col items-center justify-between min-h-[60px] ${
                                  isExcluded ? 'bg-zinc-900/50 border-zinc-800 text-zinc-600 opacity-50' :
                                  isPush ? 'bg-zinc-800 border-zinc-700 text-zinc-300' :
                                  isHit ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400' : 'bg-rose-500/10 border-rose-500/30 text-rose-400'
                                }`}
                                title={`${sample.date} vs ${sample.opponent} (Difficulty: ${sample.matchDifficulty})`}
                              >
                                <div className={`absolute -top-1 -right-1 w-2 h-2 rounded-full ${diffColor} shadow-[0_0_5px_rgba(0,0,0,0.5)]`} />
                                <div className="flex flex-col items-center">
                                  <span className="text-xs font-black leading-none">{sample.value}</span>
                                  <span className="text-[8px] font-bold opacity-60 mt-0.5">{sample.minutesPlayed}'</span>
                                </div>
                                <span className="text-[7px] font-bold uppercase opacity-60 truncate w-full text-center mt-1">{sample.opponent.substring(0, 3)}</span>
                              </div>
                            );
                          })}
                        </div>
                        <p className="text-[9px] text-zinc-500 mt-3 italic text-center">Tiles show opponent difficulty (Red=Hard, Yellow=Med, Green=Easy). Tap to exclude.</p>
                      </div>
                    )}

                    <div className="space-y-4">
                      <div className="grid grid-cols-2 gap-3">
                        <div className="bg-zinc-900/50 p-3 rounded-xl border border-zinc-800">
                          <div className="text-[9px] text-zinc-500 uppercase font-bold mb-1">Position</div>
                          <div className="text-sm font-bold text-white">{projection.player.position}</div>
                        </div>
                        <div className="bg-zinc-900/50 p-3 rounded-xl border border-zinc-800">
                          <div className="text-[9px] text-zinc-500 uppercase font-bold mb-1">Tactical Role</div>
                          <div className="text-sm font-bold text-emerald-400">{projection.player.role}</div>
                        </div>
                      </div>

                      <div className="bg-zinc-900/80 p-4 rounded-xl border border-zinc-800 space-y-3">
                        <div className="text-[10px] text-zinc-500 uppercase font-bold flex items-center gap-2">
                          <Zap className="w-3 h-3 text-emerald-400" /> Bayesian Model Metrics
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                          <div>
                            <div className="text-[8px] text-zinc-500 uppercase font-bold">Prior Mean</div>
                            <div className="text-sm font-bold text-white">{projection.bayesianMetrics.priorMean}</div>
                          </div>
                          <div>
                            <div className="text-[8px] text-zinc-500 uppercase font-bold">Momentum</div>
                            <div className="text-sm font-bold text-emerald-400">{projection.bayesianMetrics.momentumEffect > 0 ? '+' : ''}{projection.bayesianMetrics.momentumEffect}</div>
                          </div>
                          <div>
                            <div className="text-[8px] text-zinc-500 uppercase font-bold">Covariates</div>
                            <div className="text-sm font-bold text-white">{projection.bayesianMetrics.covariateAdjustment > 0 ? '+' : ''}{projection.bayesianMetrics.covariateAdjustment}</div>
                          </div>
                          <div>
                            <div className="text-[8px] text-zinc-500 uppercase font-bold">Reversal</div>
                            <div className="text-[10px] font-bold text-zinc-300 capitalize">{projection.bayesianMetrics.reversalFlag.replace(/_/g, ' ')}</div>
                          </div>
                        </div>
                      </div>

                      <div className="space-y-3 pt-2">
                        <h4 className="text-xs font-bold text-zinc-500 uppercase tracking-widest flex items-center gap-2">
                          <ShieldAlert className="w-3 h-3" /> Tactical Analysis
                        </h4>
                        
                        <div className="space-y-3">
                          <div className="bg-zinc-900/30 p-3 rounded-xl border border-zinc-800/50">
                            <div className="text-[10px] font-bold text-zinc-400 mb-1 flex items-center gap-1.5">
                              <Target className="w-3 h-3" /> Pressing & Space
                            </div>
                            <p className="text-xs text-zinc-300 leading-relaxed">{projection.tacticalAnalysis.pressingStyle}</p>
                            <p className="text-xs text-zinc-300 leading-relaxed mt-2">{projection.tacticalAnalysis.spaceAndTime}</p>
                          </div>

                          <div className="bg-zinc-900/30 p-3 rounded-xl border border-zinc-800/50">
                            <div className="text-[10px] font-bold text-zinc-400 mb-1 flex items-center gap-1.5">
                              <BarChart3 className="w-3 h-3" /> Possession & Matchup
                            </div>
                            <p className="text-xs text-zinc-300 leading-relaxed">{projection.tacticalAnalysis.possessionImpact}</p>
                          </div>

                          {projection.propType === 'saves' && projection.tacticalAnalysis.opponentShotProfile && (
                            <div className="bg-zinc-900/30 p-3 rounded-xl border border-zinc-800/50">
                              <div className="text-[10px] font-bold text-zinc-400 mb-1 flex items-center gap-1.5">
                                <Zap className="w-3 h-3" /> Shot Profile
                              </div>
                              <p className="text-xs text-zinc-300 leading-relaxed">{projection.tacticalAnalysis.opponentShotProfile}</p>
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="space-y-2 pt-2">
                        <h4 className="text-xs font-bold text-zinc-500 uppercase tracking-widest flex items-center gap-2">
                          <BarChart3 className="w-3 h-3" /> Model Reasoning
                        </h4>
                        <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">{projection.reasoning}</p>
                      </div>
                    </div>
                  </Card>

                  <div className="flex gap-4">
                    <button onClick={() => setProjection(null)} className="flex-1 bg-zinc-900 border border-zinc-800 py-4 rounded-2xl font-bold hover:bg-zinc-800 transition-colors">Discard</button>
                    <button onClick={savePick} className="flex-[2] bg-emerald-500 text-black py-4 rounded-2xl font-black shadow-[0_0_20px_rgba(16,185,129,0.2)] hover:bg-emerald-400 transition-colors">SAVE TO TRACKING</button>
                  </div>
                </motion.div>
              )}
            </motion.div>
          )}

          {activeTab === 'tracking' && (
            <motion.div
              key="tracking"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              className="space-y-6"
            >
              <div className="flex items-center justify-between">
                <h2 className="text-2xl font-bold tracking-tight">Tracking</h2>
                <div className="flex bg-zinc-900 p-1 rounded-lg border border-zinc-800">
                  <button 
                    onClick={() => setTrackingView('live')}
                    className={`px-4 py-1.5 rounded-md text-xs font-bold transition-all ${trackingView === 'live' ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}
                  >
                    Live
                  </button>
                  <button 
                    onClick={() => setTrackingView('history')}
                    className={`px-4 py-1.5 rounded-md text-xs font-bold transition-all ${trackingView === 'history' ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}
                  >
                    History
                  </button>
                </div>
              </div>

              <div className="space-y-4">
                {savedPicks.filter(p => trackingView === 'live' ? p.status === 'live' : p.status === 'settled').length === 0 ? (
                  <div className="text-center py-24 space-y-4">
                    <div className="w-16 h-16 bg-zinc-900 rounded-full flex items-center justify-center mx-auto border border-zinc-800">
                      <Clock className="w-8 h-8 text-zinc-700" />
                    </div>
                    <p className="text-zinc-500 text-sm">No {trackingView} picks being tracked.</p>
                    {trackingView === 'live' && <button onClick={() => setActiveTab('wizard')} className="text-emerald-400 text-sm font-bold">Start a new query</button>}
                  </div>
                ) : (
                  savedPicks.filter(p => trackingView === 'live' ? p.status === 'live' : p.status === 'settled').map((pick) => (
                    <Card key={pick.id} className="p-4 relative group cursor-pointer hover:border-zinc-700 transition-colors" onClick={() => setSelectedPick(pick)}>
                      <div className="flex justify-between items-start mb-3">
                        <div className="flex items-center gap-2">
                          <div className={`w-2 h-2 rounded-full ${pick.status === 'live' ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-600'}`} />
                          <span className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest">{pick.status}</span>
                        </div>
                        <div className="flex items-center gap-1.5">
                          {pick.status === 'settled' && (
                            <Badge variant={pick.result === 'win' ? 'neon' : 'danger'}>{pick.result}</Badge>
                          )}
                          <div className={`px-2 py-0.5 rounded-md text-[8px] font-black uppercase tracking-tighter ${pick.recommendation === 'over' ? 'bg-emerald-500 text-black' : 'bg-rose-500 text-white'}`}>
                            {pick.recommendation}
                          </div>
                        </div>
                      </div>
                      
                      <div className="flex justify-between items-start mb-4">
                        <div>
                          <h4 className="font-black text-base leading-tight">{pick.player.name}</h4>
                          <p className="text-[10px] text-zinc-500 mt-0.5">{pick.player.team} vs {pick.opponent}</p>
                        </div>
                        <div className="text-right">
                          <div className="text-[8px] text-zinc-500 uppercase font-bold">Line</div>
                          <div className="text-lg font-black">{pick.line}</div>
                        </div>
                      </div>

                      <div className="grid grid-cols-4 gap-2 mb-4">
                        <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/50">
                          <div className="text-[7px] text-zinc-500 uppercase font-bold">Proj</div>
                          <div className="text-xs font-black text-emerald-400">{pick.projectedValue}</div>
                        </div>
                        <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/50">
                          <div className="text-[7px] text-zinc-500 uppercase font-bold">Now</div>
                          <div className="text-xs font-black text-white">{pick.actualValue || 0}</div>
                        </div>
                        <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/50">
                          <div className="text-[7px] text-zinc-500 uppercase font-bold">Pace</div>
                          <div className="text-xs font-black text-zinc-400">{(pick.actualValue || 0) > 0 ? ((pick.actualValue || 0) * 1.2).toFixed(1) : '-'}</div>
                        </div>
                        <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/50">
                          <div className="text-[7px] text-zinc-500 uppercase font-bold">Hit Rate</div>
                          <div className="text-xs font-black text-amber-400">
                            {Math.round((pick.recentSamples.filter(s => pick.recommendation === 'over' ? s.value > pick.line : s.value < pick.line).length / pick.recentSamples.length) * 100)}%
                          </div>
                        </div>
                      </div>

                      <div className="pt-3 border-t border-zinc-800 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <BarChart3 className="w-3 h-3 text-zinc-600" />
                          <span className="text-[9px] text-zinc-600 font-mono">ID: {pick.player.id}</span>
                        </div>
                        <div className="text-[9px] text-zinc-600">
                          {new Date(pick.timestamp).toLocaleDateString()}
                        </div>
                      </div>
                    </Card>
                  ))
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      {/* Selected Pick Detail Modal */}
      <AnimatePresence>
        {selectedPick && (
          <motion.div 
            initial={{ opacity: 0 }} 
            animate={{ opacity: 1 }} 
            exit={{ opacity: 0 }} 
            className="fixed inset-0 z-[100] bg-black/90 backdrop-blur-md p-6 overflow-y-auto"
          >
            <div className="max-w-md mx-auto space-y-8">
              <button onClick={() => setSelectedPick(null)} className="flex items-center gap-2 text-zinc-400 hover:text-white transition-colors">
                <ArrowLeft className="w-5 h-5" /> Back to Tracking
              </button>

              <div className="space-y-6">
                <div className="flex justify-between items-start">
                  <div>
                    <Badge variant="neon">Analysis Detail</Badge>
                    <h2 className="text-4xl font-black mt-2 tracking-tighter">{selectedPick.player.name}</h2>
                    <p className="text-zinc-400">{selectedPick.player.team} vs {selectedPick.opponent}</p>
                  </div>
                  <div className="text-right">
                    <div className="text-xs text-zinc-500 uppercase font-bold">Status</div>
                    <div className="text-xl font-black text-emerald-400 uppercase">{selectedPick.status}</div>
                    {selectedPick.status === 'settled' && (
                      <div className={`text-sm font-bold uppercase mt-1 ${selectedPick.result === 'won' ? 'text-emerald-500' : 'text-rose-500'}`}>
                        {selectedPick.result}
                      </div>
                    )}
                  </div>
                </div>

                <Card className="p-6 space-y-6">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="bg-zinc-900/80 p-4 rounded-xl border border-zinc-800">
                      <div className="text-[10px] text-zinc-500 uppercase font-bold mb-1">Prop Line</div>
                      <div className="text-2xl font-black">{selectedPick.line} <span className="text-xs text-zinc-500 capitalize">{selectedPick.propType}</span></div>
                    </div>
                    <div className="bg-zinc-900/80 p-4 rounded-xl border border-zinc-800">
                      <div className="text-[10px] text-zinc-500 uppercase font-bold mb-1">Projected</div>
                      <div className="text-2xl font-black text-emerald-400">{selectedPick.projectedValue}</div>
                    </div>
                  </div>

                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="bg-zinc-900/50 p-3 rounded-xl border border-zinc-800">
                        <div className="text-[9px] text-zinc-500 uppercase font-bold mb-1">Position</div>
                        <div className="text-sm font-bold text-white">{selectedPick.player.position}</div>
                      </div>
                      <div className="bg-zinc-900/50 p-3 rounded-xl border border-zinc-800">
                        <div className="text-[9px] text-zinc-500 uppercase font-bold mb-1">Tactical Role</div>
                        <div className="text-sm font-bold text-emerald-400">{selectedPick.player.role}</div>
                      </div>
                    </div>

                    {selectedPick.bayesianMetrics && (
                      <div className="bg-zinc-900/80 p-4 rounded-xl border border-zinc-800 space-y-3">
                        <div className="text-[10px] text-zinc-500 uppercase font-bold flex items-center gap-2">
                          <Zap className="w-3 h-3 text-emerald-400" /> Bayesian Model Metrics
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                          <div>
                            <div className="text-[8px] text-zinc-500 uppercase font-bold">Prior Mean</div>
                            <div className="text-sm font-bold text-white">{selectedPick.bayesianMetrics.priorMean}</div>
                          </div>
                          <div>
                            <div className="text-[8px] text-zinc-500 uppercase font-bold">Momentum</div>
                            <div className="text-sm font-bold text-emerald-400">{selectedPick.bayesianMetrics.momentumEffect > 0 ? '+' : ''}{selectedPick.bayesianMetrics.momentumEffect}</div>
                          </div>
                          <div>
                            <div className="text-[8px] text-zinc-500 uppercase font-bold">Covariates</div>
                            <div className="text-sm font-bold text-white">{selectedPick.bayesianMetrics.covariateAdjustment > 0 ? '+' : ''}{selectedPick.bayesianMetrics.covariateAdjustment}</div>
                          </div>
                          <div>
                            <div className="text-[8px] text-zinc-500 uppercase font-bold">Reversal</div>
                            <div className="text-[10px] font-bold text-zinc-300 capitalize">{selectedPick.bayesianMetrics.reversalFlag.replace(/_/g, ' ')}</div>
                          </div>
                        </div>
                      </div>
                    )}

                    <div className="space-y-3 pt-2">
                      <h4 className="text-xs font-bold text-zinc-500 uppercase tracking-widest flex items-center gap-2">
                        <ShieldAlert className="w-3 h-3" /> Tactical Analysis
                      </h4>
                      
                      <div className="space-y-3">
                        <div className="bg-zinc-900/30 p-3 rounded-xl border border-zinc-800/50">
                          <div className="text-[10px] font-bold text-zinc-400 mb-1 flex items-center gap-1.5">
                            <Target className="w-3 h-3" /> Pressing & Space
                          </div>
                          <p className="text-xs text-zinc-300 leading-relaxed">{selectedPick.tacticalAnalysis.pressingStyle}</p>
                          <p className="text-xs text-zinc-300 leading-relaxed mt-2">{selectedPick.tacticalAnalysis.spaceAndTime}</p>
                        </div>

                        <div className="bg-zinc-900/30 p-3 rounded-xl border border-zinc-800/50">
                          <div className="text-[10px] font-bold text-zinc-400 mb-1 flex items-center gap-1.5">
                            <BarChart3 className="w-3 h-3" /> Possession & Matchup
                          </div>
                          <p className="text-xs text-zinc-300 leading-relaxed">{selectedPick.tacticalAnalysis.possessionImpact}</p>
                        </div>

                        {selectedPick.propType === 'saves' && selectedPick.tacticalAnalysis.opponentShotProfile && (
                          <div className="bg-zinc-900/30 p-3 rounded-xl border border-zinc-800/50">
                            <div className="text-[10px] font-bold text-zinc-400 mb-1 flex items-center gap-1.5">
                              <Zap className="w-3 h-3" /> Shot Profile
                            </div>
                            <p className="text-xs text-zinc-300 leading-relaxed">{selectedPick.tacticalAnalysis.opponentShotProfile}</p>
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="space-y-2 pt-2">
                      <h4 className="text-xs font-bold text-zinc-500 uppercase tracking-widest flex items-center gap-2">
                        <BarChart3 className="w-3 h-3" /> Model Reasoning
                      </h4>
                      <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">{selectedPick.reasoning}</p>
                    </div>
                    
                    <div className="pt-4 border-t border-zinc-800">
                      <div className="flex items-center justify-between mb-3">
                        <h4 className="text-xs font-bold text-zinc-500 uppercase tracking-widest flex items-center gap-2">
                          <Activity className="w-3 h-3" /> Recent Form
                        </h4>
                        <div className="text-[10px] font-bold text-zinc-400 bg-zinc-900 px-2 py-1 rounded-md border border-zinc-800">
                          {selectedPick.recentSamples.filter((s, idx) => !(selectedPick.excludedSampleIndices || []).includes(idx) && (selectedPick.recommendation === 'over' ? s.value > selectedPick.line : s.value < selectedPick.line)).length} / {selectedPick.recentSamples.filter((_, idx) => !(selectedPick.excludedSampleIndices || []).includes(idx)).length} HIT RATE
                        </div>
                      </div>
                      <div className="grid grid-cols-5 gap-2">
                        {selectedPick.recentSamples.map((sample, idx) => {
                          const isExcluded = (selectedPick.excludedSampleIndices || []).includes(idx);
                          const isHit = selectedPick.recommendation === 'over' ? sample.value > selectedPick.line : sample.value < selectedPick.line;
                          const isPush = sample.value === selectedPick.line;
                          const diffColor = sample.matchDifficulty === 'high' ? 'bg-rose-500' : sample.matchDifficulty === 'medium' ? 'bg-amber-500' : 'bg-emerald-500';
                          
                          return (
                            <div 
                              key={idx} 
                              onClick={() => {
                                const currentExcluded = selectedPick.excludedSampleIndices || [];
                                const newExcluded = isExcluded 
                                  ? currentExcluded.filter(i => i !== idx)
                                  : [...currentExcluded, idx];
                                
                                const updatedPick = { ...selectedPick, excludedSampleIndices: newExcluded };
                                setSelectedPick(updatedPick);
                                setSavedPicks(savedPicks.map(p => p.id === updatedPick.id ? updatedPick : p));
                              }}
                              className={`relative p-2 rounded-lg border transition-all hover:scale-105 active:scale-95 cursor-pointer flex flex-col items-center justify-between min-h-[60px] ${
                                isExcluded ? 'bg-zinc-900/50 border-zinc-800 text-zinc-600 opacity-50' :
                                isPush ? 'bg-zinc-800 border-zinc-700 text-zinc-300' :
                                isHit ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400' : 'bg-rose-500/10 border-rose-500/30 text-rose-400'
                              }`}
                              title={`${sample.date} vs ${sample.opponent} (Difficulty: ${sample.matchDifficulty})`}
                            >
                              <div className={`absolute -top-1 -right-1 w-2 h-2 rounded-full ${diffColor} shadow-[0_0_5px_rgba(0,0,0,0.5)]`} />
                              <div className="flex flex-col items-center">
                                <span className="text-xs font-black leading-none">{sample.value}</span>
                                <span className="text-[8px] font-bold opacity-60 mt-0.5">{sample.minutesPlayed}'</span>
                              </div>
                              <span className="text-[7px] font-bold uppercase opacity-60 truncate w-full text-center mt-1">{sample.opponent.substring(0, 3)}</span>
                            </div>
                          );
                        })}
                      </div>
                      <p className="text-[9px] text-zinc-500 mt-3 italic text-center">Tiles show opponent difficulty (Red=Hard, Yellow=Med, Green=Easy). Tap to exclude.</p>
                    </div>
                  </div>
                </Card>

                <div className="space-y-3">
                  {selectedPick.status === 'live' && (
                    <div className="grid grid-cols-2 gap-3">
                      <button 
                        onClick={() => {
                          const updatedPick = { ...selectedPick, status: 'settled' as const, result: 'win' as const };
                          setSelectedPick(updatedPick);
                          setSavedPicks(savedPicks.map(p => p.id === updatedPick.id ? updatedPick : p));
                        }}
                        className="w-full py-4 bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 font-bold hover:bg-emerald-500/20 rounded-2xl transition-all"
                      >
                        Mark as Won
                      </button>
                      <button 
                        onClick={() => {
                          const updatedPick = { ...selectedPick, status: 'settled' as const, result: 'loss' as const };
                          setSelectedPick(updatedPick);
                          setSavedPicks(savedPicks.map(p => p.id === updatedPick.id ? updatedPick : p));
                        }}
                        className="w-full py-4 bg-rose-500/10 text-rose-500 border border-rose-500/20 font-bold hover:bg-rose-500/20 rounded-2xl transition-all"
                      >
                        Mark as Lost
                      </button>
                    </div>
                  )}
                  <button 
                    onClick={() => {
                      setSavedPicks(savedPicks.filter(p => p.id !== selectedPick.id));
                      setSelectedPick(null);
                    }}
                    className="w-full py-4 text-rose-500 font-bold hover:bg-rose-500/10 rounded-2xl transition-all"
                  >
                    Delete Pick from Tracking
                  </button>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Navigation */}
      <nav className="fixed bottom-0 left-0 right-0 bg-black/80 backdrop-blur-xl border-t border-zinc-800 px-8 pt-6 pb-[calc(1.5rem+env(safe-area-bottom))] z-50">
        <div className="max-w-md mx-auto flex items-center justify-around">
          <button 
            onClick={() => { setActiveTab('wizard'); setWizardStep(1); setProjection(null); }}
            className={`flex flex-col items-center gap-1 transition-all ${activeTab === 'wizard' ? 'text-emerald-400 scale-110' : 'text-zinc-500 hover:text-zinc-300'}`}
          >
            <div className={`p-3 rounded-2xl -mt-10 mb-1 shadow-lg transition-all ${activeTab === 'wizard' ? 'bg-emerald-500 text-black shadow-emerald-500/20' : 'bg-zinc-800 text-zinc-400'}`}>
              <Plus className="w-7 h-7" />
            </div>
            <span className="text-[10px] font-bold uppercase tracking-widest">Predict</span>
          </button>

          <button 
            onClick={() => setActiveTab('tracking')}
            className={`flex flex-col items-center gap-1 transition-all ${activeTab === 'tracking' ? 'text-emerald-400 scale-110' : 'text-zinc-500 hover:text-zinc-300'}`}
          >
            <History className="w-6 h-6" />
            <span className="text-[10px] font-bold uppercase tracking-widest">Tracking</span>
          </button>
        </div>
      </nav>
    </div>
  );
}
