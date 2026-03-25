/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useMemo, useRef } from 'react';
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
  ShieldAlert,
  User,
  Shield,
  MessageSquare,
  Send,
  Loader2,
  Trash2,
  Brain
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  LineChart, 
  Line, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer,
  AreaChart,
  Area
} from 'recharts';
import { 
  SUPPORTED_LEAGUES, 
  searchPlayers, 
  getTeamsByLeague, 
  getFixtures, 
  getPlayerStats,
  getUpcomingFixtures,
  getRecentPlayerMatchHistory,
  getTeamStats,
  getH2H,
  getStandings,
  getOdds,
  getLivePlayerStats,
  getFixtureLineups,
  checkApiStatus
} from './services/apiFootball';
import { 
  generateProjection, 
  parseNaturalLanguageQuery, 
  startTacticalChat,
  getMarketSentiment
} from './services/geminiService';
import { Player, Team, PredictionRequest, PredictionResponse, SavedPick, PropType } from './types';
import { LoginPage } from './components/LoginPage';
import { AdminPanel } from './components/AdminPanel';

// --- Components ---

const ProbabilityChart = ({ data, projectedValue, line }: { data: { value: number, probability: number }[], projectedValue: number, line: number }) => {
  return (
    <div className="h-[150px] w-full mt-4">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data}>
          <defs>
            <linearGradient id="colorProb" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10b981" stopOpacity={0.3}/>
              <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
          <XAxis 
            dataKey="value" 
            stroke="#71717a" 
            fontSize={10} 
            tickLine={false} 
            axisLine={false}
            tickFormatter={(val) => val.toFixed(1)}
          />
          <YAxis hide />
          <Tooltip 
            contentStyle={{ backgroundColor: '#18181b', border: '1px solid #27272a', borderRadius: '8px', fontSize: '10px' }}
            itemStyle={{ color: '#10b981' }}
            labelStyle={{ color: '#71717a' }}
          />
          <Area 
            type="monotone" 
            dataKey="probability" 
            stroke="#10b981" 
            fillOpacity={1} 
            fill="url(#colorProb)" 
            strokeWidth={2}
          />
          {/* Reference lines for projected value and line */}
          <Line type="monotone" dataKey="probability" stroke="transparent" dot={false} />
        </AreaChart>
      </ResponsiveContainer>
      <div className="flex justify-between text-[8px] uppercase font-bold text-zinc-500 mt-1 px-2">
        <span>0.0</span>
        <span>Probability Density Curve</span>
        <span>{(data[data.length-1]?.value || 0).toFixed(1)}</span>
      </div>
    </div>
  );
};

const TacticalAlerts = ({ alerts }: { alerts: { type: string, message: string, severity: string }[] }) => {
  if (!alerts || alerts.length === 0) return null;

  return (
    <div className="space-y-2 mb-6">
      {alerts.map((alert, idx) => (
        <div 
          key={idx} 
          className={`p-3 rounded-xl border flex items-start gap-3 ${
            alert.severity === 'high' ? 'bg-rose-500/10 border-rose-500/30 text-rose-400' :
            alert.severity === 'medium' ? 'bg-amber-500/10 border-amber-500/30 text-amber-400' :
            'bg-blue-500/10 border-blue-500/30 text-blue-400'
          }`}
        >
          <ShieldAlert className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <div className="text-xs leading-relaxed">
            <span className="font-black uppercase text-[10px] block mb-0.5">{alert.type} Alert</span>
            {alert.message}
          </div>
        </div>
      ))}
    </div>
  );
};

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

