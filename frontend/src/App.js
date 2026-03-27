import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Zap, ChevronRight, RefreshCw, ArrowLeft, Clock, Activity,
  Shield, Send, Loader2, Trash2, User, Search,
  TrendingUp, TrendingDown, BarChart3, ShieldAlert, Target, LogOut, Lock, Mail
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';
import {
  getTeamsByLeague, searchPlayers, predict, startChat, sendChatMessage,
  checkApiStatus, SUPPORTED_LEAGUES,
  verifyWhop, authLogin, setPassword as apiSetPassword, resetPassword, verifySession, authLogout,
  getPickOfTheDay, settlePicks
} from './api';
import './App.css';

const PROP_TYPES = [
  { key: 'pass_attempts', label: 'Pass Attempts', stat: 'passes.total', desc: 'Total passes attempted' },
  { key: 'shots', label: 'Shots', stat: 'shots.total', desc: 'Total shots taken' },
  { key: 'shots_on_target', label: 'Shots on Target', stat: 'shots.on', desc: 'Shots on goal' },
  { key: 'tackles', label: 'Tackles', stat: 'tackles.total', desc: 'Total tackles won' },
  { key: 'key_passes', label: 'Key Passes', stat: 'passes.key', desc: 'Passes leading to a shot' },
  { key: 'saves', label: 'Saves', stat: 'goals.saves', desc: 'Goalkeeper saves' },
  { key: 'interceptions', label: 'Interceptions', stat: 'tackles.interceptions', desc: 'Passes intercepted' },
  { key: 'blocks', label: 'Blocks', stat: 'tackles.blocks', desc: 'Shots/passes blocked' },
  { key: 'dribbles', label: 'Dribble Attempts', stat: 'dribbles.attempts', desc: 'Dribble attempts made' },
  { key: 'fouls_drawn', label: 'Fouls Drawn', stat: 'fouls.drawn', desc: 'Fouls won by player' },
];

function getPropLabel(key) {
  const p = PROP_TYPES.find(pt => pt.key === key);
  return p ? p.label : key.replace(/_/g, ' ');
}

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

