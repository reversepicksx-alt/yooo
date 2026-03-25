import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Zap, ChevronRight, RefreshCw, ArrowLeft, Clock, Activity,
  Shield, MessageSquare, Send, Loader2, Trash2, Search, User,
  TrendingUp, TrendingDown, BarChart3, ShieldAlert, Target
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';
import {
  getTeamsByLeague, searchPlayers, predict, startChat, sendChatMessage,
  parseNaturalQuery, checkApiStatus, SUPPORTED_LEAGUES
} from './api';
import './App.css';

const PROP_TYPES = ['pass_attempts', 'shots', 'saves', 'clearances', 'tackles'];

function ProbabilityChart({ data }) {
  if (!data || data.length === 0) return null;
  return (
    <div style={{ height: 140, width: '100%', marginTop: 12 }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data}>
          <defs>
            <linearGradient id="colorProb" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#222238" vertical={false} />
          <XAxis dataKey="value" stroke="#555570" fontSize={9} tickLine={false} axisLine={false}
            tickFormatter={v => typeof v === 'number' ? v.toFixed(1) : v} />
          <YAxis hide />
          <Tooltip
            contentStyle={{ backgroundColor: '#141422', border: '1px solid #222238', borderRadius: 8, fontSize: 10, color: '#e8e8f0' }}
            itemStyle={{ color: '#10b981' }}
            labelStyle={{ color: '#8888a8' }}
          />
          <Area type="monotone" dataKey="probability" stroke="#10b981" fillOpacity={1} fill="url(#colorProb)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function ProjectionCard({ projection, onSave, excludedIndices, onToggleSample }) {
  const rec = projection.recommendation;
  const [venueFilter, setVenueFilter] = React.useState('all');

  const filteredSamples = (projection.recentSamples || []).map((s, i) => ({ ...s, _idx: i }))
    .filter(s => venueFilter === 'all' || s.venue === venueFilter);

  return (
    <div className="animate-fade-in space-y-6">
      <div className="projection-card">
        <div className="projection-header">
          <div>
            <span className="badge neon">Projection Ready</span>
            <div className="projection-player" data-testid="projection-player-name">{projection.player?.name}</div>
            <div className="projection-matchup">{projection.player?.team} vs {projection.opponent}</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="stat-label">Confidence</div>
            <div className="confidence-value" data-testid="projection-confidence">{projection.confidenceScore}%</div>
          </div>
        </div>

        {projection.tacticalAlerts?.length > 0 && (
          <div className="space-y-2 mb-6">
            {projection.tacticalAlerts.map((alert, i) => (
              <div key={i} className={`alert-item ${alert.severity}`}>
                <ShieldAlert />
                <div>
                  <span className="alert-type">{alert.type} Alert</span>
                  <span className="alert-message">{alert.message}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="grid-2 mb-6">
          <div className="stat-box">
            <div className="stat-label">Prop Line</div>
            <div className="flex items-center gap-2">
              <span className="stat-value">{projection.line}</span>
              <span className="stat-suffix">{projection.propType?.replace('_', ' ')}</span>
            </div>
          </div>
          <div className="stat-box">
            <div className="stat-label" style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Projected</span>
              <span style={{ fontSize: 7, opacity: 0.5 }}>95% CI</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="stat-value accent" data-testid="projected-value">{projection.projectedValue}</span>
              <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)' }}>
                [{projection.confidenceInterval?.[0]} - {projection.confidenceInterval?.[1]}]
              </span>
            </div>
          </div>
        </div>

        {projection.probabilityCurve && <ProbabilityChart data={projection.probabilityCurve} />}

        <div className={`rec-banner ${rec} mt-6`} data-testid="recommendation-banner">
          <div className={`rec-label ${rec}`}>
            {rec === 'over' ? <TrendingUp /> : <TrendingDown />}
            <span>Recommend: {rec}</span>
          </div>
          <span className={`badge ${rec === 'over' ? 'neon' : 'danger'}`}>{projection.confidenceLevel}</span>
        </div>

        {projection.recentSamples?.length > 0 && (
          <div className="mt-6">
            <div className="flex justify-between items-center mb-4">
              <div className="stat-label flex items-center gap-2"><Activity style={{ width: 12, height: 12 }} /> Recent Form ({filteredSamples.length} Games)</div>
              <div className="badge default">
                {filteredSamples.filter(s => !excludedIndices.includes(s._idx) && (rec === 'over' ? s.value > projection.line : s.value < projection.line)).length} / {filteredSamples.filter(s => !excludedIndices.includes(s._idx)).length} HIT RATE
              </div>
            </div>
            <div className="venue-filter-row">
              <button className={`venue-filter-btn ${venueFilter === 'all' ? 'active' : ''}`} onClick={() => setVenueFilter('all')} data-testid="venue-filter-all">All</button>
              <button className={`venue-filter-btn ${venueFilter === 'home' ? 'active' : ''}`} onClick={() => setVenueFilter('home')} data-testid="venue-filter-home">Home</button>
              <button className={`venue-filter-btn ${venueFilter === 'away' ? 'active' : ''}`} onClick={() => setVenueFilter('away')} data-testid="venue-filter-away">Away</button>
            </div>
            <div className="samples-grid">
              {filteredSamples.map((sample) => {
                const excluded = excludedIndices.includes(sample._idx);
                const isHit = rec === 'over' ? sample.value > projection.line : sample.value < projection.line;
                const cls = excluded ? 'excluded' : isHit ? 'hit' : 'miss';
                const diffColor = sample.matchDifficulty === 'high' ? '#f43f5e' : sample.matchDifficulty === 'medium' ? '#f59e0b' : '#10b981';
                return (
                  <div key={sample._idx} className={`sample-cell ${cls}`} onClick={() => onToggleSample(sample._idx)}
                    title={`${sample.date} vs ${sample.opponent} (${sample.matchDifficulty}) - ${sample.venue || 'unknown'}`}>
                    <div className="difficulty-dot" style={{ background: diffColor }} />
                    <span className="sample-value">{sample.value}</span>
                    <span className="sample-minutes">{sample.minutesPlayed}'</span>
                    <span className="sample-venue-tag">{sample.venue === 'home' ? 'H' : 'A'}</span>
                    <span className="sample-opponent">{(sample.opponent || '').substring(0, 3)}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="space-y-4 mt-6">
          <div className="grid-2">
            <div className="stat-box">
              <div className="stat-label">Position</div>
              <div style={{ fontSize: 13, fontWeight: 700 }}>{projection.player?.position}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Tactical Role</div>
              <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--accent)' }}>{projection.player?.role}</div>
            </div>
          </div>

          {projection.bayesianMetrics && (
            <div className="stat-box">
              <div className="stat-label flex items-center gap-2">
                <Zap style={{ width: 12, height: 12, color: 'var(--accent)' }} /> Bayesian Model Metrics
              </div>
              <div className="grid-2 mt-2">
                <div>
                  <div style={{ fontSize: 8, fontWeight: 800, textTransform: 'uppercase', color: 'var(--text-muted)' }}>Prior Mean</div>
                  <div style={{ fontSize: 14, fontWeight: 900 }}>{projection.bayesianMetrics.priorMean}</div>
                </div>
                <div>
                  <div style={{ fontSize: 8, fontWeight: 800, textTransform: 'uppercase', color: 'var(--text-muted)' }}>Momentum</div>
                  <div style={{ fontSize: 14, fontWeight: 900, color: 'var(--accent)' }}>
                    {projection.bayesianMetrics.momentumEffect > 0 ? '+' : ''}{projection.bayesianMetrics.momentumEffect}
                  </div>
                </div>
              </div>
            </div>
          )}

          <div className="space-y-2">
            <div className="stat-label flex items-center gap-2"><BarChart3 style={{ width: 12, height: 12 }} /> Model Reasoning</div>
            <p className="reasoning-text">{projection.reasoning}</p>
          </div>
        </div>
      </div>

      <button className="btn-primary" onClick={onSave} data-testid="save-to-tracking-btn">
        Save to Tracking
      </button>
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState('predict');
  const [trackingView, setTrackingView] = useState('live');

  const [wizardStep, setWizardStep] = useState(1);
  const [wizardData, setWizardData] = useState({});
  const [wizardError, setWizardError] = useState(null);
  const [searchMode, setSearchMode] = useState('wizard');
  const [naturalQuery, setNaturalQuery] = useState('');
  const [isParsingQuery, setIsParsingQuery] = useState(false);

  const [teams, setTeams] = useState([]);
  const [isTeamsLoading, setIsTeamsLoading] = useState(false);
  const [wizardPlayers, setWizardPlayers] = useState([]);
  const [isPlayersLoading, setIsPlayersLoading] = useState(false);

  const [projection, setProjection] = useState(null);
  const [isProjecting, setIsProjecting] = useState(false);
  const [excludedSampleIndices, setExcludedSampleIndices] = useState([]);

  const [savedPicks, setSavedPicks] = useState([]);
  const [selectedPick, setSelectedPick] = useState(null);

  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [isChatting, setIsChatting] = useState(false);
  const [chatSessionId, setChatSessionId] = useState(null);
  const [apiStatus, setApiStatus] = useState('checking');

  const searchTimeout = useRef(null);
  const chatEndRef = useRef(null);

  useEffect(() => {
    const saved = localStorage.getItem('reverse_picks_v2');
    if (saved) setSavedPicks(JSON.parse(saved));
    checkApiStatus().then(ok => setApiStatus(ok ? 'online' : 'offline')).catch(() => setApiStatus('offline'));
  }, []);

  useEffect(() => {
    localStorage.setItem('reverse_picks_v2', JSON.stringify(savedPicks));
  }, [savedPicks]);

  useEffect(() => {
    if (chatEndRef.current) chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages]);

  const handleStartChat = useCallback(async () => {
    try {
      const data = await startChat();
      setChatSessionId(data.session_id);
      setChatMessages([{ role: 'model', text: data.message }]);
    } catch (err) {
      setChatMessages([{ role: 'model', text: 'Failed to connect. Please try again.' }]);
    }
  }, []);

  useEffect(() => {
    if (activeTab === 'chat' && !chatSessionId) handleStartChat();
  }, [activeTab, chatSessionId, handleStartChat]);

  const handleSendMessage = async () => {
    if (!chatInput.trim() || !chatSessionId) return;
    const msg = chatInput;
    setChatInput('');
    setChatMessages(prev => [...prev, { role: 'user', text: msg }]);
    setIsChatting(true);
    try {
      const data = await sendChatMessage(chatSessionId, msg);
      setChatMessages(prev => [...prev, { role: 'model', text: data.response }]);
    } catch {
      setChatMessages(prev => [...prev, { role: 'model', text: 'Error connecting to tactical uplink. Please try again.' }]);
    } finally {
      setIsChatting(false);
    }
  };

  const handleLeagueSelect = async (leagueId) => {
    setWizardData({ ...wizardData, leagueId });
    setWizardStep(2);
    setIsTeamsLoading(true);
    try {
      const data = await getTeamsByLeague(leagueId);
      setTeams(data.teams || []);
    } catch {
      setTeams([]);
    } finally {
      setIsTeamsLoading(false);
    }
  };

  const handlePlayerSearch = (query) => {
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    if (query.length < 3) { setWizardPlayers([]); return; }
    searchTimeout.current = setTimeout(async () => {
      setIsPlayersLoading(true);
      setWizardError(null);
      try {
        const data = await searchPlayers(query, wizardData.leagueId);
        setWizardPlayers(data.players || []);
      } catch (err) {
        setWizardError(err.message);
      } finally {
        setIsPlayersLoading(false);
      }
    }, 500);
  };

  const handlePlayerSelect = (player) => {
    setWizardData({ ...wizardData, playerId: player.id, playerName: player.name, teamId: player.teamId });
    setWizardStep(3);
  };

  const handleOpponentSelect = (team) => {
    setWizardData({ ...wizardData, opponentId: team.id, opponentName: team.name });
    setWizardStep(4);
  };

  const runProjection = async (data) => {
    setIsProjecting(true);
    setWizardError(null);
    try {
      const result = await predict(data);
      if (!result || !result.player) throw new Error('AI model failed to generate a valid projection.');
      setProjection(result);
      setExcludedSampleIndices([]);
    } catch (err) {
      setWizardError(err.message || 'Projection failed.');
    } finally {
      setIsProjecting(false);
    }
  };

  const handleNaturalSearch = async (e) => {
    e.preventDefault();
    if (!naturalQuery.trim()) return;
    setIsParsingQuery(true);
    setWizardError(null);
    try {
      const parsed = await parseNaturalQuery(naturalQuery);
      if (!parsed.playerName) throw new Error('Could not identify player. Try a more specific query.');
      const playersData = await searchPlayers(parsed.playerName);
      const players = playersData.players || [];
      if (!players.length) throw new Error(`Player "${parsed.playerName}" not found.`);
      const player = players[0];
      const teamsData = await getTeamsByLeague(39);
      const leagueTeams = teamsData.teams || [];
      const opponent = leagueTeams.find(t => t.name.toLowerCase().includes((parsed.opponentName || '').toLowerCase())) || leagueTeams[0];
      await runProjection({
        leagueId: 39,
        playerId: player.id,
        playerName: player.name,
        teamId: player.teamId,
        opponentId: opponent?.id || 0,
        opponentName: opponent?.name || 'Unknown',
        venue: parsed.venue || 'home',
        propType: parsed.propType || 'pass_attempts',
        line: parsed.line || 0,
      });
    } catch (err) {
      setWizardError(err.message);
    } finally {
      setIsParsingQuery(false);
    }
  };

  const savePick = () => {
    if (!projection) return;
    const newPick = {
      ...projection,
      id: Math.random().toString(36).substring(2, 9),
      timestamp: Date.now(),
      status: 'live',
      result: 'pending',
      excludedSampleIndices,
    };
    const updated = [newPick, ...savedPicks];
    setSavedPicks(updated);
    setProjection(null);
    setExcludedSampleIndices([]);
    setWizardStep(1);
    setWizardData({});
    setActiveTab('tracking');
  };

  const removePick = (id, e) => {
    e.stopPropagation();
    const updated = savedPicks.filter(p => p.id !== id);
    setSavedPicks(updated);
  };

  const leaguesByType = (type) => SUPPORTED_LEAGUES.filter(l => l.type === type);

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header-logo">
          <div className="logo-icon"><Zap /></div>
          <div className="logo-text" data-testid="app-logo">Reverse<span>Picks</span></div>
        </div>
        <div className="header-right">
          <div className="api-badge">
            <div className={`api-dot ${apiStatus}`} data-testid="api-status-dot" />
            <span>API</span>
          </div>
          <div className="version-badge">v2.0.0</div>
          <button className="icon-btn" onClick={() => window.location.reload()} data-testid="refresh-btn">
            <RefreshCw />
          </button>
        </div>
      </header>

      {/* Main */}
      <main className="main-content">
        {/* PREDICT TAB */}
        {activeTab === 'predict' && (
          <div className="animate-fade-in space-y-6">
            {!projection && !isProjecting && (
              <>
                <div className="flex justify-between items-center">
                  <div>
                    <h2 className="section-title" data-testid="wizard-title">AI Wizard</h2>
                    <p className="section-subtitle">
                      {searchMode === 'wizard' ? `Step ${wizardStep} of 6` : 'Natural Language Search'}
                    </p>
                  </div>
                  {searchMode === 'wizard' && wizardStep > 1 && (
                    <button className="back-btn" onClick={() => setWizardStep(wizardStep - 1)} data-testid="wizard-back-btn">
                      <ArrowLeft /> Back
                    </button>
                  )}
                </div>

                <div className="tab-switcher">
                  <button className={`tab-btn ${searchMode === 'wizard' ? 'active' : ''}`}
                    onClick={() => setSearchMode('wizard')} data-testid="step-by-step-tab">Step-by-Step</button>
                  <button className={`tab-btn ${searchMode === 'natural' ? 'active' : ''}`}
                    onClick={() => setSearchMode('natural')} data-testid="natural-search-tab">Natural Search</button>
                </div>

                {searchMode === 'natural' && (
                  <form onSubmit={handleNaturalSearch} className="space-y-4">
                    <div className="search-input-wrap">
                      <Search className="search-icon" />
                      <input className="search-input" type="text" value={naturalQuery}
                        onChange={e => setNaturalQuery(e.target.value)}
                        placeholder="e.g. Lamine Yamal 52.5 passes vs Villarreal"
                        data-testid="natural-search-input" />
                    </div>
                    <button className="btn-primary" type="submit" disabled={isParsingQuery || !naturalQuery.trim()}
                      data-testid="analyze-query-btn">
                      {isParsingQuery ? <Loader2 className="animate-spin" /> : <Zap style={{ fill: 'currentColor' }} />}
                      {isParsingQuery ? 'Parsing Query...' : 'Analyze Query'}
                    </button>
                    {wizardError && (
                      <div className="error-box"><ShieldAlert /><p>{wizardError}</p></div>
                    )}
                  </form>
                )}

                {searchMode === 'wizard' && wizardStep === 1 && (
                  <div className="space-y-6" data-testid="league-list">
                    {['Domestic', 'International Club', 'International Team'].map(type => {
                      const leagues = leaguesByType(type);
                      if (!leagues.length) return null;
                      return (
                        <div key={type} className="space-y-3">
                          <div className="category-label">{type}</div>
                          <div className="space-y-2">
                            {leagues.map(league => (
                              <div key={league.id} className="card card-clickable" onClick={() => handleLeagueSelect(league.id)}
                                data-testid={`league-${league.id}`}>
                                <div className="league-item">
                                  <span className="name">{league.name}</span>
                                  <ChevronRight />
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 2 && (
                  <div className="space-y-4" data-testid="player-search-step">
                    {wizardError && <div className="error-box"><ShieldAlert /><p>{wizardError}</p></div>}
                    <div className="search-input-wrap">
                      <Search className="search-icon" />
                      <input className="search-input" type="text" placeholder="Search player name..."
                        onChange={e => handlePlayerSearch(e.target.value)} data-testid="player-search-input" />
                      {isPlayersLoading && <RefreshCw className="animate-spin" style={{ position: 'absolute', right: 14, top: '50%', transform: 'translateY(-50%)', width: 16, height: 16, color: 'var(--accent)' }} />}
                    </div>
                    <div className="space-y-2" style={{ maxHeight: 400, overflowY: 'auto' }}>
                      {wizardPlayers.map(player => (
                        <div key={player.id} className="card card-clickable" onClick={() => handlePlayerSelect(player)}
                          data-testid={`player-${player.id}`}>
                          <div className="player-item">
                            <div className="player-avatar"><User /></div>
                            <div>
                              <div className="player-name">{player.name}</div>
                              <div className="player-team">{player.teamName}</div>
                            </div>
                          </div>
                        </div>
                      ))}
                      {!wizardPlayers.length && !isPlayersLoading && (
                        <div className="text-center" style={{ padding: '32px 0', color: 'var(--text-secondary)', fontSize: 13 }}>
                          Type at least 3 characters to search players.
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 3 && (
                  <div className="space-y-4" data-testid="opponent-select-step">
                    <div className="stat-label">Select Opponent</div>
                    {isTeamsLoading ? (
                      <div className="loading-wrap"><div className="spinner-ring" /></div>
                    ) : teams.length > 0 ? (
                      <div className="teams-grid">
                        {teams.map(team => (
                          <div key={team.id} className="card card-clickable" onClick={() => handleOpponentSelect(team)}
                            data-testid={`team-${team.id}`}>
                            <div className="team-card">
                              <div className="team-icon"><Shield /></div>
                              <div className="team-name">{team.name}</div>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-center" style={{ padding: '32px 0', color: 'var(--text-secondary)', fontSize: 13 }}>
                        No teams found. <button className="btn-secondary mt-4" onClick={() => setWizardStep(1)}>Go to League Selection</button>
                      </div>
                    )}
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 4 && (
                  <div className="venue-grid" data-testid="venue-select-step">
                    <button className="venue-btn" onClick={() => { setWizardData({ ...wizardData, venue: 'home' }); setWizardStep(5); }}
                      data-testid="venue-home-btn">
                      <span className="label">Home</span>
                      <span className="sub">Venue</span>
                    </button>
                    <button className="venue-btn" onClick={() => { setWizardData({ ...wizardData, venue: 'away' }); setWizardStep(5); }}
                      data-testid="venue-away-btn">
                      <span className="label">Away</span>
                      <span className="sub">Venue</span>
                    </button>
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 5 && (
                  <div className="space-y-2" data-testid="prop-type-step">
                    {PROP_TYPES.map(type => (
                      <div key={type} className="card card-clickable" onClick={() => { setWizardData({ ...wizardData, propType: type }); setWizardStep(6); }}
                        data-testid={`prop-${type}`}>
                        <div className="prop-item">
                          <span className="name">{type.replace('_', ' ')}</span>
                          <ChevronRight />
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 6 && (
                  <div className="space-y-6" data-testid="line-set-step">
                    <div className="line-setter">
                      <div className="line-setter-label">Set Prop Line</div>
                      <div className="line-setter-row">
                        <button className="line-btn" onClick={() => setWizardData({ ...wizardData, line: Math.max(0, (wizardData.line || 0) - 0.5) })}
                          data-testid="line-decrease-btn">-</button>
                        <input className="line-input" type="number" step="0.5" placeholder="0.0"
                          value={wizardData.line === 0 ? '' : wizardData.line || ''}
                          onChange={e => setWizardData({ ...wizardData, line: parseFloat(e.target.value) || 0 })}
                          data-testid="line-input" />
                        <button className="line-btn" onClick={() => setWizardData({ ...wizardData, line: (wizardData.line || 0) + 0.5 })}
                          data-testid="line-increase-btn">+</button>
                      </div>
                    </div>
                    {wizardError && <div className="error-box"><ShieldAlert /><p>{wizardError}</p></div>}
                    <button className="btn-primary" onClick={() => runProjection(wizardData)} data-testid="generate-projection-btn">
                      <Zap style={{ fill: 'currentColor' }} /> Generate Projection
                    </button>
                  </div>
                )}
              </>
            )}

            {isProjecting && (
              <div className="loading-wrap">
                <div className="spinner-ring">
                  <Zap className="inner-icon" style={{ width: 28, height: 28 }} />
                </div>
                <div className="loading-title">Analyzing Matchup...</div>
                <div className="loading-sub">Running Bayesian simulations & searching live data</div>
              </div>
            )}

            {projection && !isProjecting && (
              <div className="space-y-6">
                <button className="back-btn" onClick={() => { setProjection(null); setWizardStep(1); setWizardData({}); }}
                  data-testid="back-to-search-btn">
                  <ArrowLeft /> Back to Search
                </button>
                <ProjectionCard
                  projection={projection}
                  onSave={savePick}
                  excludedIndices={excludedSampleIndices}
                  onToggleSample={idx => setExcludedSampleIndices(prev =>
                    prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx]
                  )}
                />
              </div>
            )}
          </div>
        )}

        {/* TRACKING TAB */}
        {activeTab === 'tracking' && (
          <div className="animate-fade-in space-y-6" data-testid="tracking-tab">
            <div className="flex justify-between items-center">
              <h2 className="section-title">Tracking</h2>
              <div className="tab-switcher" style={{ width: 'auto' }}>
                <button className={`tab-btn ${trackingView === 'live' ? 'active' : ''}`}
                  onClick={() => setTrackingView('live')} data-testid="tracking-live-btn">Live</button>
                <button className={`tab-btn ${trackingView === 'history' ? 'active' : ''}`}
                  onClick={() => setTrackingView('history')} data-testid="tracking-history-btn">History</button>
              </div>
            </div>

            <div className="space-y-4">
              {savedPicks.filter(p => trackingView === 'live' ? p.status === 'live' : p.status === 'settled').length === 0 ? (
                <div className="empty-state" data-testid="tracking-empty">
                  <div className="empty-icon"><Clock /></div>
                  <p className="empty-text">No {trackingView} picks being tracked.</p>
                </div>
              ) : (
                savedPicks.filter(p => trackingView === 'live' ? p.status === 'live' : p.status === 'settled').map(pick => (
                  <div key={pick.id} className="card card-clickable" onClick={() => setSelectedPick(pick)}
                    data-testid={`pick-${pick.id}`}>
                    <div className="pick-card">
                      <div className="pick-status-row">
                        <div className="status-indicator">
                          <div className={`status-dot ${pick.status}`} />
                          <span className="status-label">{pick.status}</span>
                        </div>
                        <div className="pick-actions">
                          <div className={`rec-tag ${pick.recommendation}`}>{pick.recommendation}</div>
                          <button className="remove-btn" onClick={e => removePick(pick.id, e)} data-testid={`remove-pick-${pick.id}`}>
                            <Trash2 style={{ width: 14, height: 14 }} />
                          </button>
                        </div>
                      </div>
                      <div className="pick-info">
                        <div>
                          <div className="pick-player-name">{pick.player?.name}</div>
                          <div className="pick-matchup">{pick.player?.team} vs {pick.opponent}</div>
                        </div>
                        <div style={{ textAlign: 'right' }}>
                          <div className="pick-line-label">Line</div>
                          <div className="pick-line-value">{pick.line}</div>
                        </div>
                      </div>
                      <div className="pick-stats-grid">
                        <div className="pick-stat">
                          <div className="pick-stat-label">Proj</div>
                          <div className="pick-stat-value accent">{pick.projectedValue}</div>
                        </div>
                        <div className="pick-stat">
                          <div className="pick-stat-label">Conf</div>
                          <div className="pick-stat-value">{pick.confidenceScore}%</div>
                        </div>
                        <div className="pick-stat">
                          <div className="pick-stat-label">95% CI</div>
                          <div className="pick-stat-value" style={{ fontSize: 9 }}>
                            {pick.confidenceInterval?.[0]}-{pick.confidenceInterval?.[1]}
                          </div>
                        </div>
                        <div className="pick-stat">
                          <div className="pick-stat-label">Hit Rate</div>
                          <div className="pick-stat-value warning">
                            {pick.recentSamples?.length > 0
                              ? Math.round((pick.recentSamples.filter(s => pick.recommendation === 'over' ? s.value > pick.line : s.value < pick.line).length / pick.recentSamples.length) * 100)
                              : 0}%
                          </div>
                        </div>
                      </div>
                      <div className="pick-footer">
                        <div className="flex items-center gap-2">
                          <BarChart3 style={{ width: 12, height: 12 }} />
                          <span style={{ fontFamily: 'JetBrains Mono' }}>ID: {pick.player?.id}</span>
                        </div>
                        <span>{new Date(pick.timestamp).toLocaleDateString()}</span>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {/* CHAT TAB */}
        {activeTab === 'chat' && (
          <div className="animate-fade-in chat-container" data-testid="chat-tab">
            <div className="chat-header">
              <div>
                <h2 className="chat-title">Tactical Uplink</h2>
                <p className="chat-subtitle">AI Strategic Analyst</p>
              </div>
              <button className="icon-btn" onClick={handleStartChat} data-testid="chat-reset-btn">
                <RefreshCw />
              </button>
            </div>

            <div className="chat-messages" data-testid="chat-messages">
              {chatMessages.map((msg, i) => (
                <div key={i} className={`chat-msg ${msg.role}`} data-testid={`chat-msg-${i}`}>
                  {msg.text}
                </div>
              ))}
              {isChatting && (
                <div className="chat-msg model">
                  <Loader2 className="animate-spin" style={{ width: 16, height: 16, color: 'var(--accent)' }} />
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            <div className="chat-input-wrap">
              <input className="chat-input" type="text" value={chatInput}
                onChange={e => setChatInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleSendMessage()}
                placeholder="Ask for tactical insights..."
                data-testid="chat-input" />
              <button className="chat-send-btn" onClick={handleSendMessage}
                disabled={isChatting || !chatInput.trim()} data-testid="chat-send-btn">
                <Send />
              </button>
            </div>
          </div>
        )}
      </main>

      {/* Selected Pick Modal */}
      {selectedPick && (
        <div className="modal-overlay" data-testid="pick-detail-modal">
          <div className="modal-content space-y-6">
            <button className="back-btn" onClick={() => setSelectedPick(null)} data-testid="close-modal-btn">
              <ArrowLeft /> Back to Tracking
            </button>
            <div>
              <span className="badge neon">Analysis Detail</span>
              <h2 style={{ fontSize: 32, fontWeight: 900, letterSpacing: -0.5, marginTop: 8 }}>{selectedPick.player?.name}</h2>
              <p style={{ color: 'var(--text-secondary)' }}>{selectedPick.player?.team} vs {selectedPick.opponent}</p>
            </div>
            <div className="projection-card">
              <div className="grid-2 mb-4">
                <div className="stat-box">
                  <div className="stat-label">Prop Line</div>
                  <div className="stat-value">{selectedPick.line} <span className="stat-suffix">{selectedPick.propType?.replace('_', ' ')}</span></div>
                </div>
                <div className="stat-box">
                  <div className="stat-label">Projected</div>
                  <div className="stat-value accent">{selectedPick.projectedValue}</div>
                </div>
              </div>
              <div className={`rec-banner ${selectedPick.recommendation}`}>
                <div className={`rec-label ${selectedPick.recommendation}`}>
                  {selectedPick.recommendation === 'over' ? <TrendingUp /> : <TrendingDown />}
                  <span>{selectedPick.recommendation}</span>
                </div>
                <span className={`badge ${selectedPick.recommendation === 'over' ? 'neon' : 'danger'}`}>
                  {selectedPick.confidenceLevel}
                </span>
              </div>
              {selectedPick.reasoning && (
                <div className="mt-4">
                  <div className="stat-label">Reasoning</div>
                  <p className="reasoning-text mt-2">{selectedPick.reasoning}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Bottom Nav */}
      <nav className="bottom-nav" data-testid="bottom-nav">
        <div className="nav-items">
          <button className={`nav-item ${activeTab === 'predict' ? 'active' : ''}`}
            onClick={() => setActiveTab('predict')} data-testid="nav-predict">
            <Zap />
            <span>Predict</span>
          </button>
          <button className={`nav-item ${activeTab === 'tracking' ? 'active' : ''}`}
            onClick={() => setActiveTab('tracking')} data-testid="nav-tracking">
            <Activity />
            <span>Tracking</span>
          </button>
          <button className={`nav-item ${activeTab === 'chat' ? 'active' : ''}`}
            onClick={() => setActiveTab('chat')} data-testid="nav-chat">
            <MessageSquare />
            <span>Chat</span>
          </button>
        </div>
      </nav>
    </div>
  );
}