const ProjectionCard = ({ 
  projection, 
  onSave, 
  excludedIndices, 
  onToggleSample 
}: { 
  projection: PredictionResponse, 
  onSave: () => void, 
  excludedIndices: number[],
  onToggleSample: (idx: number) => void
}) => {
  return (
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

        {projection.tacticalAlerts && <TacticalAlerts alerts={projection.tacticalAlerts} />}

        <div className="grid grid-cols-2 gap-4 mb-8">
          <div className="bg-zinc-900/80 p-4 rounded-xl border border-zinc-800">
            <div className="text-[10px] text-zinc-500 uppercase font-bold mb-1">Prop Line</div>
            <div className="flex items-baseline gap-1">
              <span className="text-2xl font-black text-white">{projection.line}</span>
              <span className="text-sm text-zinc-500 font-medium capitalize">{projection.propType}</span>
            </div>
          </div>
          <div className="bg-zinc-900/80 p-4 rounded-xl border border-zinc-800">
            <div className="text-[10px] text-zinc-500 uppercase font-bold mb-1 flex justify-between">
              <span>Projected</span>
              <span className="text-[8px] opacity-50">95% CI</span>
            </div>
            <div className="flex items-baseline gap-2">
              <div className="text-2xl font-black text-emerald-400">{projection.projectedValue}</div>
              <div className="text-[10px] font-bold text-zinc-500">
                [{projection.confidenceInterval[0]} - {projection.confidenceInterval[1]}]
              </div>
            </div>
          </div>
        </div>

        {projection.probabilityCurve && (
          <div className="mb-8">
            <div className="text-[10px] text-zinc-500 uppercase font-bold mb-2 flex items-center gap-2">
              <BarChart3 className="w-3 h-3 text-emerald-400" /> Bayesian Probability Density
            </div>
            <ProbabilityChart 
              data={projection.probabilityCurve} 
              projectedValue={projection.projectedValue} 
              line={projection.line} 
            />
          </div>
        )}

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
                {projection.recentSamples.filter((s, idx) => !excludedIndices.includes(idx) && (projection.recommendation === 'over' ? s.value > projection.line : s.value < projection.line)).length} / {projection.recentSamples.filter((_, idx) => !excludedIndices.includes(idx)).length} HIT RATE
              </div>
            </div>
            <div className="grid grid-cols-5 gap-2">
              {projection.recentSamples.map((sample, idx) => {
                const isExcluded = excludedIndices.includes(idx);
                const isHit = projection.recommendation === 'over' ? sample.value > projection.line : sample.value < projection.line;
                const isPush = sample.value === projection.line;
                const diffColor = sample.matchDifficulty === 'high' ? 'bg-rose-500' : sample.matchDifficulty === 'medium' ? 'bg-amber-500' : 'bg-emerald-500';
                
                return (
                  <div 
                    key={idx} 
                    onClick={() => onToggleSample(idx)}
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

      <button onClick={onSave} className="w-full bg-emerald-500 text-black py-5 rounded-2xl font-black shadow-[0_0_20px_rgba(16,185,129,0.2)] hover:bg-emerald-400 transition-colors">
        SAVE TO TRACKING
      </button>
    </motion.div>
  );
};

// --- Main App ---

export default function App() {
  const [user, setUser] = useState<{email: string, accessType: string, sessionToken: string} | null>(null);
  const [activeTab, setActiveTab] = useState<'predict' | 'tracking' | 'admin' | 'chat'>('predict');
  const [trackingView, setTrackingView] = useState<'live' | 'history'>('live');
  
  const [wizardStep, setWizardStep] = useState(1);
  const [wizardData, setWizardData] = useState<Partial<PredictionRequest>>({});
  const [wizardError, setWizardError] = useState<string | null>(null);
  const [teams, setTeams] = useState<Team[]>([]);
  const [searchMode, setSearchMode] = useState<'wizard' | 'natural'>('wizard');
  const [naturalQuery, setNaturalQuery] = useState('');
  const [isParsingQuery, setIsParsingQuery] = useState(false);
  
  const [projection, setProjection] = useState<PredictionResponse | null>(null);
  const [isProjecting, setIsProjecting] = useState(false);
  const [isReAnalyzing, setIsReAnalyzing] = useState<string | null>(null);
  const [excludedSampleIndices, setExcludedSampleIndices] = useState<number[]>([]);
  
  const [savedPicks, setSavedPicks] = useState<SavedPick[]>([]);
  const [selectedPick, setSelectedPick] = useState<SavedPick | null>(null);
  const [marketSentiment, setMarketSentiment] = useState<string | null>(null);

  // Chat State
  const [chatMessages, setChatMessages] = useState<{role: 'user' | 'model', text: string}[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [isChatting, setIsChatting] = useState(false);
  const [chatSession, setChatSession] = useState<any>(null);
  const [apiStatus, setApiStatus] = useState<'online' | 'offline' | 'checking'>('checking');

  const startNewChat = async () => {
    const session = await startTacticalChat();
    setChatSession(session);
    setChatMessages([{ role: 'model', text: "Welcome to the Tactical Command Center. I am your elite analyst. How can I help you dominate the props market today?" }]);
  };

  const handleSendMessage = async () => {
    if (!chatInput.trim() || !chatSession) return;
    
    const userMsg = chatInput;
    setChatInput('');
    setChatMessages(prev => [...prev, { role: 'user', text: userMsg }]);
    setIsChatting(true);

    try {
      const result = await chatSession.sendMessage({ message: userMsg });
      setChatMessages(prev => [...prev, { role: 'model', text: result.text }]);
    } catch (e) {
      setChatMessages(prev => [...prev, { role: 'model', text: "Error connecting to tactical uplink. Please try again." }]);
    } finally {
      setIsChatting(false);
    }
  };

  // Live Pulse Polling
  useEffect(() => {
    const pollLiveStats = async () => {
      const livePicks = savedPicks.filter(p => p.status === 'live');
      if (livePicks.length === 0) return;

      const updatedPicks = [...savedPicks];
      let hasChanges = false;

      for (const pick of livePicks) {
        if (!pick.fixtureId) continue;
        
        const liveData = await getLivePlayerStats(pick.fixtureId, pick.player.id);
        if (liveData) {
          const index = updatedPicks.findIndex(p => p.id === pick.id);
          const currentValue = liveData.value[pick.propType.toLowerCase()]?.total || 0;
          
          updatedPicks[index] = {
            ...updatedPicks[index],
            liveStats: {
              minutes: liveData.minutes,
              value: currentValue,
              onTrack: pick.recommendation === 'over' ? currentValue >= (pick.line * (liveData.minutes / 90)) : currentValue <= (pick.line * (liveData.minutes / 90)),
              lastUpdated: Date.now()
            }
          };
          hasChanges = true;
        }
      }

      if (hasChanges) {
        setSavedPicks(updatedPicks);
        localStorage.setItem('reverse_picks', JSON.stringify(updatedPicks));
      }
    };

    const interval = setInterval(pollLiveStats, 60000); // Poll every minute
    return () => clearInterval(interval);
  }, [savedPicks]);

  // Load saved picks from local storage
  useEffect(() => {
    const saved = localStorage.getItem('reverse_picks');
    if (saved) setSavedPicks(JSON.parse(saved));
    
    // Check auth
    const savedUser = localStorage.getItem('rp_user_info');
    if (savedUser) {
      try {
        const parsedUser = JSON.parse(savedUser);
        setUser(parsedUser);
        
        // Verify session in background
        fetch('/api/auth/verify-session', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ 
            email: parsedUser.email, 
            session_token: parsedUser.sessionToken 
          })
        }).then(res => res.json()).then(data => {
          if (!data.valid) {
            handleLogout();
          } else if (data.access_type && data.access_type !== parsedUser.accessType) {
            handleLogin({ ...parsedUser, accessType: data.access_type });
          }
        }).catch(() => {});
      } catch (e) {}
    }

    // Check API Status
    checkApiStatus().then(status => {
      if (status) setApiStatus('online');
      else setApiStatus('offline');
    }).catch(() => setApiStatus('offline'));
  }, []);

  useEffect(() => {
    localStorage.setItem('reverse_picks', JSON.stringify(savedPicks));
  }, [savedPicks]);

  const handleLogin = (userData: any) => {
    setUser(userData);
    localStorage.setItem('rp_user_info', JSON.stringify(userData));
  };

  const handleLogout = () => {
    if (user) {
      fetch(`/api/auth/logout?email=${encodeURIComponent(user.email)}`, { method: 'POST' }).catch(() => {});
    }
    localStorage.removeItem('rp_session_token');
    localStorage.removeItem('rp_user_info');
    setUser(null);
  };

  const handleNaturalSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!naturalQuery.trim()) return;
    
    setIsParsingQuery(true);
    setWizardError(null);
    try {
      const parsed = await parseNaturalLanguageQuery(naturalQuery);
      if (!parsed.playerName) {
        throw new Error("Could not identify player. Please try a more specific query (e.g., 'Lamine Yamal 52.5 passes vs Villarreal').");
      }
      
      // Now we need to find the player and opponent IDs to run the full analysis
      const players = await searchPlayers(parsed.playerName);
      if (players.length === 0) throw new Error(`Player "${parsed.playerName}" not found.`);
      const player = players[0];
      
      const leagueId = player.teamId ? (await getPlayerStats(player.id))?.statistics?.[0]?.league?.id : 39;
      const teamsInLeague = await getTeamsByLeague(leagueId || 39);
      const opponent = teamsInLeague.find(t => t.name.toLowerCase().includes((parsed.opponentName || '').toLowerCase())) || teamsInLeague[0];
      
      const request: PredictionRequest = {
        leagueId: leagueId || 39,
        playerId: player.id,
        playerName: player.name,
        teamId: player.teamId,
        opponentId: opponent.id,
        opponentName: opponent.name,
        venue: parsed.venue || 'home',
        propType: parsed.propType || 'pass_attempts',
        line: parsed.line || 0
      };
      
      await runProjection(request);
    } catch (err: any) {
      setWizardError(err.message || "Failed to parse query.");
    } finally {
      setIsParsingQuery(false);
    }
  };

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

  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const handleWizardPlayerSearch = (query: string) => {
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }

    if (query.length < 3) {
      setWizardPlayers([]);
      return;
    }

    searchTimeoutRef.current = setTimeout(async () => {
      setIsWizardPlayersLoading(true);
      setWizardError(null);
      try {
        const results = await searchPlayers(query, wizardData.leagueId);
        setWizardPlayers(results);
      } catch (error: any) {
        console.error(error);
        setWizardError(error.message || "Failed to search players.");
      } finally {
        setIsWizardPlayersLoading(false);
      }
    }, 500);
  };

  const handleWizardPlayerSelect = (player: Player) => {
    setWizardData({ ...wizardData, playerId: player.id, playerName: player.name, teamId: player.teamId });
    setWizardStep(3);
  };

  const handleWizardOpponentSelect = (team: Team) => {
    setWizardData({ ...wizardData, opponentId: team.id, opponentName: team.name });
    setWizardStep(4);
  };

  const performFullAnalysis = async (data: PredictionRequest) => {
    // 1. Fetch player statistics
    const playerStats = await getPlayerStats(data.playerId);
    if (!playerStats) throw new Error("Could not fetch player statistics.");
    
    let actualTeamId = data.teamId;
    if (actualTeamId === 0 && playerStats && playerStats.statistics && playerStats.statistics.length > 0) {
      actualTeamId = playerStats.statistics[0].team?.id || 0;
    }
    if (actualTeamId === 0) throw new Error("Could not identify player's team.");
    
    const matchHistory = await getRecentPlayerMatchHistory(data.playerId, actualTeamId, 10);
    const leagueId = data.leagueId || playerStats.statistics?.[0]?.league?.id;
    
    const [teamStats, opponentStats, h2hData, teamFixtures, standings] = await Promise.all([
      getTeamStats(actualTeamId, leagueId).catch(() => null),
      getTeamStats(data.opponentId, leagueId).catch(() => null),
      getH2H(actualTeamId, data.opponentId, 5).catch(() => []),
      getFixtures(actualTeamId, 20).catch(() => []),
      getStandings(leagueId).catch(() => [])
    ]);

    const upcomingFixture = teamFixtures?.find((f: any) => f.fixture.status.short === 'NS');
    let odds = null;
    let fixtureMetadata = null;
    if (upcomingFixture) {
      odds = await getOdds(upcomingFixture.fixture.id).catch(() => null);
      fixtureMetadata = {
        round: upcomingFixture.league.round,
        venue: upcomingFixture.fixture.venue.name,
        city: upcomingFixture.fixture.venue.city
      };
    }

    const historicalData = { 
      playerStats, 
      teamStats,
      opponentStats,
      h2hData,
      standings,
      teamFixtures,
      matchHistory,
      odds,
      fixtureMetadata
    };
    
    return await generateProjection(data, historicalData);
  };

  const reAnalyzePick = async (pick: SavedPick, e: React.MouseEvent) => {
    e.stopPropagation();
    setIsReAnalyzing(pick.id);
    try {
      // 1. Search for player
      const players = await searchPlayers(pick.player.name);
      if (players.length === 0) throw new Error(`Player "${pick.player.name}" not found in database.`);
      const foundPlayer = players[0];

      // 2. Search for opponent in same league
      const leagueId = foundPlayer.teamId ? (await getPlayerStats(foundPlayer.id))?.statistics?.[0]?.league?.id : 39;
      const teamsInLeague = await getTeamsByLeague(leagueId || 39);
      const foundOpponent = teamsInLeague.find(t => t.name.toLowerCase().includes(pick.opponent.toLowerCase())) || teamsInLeague[0];

      const request: PredictionRequest = {
        leagueId: leagueId || 39,
        playerId: foundPlayer.id,
        playerName: foundPlayer.name,
        teamId: foundPlayer.teamId,
        opponentId: foundOpponent.id,
        opponentName: foundOpponent.name,
        venue: 'home',
        propType: pick.propType as any,
        line: pick.line
      };

      const result = await performFullAnalysis(request);
      if (result) {
        const updatedPick: SavedPick = {
          ...pick,
          ...result,
          status: 'live'
        };
        const updatedPicks = savedPicks.map(p => p.id === pick.id ? updatedPick : p);
        setSavedPicks(updatedPicks);
        localStorage.setItem('reverse_picks', JSON.stringify(updatedPicks));
      }
    } catch (err: any) {
      alert(err.message || "Failed to re-analyze pick.");
    } finally {
      setIsReAnalyzing(null);
    }
  };

  const removePick = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm("Remove this pick from tracking?")) return;
    const updated = savedPicks.filter(p => p.id !== id);
    setSavedPicks(updated);
    localStorage.setItem('reverse_picks', JSON.stringify(updated));
  };

  const runProjection = async (data: PredictionRequest) => {
    setIsProjecting(true);
    setWizardError(null);
    setActiveTab('predict'); // Stay on predict to show results
    try {
      console.log('Starting projection for:', data);
      const result = await performFullAnalysis(data);
      console.log('Projection result:', result);
      if (!result || !result.player) throw new Error("AI model failed to generate a valid projection. Please try again.");
      
      // Fetch market sentiment in background
      getMarketSentiment(data.playerName!, data.propType!, data.line!).then(setMarketSentiment).catch(err => console.error('Market sentiment error:', err));

      setProjection(result);
      setExcludedSampleIndices([]);
    } catch (error: any) {
      console.error('Projection Error:', error);
      setWizardError(error.message || "An unexpected error occurred during projection.");
    } finally {
      setIsProjecting(false);
    }
  };

  const savePick = () => {
    if (!projection) return;
    const newPick: SavedPick = {
      ...projection,
      id: Math.random().toString(36).substring(2, 9),
      timestamp: Date.now(),
      status: 'live',
      result: 'pending',
      excludedSampleIndices
    };
    const updated = [newPick, ...savedPicks];
    setSavedPicks(updated);
    localStorage.setItem('reverse_picks', JSON.stringify(updated));
    setProjection(null);
    setExcludedSampleIndices([]);
    setWizardStep(1);
    setWizardData({});
    setActiveTab('tracking');
  };

  if (!user) {
    return <LoginPage onLogin={handleLogin} />;
  }

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
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-zinc-900 border border-zinc-800">
            <div className={`w-1.5 h-1.5 rounded-full ${
              apiStatus === 'online' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 
              apiStatus === 'offline' ? 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.6)]' : 
              'bg-zinc-600 animate-pulse'
            }`} />
            <span className="text-[8px] font-black uppercase tracking-widest text-zinc-500">API</span>
          </div>
          <Badge variant="neon">v1.2.4-BETA</Badge>
          <button 
            onClick={() => window.location.reload()}
            className="p-2 hover:bg-zinc-800 rounded-full transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-5 h-5 text-zinc-400" />
          </button>
          <button
            onClick={handleLogout}
            className="text-xs font-bold text-zinc-400 hover:text-white transition-colors uppercase tracking-wider"
          >
            Logout
          </button>
        </div>
      </header>

      <main className="pb-32 pt-6 px-6 max-w-md mx-auto min-h-[80vh] relative">
        <AnimatePresence mode="wait">
          {activeTab === 'predict' && (
            <motion.div
              key="predict"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="space-y-8"
            >
              {!projection && !isProjecting && (
                <div className="space-y-6">
                  <div className="flex items-center justify-between">
                    <div className="space-y-1">
                      <h2 className="text-2xl font-bold tracking-tight">AI Wizard</h2>
                      <p className="text-zinc-500 text-sm">
                        {searchMode === 'wizard' ? `Step ${wizardStep} of 6` : 'Natural Language Search'}
                      </p>
                    </div>
                    <div className="flex items-center gap-4">
                      {searchMode === 'wizard' && wizardStep > 1 && (
                        <button onClick={() => setWizardStep(wizardStep - 1)} className="text-zinc-400 text-sm flex items-center gap-1">
                          <ArrowLeft className="w-4 h-4" /> Back
                        </button>
                      )}
                    </div>
                  </div>

                  <div className="flex bg-zinc-900 p-1 rounded-xl border border-zinc-800">
                    <button 
                      onClick={() => setSearchMode('wizard')}
                      className={`flex-1 py-2 rounded-lg text-xs font-bold transition-all ${searchMode === 'wizard' ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}
                    >
                      Step-by-Step
                    </button>
                    <button 
                      onClick={() => setSearchMode('natural')}
                      className={`flex-1 py-2 rounded-lg text-xs font-bold transition-all ${searchMode === 'natural' ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}
                    >
                      Natural Search
                    </button>
                  </div>

                  {searchMode === 'natural' && (
                    <form onSubmit={handleNaturalSearch} className="space-y-4">
                      <div className="relative">
                        <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-zinc-500" />
                        <input 
                          type="text" 
                          value={naturalQuery}
                          onChange={(e) => setNaturalQuery(e.target.value)}
                          placeholder="e.g. Lamine Yamal 52.5 passes vs Villarreal" 
                          className="w-full bg-zinc-900 border border-zinc-800 rounded-2xl py-5 pl-12 pr-4 focus:outline-none focus:border-emerald-500/50 text-sm"
                        />
                      </div>
                      <button 
                        type="submit"
                        disabled={isParsingQuery || !naturalQuery.trim()}
                        className="w-full bg-emerald-500 hover:bg-emerald-400 text-black font-black py-5 rounded-2xl shadow-[0_0_30px_rgba(16,185,129,0.3)] transition-all flex items-center justify-center gap-2 disabled:opacity-50"
                      >
                        {isParsingQuery ? <Loader2 className="w-5 h-5 animate-spin" /> : <Zap className="w-5 h-5 fill-current" />}
                        {isParsingQuery ? 'PARSING QUERY...' : 'ANALYZE QUERY'}
                      </button>
                      {wizardError && (
                        <div className="bg-rose-500/10 border border-rose-500/20 p-4 rounded-xl flex items-start gap-3">
                          <ShieldAlert className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
                          <p className="text-xs text-rose-300/80 leading-relaxed">{wizardError}</p>
                        </div>
                      )}
                    </form>
                  )}

                  {searchMode === 'wizard' && wizardStep === 1 && (
                    <div className="space-y-6">
                      {['Domestic', 'International Club', 'International Team'].map(type => {
                        const leaguesOfType = SUPPORTED_LEAGUES.filter(l => (l as any).type === type);
                        if (leaguesOfType.length === 0) return null;
                        
                        return (
                          <div key={type} className="space-y-3">
                            <h3 className="text-[10px] font-black text-zinc-500 uppercase tracking-[0.2em] px-1">{type}</h3>
                            <div className="grid grid-cols-1 gap-2">
                              {leaguesOfType.map((league) => (
                                <Card key={league.id} className="p-4 hover:bg-zinc-800/50 cursor-pointer transition-colors" onClick={() => handleWizardLeagueSelect(league.id)}>
                                  <div className="flex items-center justify-between">
                                    <span className="font-bold text-sm">{league.name}</span>
                                    <ChevronRight className="w-4 h-4 text-zinc-600" />
                                  </div>
                                </Card>
                              ))}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {searchMode === 'wizard' && wizardStep === 2 && (
                    <div className="space-y-4">
                      {wizardError && (
                        <div className="bg-rose-500/10 border border-rose-500/20 p-3 rounded-xl mb-4 flex items-start gap-2">
                          <ShieldAlert className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                          <p className="text-[10px] text-rose-300/80 leading-relaxed">{wizardError}</p>
                        </div>
                      )}
                      <form onSubmit={(e) => e.preventDefault()} className="relative">
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
                      </form>
                      
                      <div className="space-y-2 max-h-[400px] overflow-y-auto pr-1">
                        {wizardPlayers.map((player) => (
                          <Card key={player.id} className="p-3 hover:bg-zinc-800/50 cursor-pointer transition-colors" onClick={() => handleWizardPlayerSelect(player)}>
                            <div className="flex items-center gap-3">
                              <div className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center border border-zinc-700">
                                <User className="w-4 h-4 text-zinc-400" />
                              </div>
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

                  {searchMode === 'wizard' && wizardStep === 3 && (
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
                              <div className="w-12 h-12 rounded-xl bg-zinc-800 flex items-center justify-center border border-zinc-700">
                                <Shield className="w-6 h-6 text-zinc-400" />
                              </div>
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

                  {searchMode === 'wizard' && wizardStep === 4 && (
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

                  {searchMode === 'wizard' && wizardStep === 5 && (
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

                  {searchMode === 'wizard' && wizardStep === 6 && (
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
                      {wizardError && (
                        <div className="bg-rose-500/10 border border-rose-500/20 p-4 rounded-xl mb-4">
                          <div className="flex items-start gap-3">
                            <ShieldAlert className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
                            <div className="space-y-1">
                              <h4 className="text-sm font-bold text-rose-400">Projection Error</h4>
                              <p className="text-xs text-rose-300/80 leading-relaxed">{wizardError}</p>
                            </div>
                          </div>
                        </div>
                      )}
                      <button 
                        onClick={() => runProjection(wizardData as PredictionRequest)}
                        className="w-full bg-emerald-500 hover:bg-emerald-400 text-black font-black py-5 rounded-2xl shadow-[0_0_30px_rgba(16,185,129,0.3)] transition-all flex items-center justify-center gap-2"
                      >
                        <Zap className="w-5 h-5 fill-current" />
                        GENERATE PROJECTION
                      </button>
                    </div>
                  )}
                </div>
              )}

              {isProjecting && (
                <div className="py-20 flex flex-col items-center justify-center space-y-6 text-center">
                  <div className="relative">
                    <div className="w-24 h-24 rounded-full border-4 border-emerald-500/20 border-t-emerald-500 animate-spin" />
                    <Zap className="w-8 h-8 text-emerald-400 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 animate-pulse" />
                  </div>
                  <div className="space-y-2">
                    <h3 className="text-xl font-bold text-white">Analyzing Matchup...</h3>
                    <p className="text-zinc-500 text-sm animate-pulse">Running Bayesian simulations & searching live data</p>
                  </div>
                </div>
              )}

              {projection && !isProjecting && (
                <div className="space-y-6">
                  <div className="flex items-center gap-2 mb-4">
                    <button 
                      onClick={() => setProjection(null)}
                      className="p-2 rounded-xl bg-zinc-900 border border-zinc-800 text-zinc-400 hover:text-white transition-colors"
                    >
                      <ArrowLeft className="w-5 h-5" />
                    </button>
                    <span className="text-xs font-bold text-zinc-500 uppercase tracking-widest">Back to Search</span>
                  </div>
                  <ProjectionCard 
                    projection={projection} 
                    onSave={savePick} 
                    excludedIndices={excludedSampleIndices}
                    onToggleSample={(idx) => {
                      setExcludedSampleIndices(prev => 
                        prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx]
                      );
                    }}
                  />
                </div>
              )}
            </motion.div>
          )}

          {activeTab === 'tracking' && (
            <motion.div
              key="tracking"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="space-y-6"
            >
              <div className="flex items-center justify-between">
                <h2 className="text-2xl font-bold tracking-tight">Tracking</h2>
                <div className="flex items-center gap-3">
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
              </div>

              <div className="space-y-4">
                {savedPicks.filter(p => trackingView === 'live' ? p.status === 'live' : p.status === 'settled').length === 0 ? (
                  <div className="text-center py-24 space-y-4">
                    <div className="w-16 h-16 bg-zinc-900 rounded-full flex items-center justify-center mx-auto border border-zinc-800">
                      <Clock className="w-8 h-8 text-zinc-700" />
                    </div>
                    <p className="text-zinc-500 text-sm">No {trackingView} picks being tracked.</p>
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
                          <button 
                            onClick={(e) => removePick(pick.id, e)}
                            className="p-1 text-zinc-600 hover:text-rose-500 transition-colors"
                            title="Remove from tracking"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
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

                      {pick.status === 'live' && (pick.confidenceScore === 0.5 || pick.player.team === 'Scanned') && (
                        <div className="mb-4">
                          <button 
                            onClick={(e) => reAnalyzePick(pick, e)}
                            disabled={isReAnalyzing === pick.id}
                            className="w-full flex items-center justify-center gap-2 py-2 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 rounded-lg border border-emerald-500/20 transition-all group/btn disabled:opacity-50"
                          >
                            {isReAnalyzing === pick.id ? (
                              <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              <Brain className="w-4 h-4 group-hover/btn:scale-110 transition-transform" />
                            )}
                            <span className="text-[10px] font-black uppercase tracking-widest">
                              {isReAnalyzing === pick.id ? 'Analyzing Data...' : 'Run Full AI Analysis'}
                            </span>
                          </button>
                        </div>
                      )}

                      <div className="grid grid-cols-4 gap-2 mb-4">
                        <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/50">
                          <div className="text-[7px] text-zinc-500 uppercase font-bold">Proj</div>
                          <div className="text-xs font-black text-emerald-400">{pick.projectedValue}</div>
                        </div>
                        <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/50">
                          <div className="text-[7px] text-zinc-500 uppercase font-bold">Now</div>
                          <div className="text-xs font-black text-white">{pick.liveStats?.value || pick.actualValue || 0}</div>
                        </div>
                        <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/50">
                          <div className="text-[7px] text-zinc-500 uppercase font-bold">95% CI</div>
                          <div className="text-[9px] font-black text-zinc-400">
                            {pick.confidenceInterval[0]}-{pick.confidenceInterval[1]}
                          </div>
                        </div>
                        <div className="bg-zinc-900/80 p-2 rounded-lg border border-zinc-800/50">
                          <div className="text-[7px] text-zinc-500 uppercase font-bold">Hit Rate</div>
                          <div className="text-xs font-black text-amber-400">
                            {Math.round((pick.recentSamples.filter(s => pick.recommendation === 'over' ? s.value > pick.line : s.value < pick.line).length / pick.recentSamples.length) * 100)}%
                          </div>
                        </div>
                      </div>

                      {pick.status === 'live' && pick.liveStats && (
                        <div className="mb-4 space-y-2">
                          <div className="flex justify-between items-center text-[9px] font-bold uppercase tracking-widest">
                            <div className="flex items-center gap-2">
                              <Activity className="w-3 h-3 text-emerald-400 animate-pulse" /> Live Pulse
                            </div>
                            <div className={pick.liveStats.onTrack ? 'text-emerald-400' : 'text-rose-400'}>
                              {pick.liveStats.onTrack ? 'ON TRACK' : 'BEHIND'} • {pick.liveStats.minutes}'
                            </div>
                          </div>
                          <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden border border-zinc-700/50">
                            <motion.div 
                              initial={{ width: 0 }}
                              animate={{ width: `${Math.min((pick.liveStats.value / pick.line) * 100, 100)}%` }}
                              className={`h-full ${pick.liveStats.onTrack ? 'bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.5)]' : 'bg-rose-500'}`}
                            />
                          </div>
                          <div className="flex justify-between text-[8px] text-zinc-500 font-bold">
                            <span>0</span>
                            <span>PROGRESS TO LINE ({pick.line})</span>
                            <span>{pick.line}</span>
                          </div>
                        </div>
                      )}

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

          {activeTab === 'chat' && (
            <motion.div
              key="chat"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="flex flex-col h-[calc(100vh-16rem)]"
            >
              <div className="flex items-center justify-between mb-6">
                <div>
                  <h2 className="text-2xl font-bold tracking-tight">Tactical Uplink</h2>
                  <p className="text-zinc-500 text-xs uppercase tracking-widest font-bold mt-1">AI Strategic Analyst</p>
                </div>
                <button 
                  onClick={startNewChat}
                  className="p-2 rounded-xl bg-zinc-900 border border-zinc-800 text-zinc-400 hover:text-white transition-colors"
                  title="Reset Session"
                >
                  <RefreshCw className="w-4 h-4" />
                </button>
              </div>

              <div className="flex-1 overflow-y-auto space-y-4 pr-2 custom-scrollbar">
                {chatMessages.map((msg, i) => (
                  <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                    <div className={`max-w-[85%] p-4 rounded-2xl text-sm leading-relaxed ${
                      msg.role === 'user' 
                        ? 'bg-emerald-500 text-black font-medium rounded-tr-none' 
                        : 'bg-zinc-900 border border-zinc-800 text-zinc-200 rounded-tl-none'
                    }`}>
                      {msg.text}
                    </div>
                  </div>
                ))}
                {isChatting && (
                  <div className="flex justify-start">
                    <div className="bg-zinc-900 border border-zinc-800 p-4 rounded-2xl rounded-tl-none">
                      <Loader2 className="w-4 h-4 text-emerald-400 animate-spin" />
                    </div>
                  </div>
                )}
              </div>

              <div className="mt-6 relative">
                <input 
                  type="text" 
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSendMessage()}
                  placeholder="Ask for tactical insights..."
                  className="w-full bg-zinc-900 border border-zinc-800 rounded-2xl py-4 pl-5 pr-14 focus:outline-none focus:border-emerald-500/50 text-sm"
                />
                <button 
                  onClick={handleSendMessage}
                  disabled={isChatting || !chatInput.trim()}
                  className="absolute right-2 top-1/2 -translate-y-1/2 w-10 h-10 bg-emerald-500 rounded-xl flex items-center justify-center text-black disabled:opacity-50 transition-all active:scale-95"
                >
                  <Send className="w-5 h-5" />
                </button>
              </div>
            </motion.div>
          )}

          {activeTab === 'admin' && user.accessType === 'Owner' && (
            <motion.div
              key="admin"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
            >
              <AdminPanel user={user} />
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
                                <div className="flex flex-col items-center mt-0.5">
                                  <span className="text-[8px] font-bold opacity-60 leading-none">{sample.minutesPlayed}'</span>
                                  {sample.blockType && (
                                    <span className="text-[6px] font-black uppercase text-emerald-400 mt-0.5 leading-none">{sample.blockType}</span>
                                  )}
                                </div>
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
            onClick={() => { setActiveTab('predict'); setWizardStep(1); setProjection(null); setWizardData({}); }}
            className={`flex flex-col items-center gap-1 transition-all ${activeTab === 'predict' ? 'text-emerald-400 scale-110' : 'text-zinc-500 hover:text-zinc-300'}`}
          >
            <Zap className="w-6 h-6" />
            <span className="text-[10px] font-bold uppercase tracking-widest">Predict</span>
          </button>

          <button 
            onClick={() => { setActiveTab('tracking'); setWizardStep(1); setProjection(null); setWizardData({}); }}
            className={`flex flex-col items-center gap-1 transition-all ${activeTab === 'tracking' ? 'text-emerald-400 scale-110' : 'text-zinc-500 hover:text-zinc-300'}`}
          >
            <Activity className="w-6 h-6" />
            <span className="text-[10px] font-bold uppercase tracking-widest">Tracking</span>
          </button>

          <button 
            onClick={() => {
              setActiveTab('chat');
              if (!chatSession) startNewChat();
            }}
            className={`flex flex-col items-center gap-1 transition-all ${activeTab === 'chat' ? 'text-emerald-400 scale-110' : 'text-zinc-500 hover:text-zinc-300'}`}
          >
            <MessageSquare className="w-6 h-6" />
            <span className="text-[10px] font-bold uppercase tracking-widest">Chat</span>
          </button>

          {user.accessType === 'Owner' && (
            <button 
              onClick={() => setActiveTab('admin')}
              className={`flex flex-col items-center gap-1 transition-all ${activeTab === 'admin' ? 'text-emerald-400 scale-110' : 'text-zinc-500 hover:text-zinc-300'}`}
            >
              <ShieldAlert className="w-6 h-6" />
              <span className="text-[10px] font-bold uppercase tracking-widest">Admin</span>
            </button>
          )}
        </div>
      </nav>
    </div>
  );
}