function MatchStatZones({ teamStats, opponentStats, venue }) {
  if (!teamStats?.length && !opponentStats?.length) return null;

  const playerVenue = (venue || 'home').toLowerCase();
  const oppVenue = playerVenue === 'home' ? 'away' : 'home';

  const StatBar = ({ label, value, max, color }) => {
    const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
    return (
      <div style={{ marginBottom: 6 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>
          <span>{label}</span>
          <span style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{value}</span>
        </div>
        <div style={{ height: 6, background: 'rgba(255,255,255,0.06)', borderRadius: 3, overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width 0.6s ease' }} />
        </div>
      </div>
    );
  };

  const avgStat = (stats, key) => {
    const vals = stats.map(s => {
      const v = s[key];
      return typeof v === 'string' ? parseFloat(v) : v;
    }).filter(v => v != null && !isNaN(v));
    return vals.length ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length * 10) / 10 : 0;
  };

  const teamAvg = teamStats?.length ? {
    possession: avgStat(teamStats, 'possession'),
    shots: avgStat(teamStats, 'totalShots'),
    shotsOnTarget: avgStat(teamStats, 'shotsOnTarget'),
    insideBox: avgStat(teamStats, 'shotsInsideBox'),
    outsideBox: avgStat(teamStats, 'shotsOutsideBox'),
    passes: avgStat(teamStats, 'totalPasses'),
    passAcc: avgStat(teamStats, 'passAccuracy'),
  } : null;

  const oppAvg = opponentStats?.length ? {
    possession: avgStat(opponentStats, 'possession'),
    shots: avgStat(opponentStats, 'totalShots'),
    shotsOnTarget: avgStat(opponentStats, 'shotsOnTarget'),
    insideBox: avgStat(opponentStats, 'shotsInsideBox'),
    outsideBox: avgStat(opponentStats, 'shotsOutsideBox'),
    passes: avgStat(opponentStats, 'totalPasses'),
    passAcc: avgStat(opponentStats, 'passAccuracy'),
  } : null;

  return (
    <div className="stat-box" style={{ borderColor: 'rgba(99,102,241,0.2)', background: 'rgba(99,102,241,0.03)' }} data-testid="match-stat-zones">
      <div className="stat-label flex items-center gap-2" style={{ marginBottom: 12 }}>
        <BarChart3 style={{ width: 12, height: 12, color: '#6366f1' }} /> Match Stat Zones (Venue-Filtered)
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: teamAvg && oppAvg ? '1fr 1fr' : '1fr', gap: 16 }}>
        {teamAvg && (
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: '#10b981', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>Team ({playerVenue})</div>
            <StatBar label="Possession" value={`${teamAvg.possession}%`} max={100} color="#10b981" />
            <StatBar label="Total Shots" value={teamAvg.shots} max={20} color="#10b981" />
            <StatBar label="Shots On Target" value={teamAvg.shotsOnTarget} max={10} color="#10b981" />
            <StatBar label="Inside Box" value={teamAvg.insideBox} max={15} color="#6366f1" />
            <StatBar label="Outside Box" value={teamAvg.outsideBox} max={10} color="#8b5cf6" />
            <StatBar label="Total Passes" value={teamAvg.passes} max={700} color="#10b981" />
            <StatBar label="Pass Accuracy" value={`${teamAvg.passAcc}%`} max={100} color="#10b981" />
          </div>
        )}
        {oppAvg && (
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: '#f43f5e', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>Opponent ({oppVenue})</div>
            <StatBar label="Possession" value={`${oppAvg.possession}%`} max={100} color="#f43f5e" />
            <StatBar label="Total Shots" value={oppAvg.shots} max={20} color="#f43f5e" />
            <StatBar label="Shots On Target" value={oppAvg.shotsOnTarget} max={10} color="#f43f5e" />
            <StatBar label="Inside Box" value={oppAvg.insideBox} max={15} color="#ef4444" />
            <StatBar label="Outside Box" value={oppAvg.outsideBox} max={10} color="#dc2626" />
            <StatBar label="Total Passes" value={oppAvg.passes} max={700} color="#f43f5e" />
            <StatBar label="Pass Accuracy" value={`${oppAvg.passAcc}%`} max={100} color="#f43f5e" />
          </div>
        )}
      </div>
    </div>
  );
}

function H2HSection({ h2hData, propType }) {
  if (!h2hData || !h2hData.matches || h2hData.matches.length === 0) return null;

  const propLabel = (propType || '').replace(/_/g, ' ');

  return (
    <div className="stat-box" style={{ borderColor: 'rgba(245,158,11,0.2)', background: 'rgba(245,158,11,0.04)' }} data-testid="h2h-section">
      <div className="stat-label flex items-center gap-2" style={{ marginBottom: 10 }}>
        <Activity style={{ width: 12, height: 12, color: '#f59e0b' }} /> H2H vs Opponent ({h2hData.sampleSize} meetings)
      </div>

      {h2hData.avgVsOpponent != null && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
          <div style={{ flex: 1, background: 'rgba(245,158,11,0.08)', borderRadius: 8, padding: '8px 12px', textAlign: 'center' }}>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>AVG</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: '#f59e0b' }}>{h2hData.avgVsOpponent}</div>
          </div>
          <div style={{ flex: 1, background: 'rgba(16,185,129,0.08)', borderRadius: 8, padding: '8px 12px', textAlign: 'center' }}>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>HIGH</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: '#10b981' }}>{h2hData.maxVsOpponent}</div>
          </div>
          <div style={{ flex: 1, background: 'rgba(244,63,94,0.08)', borderRadius: 8, padding: '8px 12px', textAlign: 'center' }}>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>LOW</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: '#f43f5e' }}>{h2hData.minVsOpponent}</div>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {h2hData.matches.map((m, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: 6, fontSize: 11 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span style={{ color: 'var(--text-muted)', minWidth: 65 }}>{(m.date || '').slice(0, 10)}</span>
              <span style={{ color: 'var(--text-secondary)' }}>vs {m.opponent}</span>
              <span style={{
                fontSize: 9, padding: '1px 6px', borderRadius: 4, fontWeight: 700,
                background: m.venue === 'home' ? 'rgba(16,185,129,0.1)' : 'rgba(99,102,241,0.1)',
                color: m.venue === 'home' ? '#10b981' : '#6366f1'
              }}>{m.venue === 'home' ? 'H' : 'A'}</span>
              <span style={{
                fontSize: 9, padding: '1px 6px', borderRadius: 4, fontWeight: 700,
                background: (m.date && m.matchScore) ? 'rgba(255,255,255,0.06)' : 'transparent',
                color: 'var(--text-muted)'
              }}>{m.matchScore}</span>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>{m.minutesPlayed}'</span>
              <span style={{
                fontWeight: 800, fontSize: 14,
                color: m.targetStat != null && h2hData.avgVsOpponent != null
                  ? (m.targetStat >= h2hData.avgVsOpponent ? '#10b981' : '#f43f5e')
                  : 'var(--text-primary)'
              }}>
                {m.targetStat != null ? m.targetStat : '—'}
              </span>
              <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>{propLabel}</span>
            </div>
          </div>
        ))}
      </div>
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
              <span className="stat-suffix">{getPropLabel(projection.propType)}</span>
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

        {/* Match Stat Zones - Visual heat map of team vs opponent */}
        <MatchStatZones teamStats={projection.teamMatchStats} opponentStats={projection.opponentMatchStats} venue={projection._request?.venue || 'home'} />

        {/* H2H Player Stats */}
        <H2HSection h2hData={projection.h2hPlayerStats} propType={projection.propType} />

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
          {/* Sharp Summary - the headline */}
          {projection.sharpSummary && (
            <div className="stat-box" style={{ borderColor: 'rgba(16,185,129,0.2)', background: 'rgba(16,185,129,0.04)' }}>
              <div className="stat-label flex items-center gap-2">
                <Target style={{ width: 12, height: 12, color: 'var(--accent)' }} /> Sharp Take
              </div>
              <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.6, marginTop: 6 }}>
                {projection.sharpSummary}
              </p>
            </div>
          )}

          {/* Key Evidence - quoted stats */}
          {projection.keyEvidence && (
            <div className="stat-box" style={{ borderColor: 'rgba(99,102,241,0.2)', background: 'rgba(99,102,241,0.04)' }}>
              <div className="stat-label flex items-center gap-2">
                <BarChart3 style={{ width: 12, height: 12, color: '#6366f1' }} /> Key Evidence
              </div>
              <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.7, marginTop: 6, fontFamily: 'monospace' }}>
                {projection.keyEvidence}
              </p>
            </div>
          )}

          {/* Scenario Analysis */}
          {projection.scenarioAnalysis && (
            <div className="stat-box" style={{ borderColor: 'rgba(245,158,11,0.2)', background: 'rgba(245,158,11,0.04)' }}>
              <div className="stat-label flex items-center gap-2">
                <Activity style={{ width: 12, height: 12, color: '#f59e0b' }} /> Scenario Analysis
              </div>
              <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.7, marginTop: 6 }}>
                {projection.scenarioAnalysis}
              </p>
            </div>
          )}

          {/* Uncertainty Note */}
          {projection.uncertaintyNote && (
            <div className="stat-box" style={{ borderColor: 'rgba(244,63,94,0.15)', background: 'rgba(244,63,94,0.03)' }}>
              <div className="stat-label flex items-center gap-2">
                <ShieldAlert style={{ width: 12, height: 12, color: '#f43f5e' }} /> Risk Factor
              </div>
              <p style={{ fontSize: 12, color: '#f43f5e', lineHeight: 1.6, marginTop: 6, fontWeight: 600 }}>
                {projection.uncertaintyNote}
              </p>
            </div>
          )}

          {/* Sensitivity Tests */}
          {projection.sensitivityTests && (
            <div className="stat-box" style={{ borderColor: 'rgba(168,85,247,0.2)', background: 'rgba(168,85,247,0.04)' }}>
              <div className="stat-label flex items-center gap-2">
                <Shield style={{ width: 12, height: 12, color: '#a855f7' }} /> Sensitivity Tests
              </div>
              <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.7, marginTop: 6 }}>
                {projection.sensitivityTests}
              </p>
            </div>
          )}

          {/* Sub Risk + Game Flow in a grid */}
          {(projection.subRisk || projection.gameFlowDynamics) && (
            <div className="grid-2">
              {projection.subRisk && (
                <div className="stat-box" style={{ borderColor: 'rgba(245,158,11,0.15)' }}>
                  <div className="stat-label flex items-center gap-2">
                    <Clock style={{ width: 12, height: 12, color: '#f59e0b' }} /> Sub Risk
                  </div>
                  <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 4 }}>
                    {projection.subRisk}
                  </p>
                </div>
              )}
              {projection.gameFlowDynamics && (
                <div className="stat-box" style={{ borderColor: 'rgba(16,185,129,0.15)' }}>
                  <div className="stat-label flex items-center gap-2">
                    <TrendingUp style={{ width: 12, height: 12, color: 'var(--accent)' }} /> Game Flow
                  </div>
                  <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 4 }}>
                    {projection.gameFlowDynamics}
                  </p>
                </div>
              )}
            </div>
          )}

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

          {/* Full Analysis */}
          <div className="space-y-2">
            <div className="stat-label flex items-center gap-2"><BarChart3 style={{ width: 12, height: 12 }} /> Analysis</div>
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

function LoginPage({ onAuth }) {
  const [step, setStep] = useState('email'); // email, password, setup, reset
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [accessType, setAccessType] = useState(null);

  const handleEmailSubmit = async (e) => {
    e.preventDefault();
    if (!email.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await verifyWhop(email);
      if (res.verified) {
        localStorage.setItem('rp_email', res.email);
        localStorage.setItem('rp_token', res.session_token);
        localStorage.setItem('rp_access', res.access_type);
        onAuth({ email: res.email, token: res.session_token, accessType: res.access_type });
      } else if (res.requires_password) {
        setStep('password');
      } else if (res.requires_password_setup) {
        setAccessType(res.access_type);
        setStep('setup');
      } else {
        setError(res.message || 'No active membership found.');
      }
    } catch (err) {
      setError(err.message || 'Verification failed.');
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    if (!password) return;
    setLoading(true);
    setError(null);
    try {
      const res = await authLogin(email, password);
      if (res.verified) {
        localStorage.setItem('rp_email', res.email);
        localStorage.setItem('rp_token', res.session_token);
        localStorage.setItem('rp_access', res.access_type);
        onAuth({ email: res.email, token: res.session_token, accessType: res.access_type });
      } else {
        setError(res.message || 'Login failed.');
      }
    } catch (err) {
      setError(err.message || 'Login failed.');
    } finally {
      setLoading(false);
    }
  };

  const handleSetPassword = async (e) => {
    e.preventDefault();
    if (password.length < 6) { setError('Password must be at least 6 characters.'); return; }
    if (password !== confirmPassword) { setError('Passwords do not match.'); return; }
    setLoading(true);
    setError(null);
    try {
      const res = await apiSetPassword(email, password);
      if (res.verified) {
        localStorage.setItem('rp_email', res.email);
        localStorage.setItem('rp_token', res.session_token);
        localStorage.setItem('rp_access', res.access_type);
        onAuth({ email: res.email, token: res.session_token, accessType: res.access_type });
      }
    } catch (err) {
      setError(err.message || 'Failed to set password.');
    } finally {
      setLoading(false);
    }
  };

  const handleResetPassword = async (e) => {
    e.preventDefault();
    if (password.length < 6) { setError('Password must be at least 6 characters.'); return; }
    if (password !== confirmPassword) { setError('Passwords do not match.'); return; }
    setLoading(true);
    setError(null);
    try {
      const res = await resetPassword(email, password);
      if (res.verified) {
        localStorage.setItem('rp_email', res.email);
        localStorage.setItem('rp_token', res.session_token);
        localStorage.setItem('rp_access', res.access_type);
        onAuth({ email: res.email, token: res.session_token, accessType: res.access_type });
      }
    } catch (err) {
      setError(err.message || 'Failed to reset password.');
    } finally {
      setLoading(false);
    }
  };

  const startForgotPassword = async () => {
    setLoading(true);
    setError(null);
    setPassword('');
    setConfirmPassword('');
    try {
      const res = await verifyWhop(email);
      if (res.verified) {
        // Owner - just log them in
        localStorage.setItem('rp_email', res.email);
        localStorage.setItem('rp_token', res.session_token);
        localStorage.setItem('rp_access', res.access_type);
        onAuth({ email: res.email, token: res.session_token, accessType: res.access_type });
      } else if (res.requires_password || res.requires_password_setup) {
        setAccessType(res.access_type || accessType || 'Member');
        setStep('reset');
      } else {
        setError(res.message || 'No active membership found. Cannot reset password.');
      }
    } catch (err) {
      setError(err.message || 'Verification failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page" data-testid="login-page">
      <div className="login-bg-glow" />
      <div className="login-bg-logo">
        <img src="/rp-logo.png" alt="" />
      </div>
      <div className="login-container">
        <div className="login-logo">
          <img src="/rp-logo.png" alt="ReversePicks" className="login-logo-img" />
          <div className="logo-text" style={{ fontSize: 28 }}>Reverse<span>Picks</span></div>
          <p style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.15em', textTransform: 'uppercase', color: 'var(--text-muted)', marginTop: 4 }}>Elite Prop Intelligence</p>
        </div>

        {step === 'email' && (
          <form onSubmit={handleEmailSubmit} className="login-form" data-testid="email-form">
            <div className="login-field">
              <div className="login-field-icon"><Mail style={{ width: 16, height: 16 }} /></div>
              <input type="email" placeholder="Enter your email" value={email}
                onChange={e => setEmail(e.target.value)} autoFocus data-testid="email-input" />
            </div>
            <button className="btn-primary" type="submit" disabled={loading || !email.trim()} data-testid="verify-btn">
              {loading ? <Loader2 className="animate-spin" /> : <Zap style={{ fill: 'currentColor' }} />}
              {loading ? 'Verifying...' : 'Verify Access'}
            </button>
          </form>
        )}

        {step === 'password' && (
          <form onSubmit={handleLogin} className="login-form" data-testid="password-form">
            <div className="badge neon" style={{ alignSelf: 'center', marginBottom: 8 }}>Membership Verified</div>
            <div className="login-field">
              <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
              <input type="password" placeholder="Enter your password" value={password}
                onChange={e => setPassword(e.target.value)} autoFocus data-testid="password-input" />
            </div>
            <button className="btn-primary" type="submit" disabled={loading || !password} data-testid="login-btn">
              {loading ? <Loader2 className="animate-spin" /> : <Zap style={{ fill: 'currentColor' }} />}
              {loading ? 'Logging in...' : 'Log In'}
            </button>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <button type="button" className="btn-secondary" onClick={() => { setStep('email'); setPassword(''); setError(null); }}>
                Back
              </button>
              <button type="button" className="forgot-password-link" onClick={startForgotPassword}
                disabled={loading} data-testid="forgot-password-btn">
                Forgot Password?
              </button>
            </div>
          </form>
        )}

        {step === 'reset' && (
          <form onSubmit={handleResetPassword} className="login-form" data-testid="reset-form">
            <div className="badge neon" style={{ alignSelf: 'center', marginBottom: 8 }}>
              {accessType} Access Re-Verified
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', textAlign: 'center' }}>Set a new password for your account</p>
            <div className="login-field">
              <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
              <input type="password" placeholder="New password (min 6 chars)" value={password}
                onChange={e => setPassword(e.target.value)} autoFocus data-testid="reset-new-password-input" />
            </div>
            <div className="login-field">
              <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
              <input type="password" placeholder="Confirm new password" value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)} data-testid="reset-confirm-password-input" />
            </div>
            <button className="btn-primary" type="submit" disabled={loading || password.length < 6} data-testid="reset-password-btn">
              {loading ? <Loader2 className="animate-spin" /> : <Zap style={{ fill: 'currentColor' }} />}
              {loading ? 'Resetting...' : 'Reset Password & Enter'}
            </button>
            <button type="button" className="btn-secondary" onClick={() => { setStep('password'); setPassword(''); setConfirmPassword(''); setError(null); }}>
              Back to Login
            </button>
          </form>
        )}

        {step === 'setup' && (
          <form onSubmit={handleSetPassword} className="login-form" data-testid="setup-form">
            <div className="badge neon" style={{ alignSelf: 'center', marginBottom: 8 }}>
              {accessType} Access Confirmed
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', textAlign: 'center' }}>Set a password for future logins</p>
            <div className="login-field">
              <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
              <input type="password" placeholder="Create password (min 6 chars)" value={password}
                onChange={e => setPassword(e.target.value)} autoFocus data-testid="new-password-input" />
            </div>
            <div className="login-field">
              <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
              <input type="password" placeholder="Confirm password" value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)} data-testid="confirm-password-input" />
            </div>
            <button className="btn-primary" type="submit" disabled={loading || password.length < 6} data-testid="set-password-btn">
              {loading ? <Loader2 className="animate-spin" /> : <Zap style={{ fill: 'currentColor' }} />}
              {loading ? 'Setting up...' : 'Set Password & Enter'}
            </button>
          </form>
        )}

        {error && (
          <div className="error-box" style={{ marginTop: 16 }}>
            <ShieldAlert /><p>{error}</p>
          </div>
        )}
      </div>
    </div>
  );
}

function PickOfTheDayCard({ potd, onUse }) {
  if (!potd || !potd.available || !potd.pick) return null;
  const p = potd.pick;
  const isOver = p.recommendation === 'over';
  return (
    <div className="potd-card" data-testid="potd-card">
      <div className="potd-header">
        <div className="potd-badge">
          <Zap style={{ width: 12, height: 12, fill: 'currentColor' }} />
          <span>Pick of the Day</span>
        </div>
        <div className="potd-date">{potd.date}</div>
      </div>
      <div className="potd-player" data-testid="potd-player-name">{p.playerName}</div>
      <div className="potd-matchup">{p.teamName} vs {p.opponentName}</div>
      <div className="potd-league">{p.league}</div>
      <div className="potd-stats-row">
        <div className="potd-stat">
          <div className="potd-stat-label">Prop</div>
          <div className="potd-stat-value">{getPropLabel(p.propType)}</div>
        </div>
        <div className="potd-stat">
          <div className="potd-stat-label">Line</div>
          <div className="potd-stat-value">{p.suggestedLine}</div>
        </div>
        <div className="potd-stat">
          <div className="potd-stat-label">Confidence</div>
          <div className="potd-stat-value accent">{p.confidenceScore}%</div>
        </div>
      </div>
      <div className={`potd-rec ${p.recommendation}`}>
        {isOver ? <TrendingUp style={{ width: 16, height: 16 }} /> : <TrendingDown style={{ width: 16, height: 16 }} />}
        <span>{p.recommendation}</span>
        <span className={`badge ${isOver ? 'neon' : 'danger'}`}>{p.confidenceLevel}</span>
      </div>
      <p className="potd-summary">{p.sharpSummary}</p>
      {onUse && (
        <button className="potd-use-btn" onClick={onUse} data-testid="potd-use-btn">
          <Target style={{ width: 14, height: 14 }} /> Run Full Analysis
        </button>
      )}
    </div>
  );
}

export default function App() {
  const [auth, setAuth] = useState(null);
  const [authChecking, setAuthChecking] = useState(true);
  const [activeTab, setActiveTab] = useState('predict');
  const [trackingView, setTrackingView] = useState('live');

  const [wizardStep, setWizardStep] = useState(1);
  const [wizardData, setWizardData] = useState({});
  const [wizardError, setWizardError] = useState(null);
  const [searchMode, setSearchMode] = useState('wizard');

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
  const [potd, setPotd] = useState(null);
  const [potdLoading, setPotdLoading] = useState(true);

  const searchTimeout = useRef(null);
  const chatEndRef = useRef(null);

  // Auth check on mount
  useEffect(() => {
    const checkAuth = async () => {
      const email = localStorage.getItem('rp_email');
      const token = localStorage.getItem('rp_token');
      const access = localStorage.getItem('rp_access');
      if (email && token) {
        try {
          const res = await verifySession(email, token);
          if (res.valid) {
            setAuth({ email, token, accessType: res.access_type || access });
          } else {
            localStorage.removeItem('rp_email');
            localStorage.removeItem('rp_token');
            localStorage.removeItem('rp_access');
          }
        } catch {
          // Session check failed, clear auth
          localStorage.removeItem('rp_email');
          localStorage.removeItem('rp_token');
          localStorage.removeItem('rp_access');
        }
      }
      setAuthChecking(false);
    };
    checkAuth();
  }, []);

  useEffect(() => {
    const saved = localStorage.getItem('reverse_picks_v2');
    if (saved) setSavedPicks(JSON.parse(saved));
    checkApiStatus().then(ok => setApiStatus(ok ? 'online' : 'offline')).catch(() => setApiStatus('offline'));
    // Fetch Pick of the Day
    getPickOfTheDay()
      .then(data => setPotd(data))
      .catch(() => setPotd(null))
      .finally(() => setPotdLoading(false));
  }, []);

  // Poll to settle live picks every 5 minutes
  const livePickCount = savedPicks.filter(p => p.status === 'live').length;
  useEffect(() => {
    if (livePickCount === 0) return;

    const checkSettled = async () => {
      try {
        const livePicks = savedPicks.filter(p => p.status === 'live');
        const result = await settlePicks(livePicks);
        if (result.settled && result.settled.length > 0) {
          setSavedPicks(prev => {
            const updated = [...prev];
            for (const s of result.settled) {
              const idx = updated.findIndex(p => p.id === s.pickId);
              if (idx !== -1) {
                updated[idx] = {
                  ...updated[idx],
                  status: s.status,
                  result: s.result,
                  actualValue: s.actualValue,
                  matchScore: s.matchScore,
                  settledAt: Date.now(),
                };
              }
            }
            return updated;
          });
        }
      } catch {}
    };

    checkSettled();
    const interval = setInterval(checkSettled, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [livePickCount, savedPicks]);

  const picksInitialized = useRef(false);
  useEffect(() => {
    if (!picksInitialized.current) {
      picksInitialized.current = true;
      return; // Skip first render to avoid overwriting localStorage with empty array
    }
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
    if (searchMode === 'chat' && !chatSessionId) handleStartChat();
  }, [searchMode, chatSessionId, handleStartChat]);

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
      setChatMessages(prev => [...prev, { role: 'model', text: 'Error connecting to tactical search. Please try again.' }]);
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

  const savePick = () => {
    if (!projection) return;
    const newPick = {
      ...projection,
      id: Math.random().toString(36).substring(2, 9),
      timestamp: Date.now(),
      status: 'live',
      result: 'pending',
      excludedSampleIndices,
      _request: {
        leagueId: wizardData.leagueId,
        teamId: wizardData.teamId || projection.player?.teamId,
        opponentId: wizardData.opponentId,
      },
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

  const handleLogout = async () => {
    if (auth) {
      try { await authLogout(auth.email, auth.token); } catch {}
    }
    localStorage.removeItem('rp_email');
    localStorage.removeItem('rp_token');
    localStorage.removeItem('rp_access');
    setAuth(null);
  };

  const leaguesByType = (type) => SUPPORTED_LEAGUES.filter(l => l.type === type);

  // Auth loading state
  if (authChecking) {
    return (
      <div className="app" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
        <div className="loading-wrap">
          <div className="spinner-ring"><Zap className="inner-icon" style={{ width: 28, height: 28 }} /></div>
          <div className="loading-title">Loading...</div>
        </div>
      </div>
    );
  }

  // Show login if not authenticated
  if (!auth) {
    return <LoginPage onAuth={setAuth} />;
  }

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
          <button className="icon-btn" onClick={handleLogout} data-testid="logout-btn" title="Logout">
            <LogOut />
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
                {/* Pick of the Day */}
                {potdLoading ? (
                  <div className="potd-skeleton" data-testid="potd-loading">
                    <div className="potd-skeleton-line wide" />
                    <div className="potd-skeleton-line" />
                    <div className="potd-skeleton-line narrow" />
                  </div>
                ) : potd?.available ? (
                  <PickOfTheDayCard
                    potd={potd}
                    onUse={() => {
                      const p = potd.pick;
                      const league = SUPPORTED_LEAGUES.find(l => l.id === p.leagueId) || SUPPORTED_LEAGUES[0];
                      setWizardData({
                        leagueId: league.id,
                        playerName: p.playerName,
                        opponentName: p.opponentName,
                        propType: p.propType,
                        line: p.suggestedLine,
                        venue: 'home',
                      });
                    }}
                  />
                ) : null}

                <div className="flex justify-between items-center">
                  <div>
                    <h2 className="section-title" data-testid="wizard-title">AI Wizard</h2>
                    <p className="section-subtitle">
                      {searchMode === 'wizard' ? `Step ${wizardStep} of 6` : 'Tactical Search'}
                    </p>
                  </div>
                  {searchMode === 'wizard' && wizardStep > 1 && (
                    <button className="back-btn" onClick={() => setWizardStep(wizardStep - 1)} data-testid="wizard-back-btn">
                      <ArrowLeft /> Back
                    </button>
                  )}
                </div>

                {(searchMode === 'chat' || wizardStep === 1) && (
                  <div className="tab-switcher">
                    <button className={`tab-btn ${searchMode === 'wizard' ? 'active' : ''}`}
                      onClick={() => { setSearchMode('wizard'); setWizardStep(1); setWizardData({}); setWizardError(null); }}
                      data-testid="step-by-step-tab">Step-by-Step</button>
                    <button className={`tab-btn ${searchMode === 'chat' ? 'active' : ''}`}
                      onClick={() => { setSearchMode('chat'); }}
                      data-testid="tactical-uplink-tab">Tactical Search</button>
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep > 1 && (
                  <div className="wizard-breadcrumb" data-testid="wizard-breadcrumb">
                    <div className="breadcrumb-steps">
                      {['League', 'Player', 'Opponent', 'Venue', 'Prop', 'Line'].map((label, i) => {
                        const step = i + 1;
                        const isActive = wizardStep === step;
                        const isDone = wizardStep > step;
                        return (
                          <div key={label} className={`breadcrumb-step ${isActive ? 'active' : ''} ${isDone ? 'done' : ''}`}>
                            <div className="breadcrumb-dot">{isDone ? '\u2713' : step}</div>
                            <span className="breadcrumb-label">{label}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {searchMode === 'chat' && (
                  <div className="chat-container-inline" data-testid="tactical-uplink-inline">
                    <div className="chat-header">
                      <div>
                        <h3 className="chat-title" style={{ fontSize: 18 }}>Tactical Search</h3>
                        <p className="chat-subtitle">AI Strategic Analyst</p>
                      </div>
                      <button className="icon-btn" onClick={handleStartChat} data-testid="chat-reset-btn">
                        <RefreshCw />
                      </button>
                    </div>

                    <div className="chat-messages" data-testid="chat-messages" style={{ minHeight: 300, maxHeight: 450 }}>
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
                            <div style={{ flex: 1 }}>
                              <div className="player-name">{player.name}</div>
                              <div className="player-team">
                                {player.nationality && <span className="player-nationality">{player.nationality}</span>}
                                {player.nationality && player.teamName ? ' · ' : ''}
                                {player.teamName || 'Free Agent'}
                              </div>
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
                      <div className="space-y-2" style={{ maxHeight: 480, overflowY: 'auto' }}>
                        {teams.map(team => (
                          <div key={team.id} className="card card-clickable" onClick={() => handleOpponentSelect(team)}
                            data-testid={`team-${team.id}`}>
                            <div className="league-item">
                              <span className="name">{team.name}</span>
                              <ChevronRight />
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
                    <div className="stat-label" style={{ marginBottom: 8 }}>Select Prop Type</div>
                    {PROP_TYPES.map(prop => (
                      <div key={prop.key} className="card card-clickable" onClick={() => { setWizardData({ ...wizardData, propType: prop.key }); setWizardStep(6); }}
                        data-testid={`prop-${prop.key}`}>
                        <div className="prop-item">
                          <div>
                            <span className="name">{prop.label}</span>
                            <span style={{ display: 'block', fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>{prop.desc}</span>
                          </div>
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
                <div className="loading-sub">Running analysis simulation</div>
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
                          <div className={`status-dot ${pick.status} ${pick.result || ''}`} />
                          <span className="status-label">{pick.status === 'settled' ? (pick.result === 'hit' ? 'HIT' : pick.result === 'push' ? 'PUSH' : 'MISS') : pick.status}</span>
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
                          <div className="pick-matchup">{pick.player?.team} vs {pick.opponent}{pick.matchScore ? ` (${pick.matchScore})` : ''}</div>
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
                        {pick.actualValue != null && (
                          <div className="pick-stat">
                            <div className="pick-stat-label">Actual</div>
                            <div className={`pick-stat-value ${pick.result === 'hit' ? 'accent' : pick.result === 'push' ? 'warning' : 'danger'}`}>{pick.actualValue}</div>
                          </div>
                        )}
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
                  <div className="stat-value">{selectedPick.line} <span className="stat-suffix">{getPropLabel(selectedPick.propType)}</span></div>
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
        </div>
      </nav>
    </div>
  );
}
