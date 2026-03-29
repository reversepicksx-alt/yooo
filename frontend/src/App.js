import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Zap, ChevronRight, RefreshCw, ArrowLeft, Clock, Activity,
  Shield, Send, Loader2, Trash2, User, Search, Users, Edit3, HelpCircle, ChevronDown,
  TrendingUp, TrendingDown, BarChart3, ShieldAlert, Target, LogOut, Lock, Mail, Bell, RotateCcw,
  Camera, Upload, Check, X, ImageIcon
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';
import {
  getTeamsByLeague, searchPlayers, predict, predictCombo, startChat, sendChatMessage,
  checkApiStatus, SUPPORTED_LEAGUES,
  verifyWhop, authLogin, setPassword as apiSetPassword, resetPassword, verifySession, authLogout,
  getPickOfTheDay, savePick, listPicks, deletePick, correctPick, liveUpdatePicks,
  scanProp
} from './api';
import { toast, Toaster } from 'sonner';
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

        {projection.dataQuality && projection.dataQuality.level !== 'good' && (
          <div data-testid="data-quality-warning" style={{
            background: projection.dataQuality.level === 'low' ? 'rgba(244,63,94,0.08)' : 'rgba(245,158,11,0.08)',
            border: `1px solid ${projection.dataQuality.level === 'low' ? 'rgba(244,63,94,0.25)' : 'rgba(245,158,11,0.25)'}`,
            borderRadius: 10, padding: '10px 14px', marginBottom: 16, display: 'flex', alignItems: 'flex-start', gap: 10,
          }}>
            <ShieldAlert style={{ width: 16, height: 16, flexShrink: 0, marginTop: 1,
              color: projection.dataQuality.level === 'low' ? '#f43f5e' : '#f59e0b' }} />
            <div>
              <div style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 2,
                color: projection.dataQuality.level === 'low' ? '#f43f5e' : '#f59e0b' }}>
                {projection.dataQuality.level === 'low' ? 'Limited Data' : 'Data Gap Detected'}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                {projection.dataQuality.message}
              </div>
            </div>
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

        {/* MATCHUP OVERVIEW — Home vs Away, Possession, Moneyline, Game Type */}
        {projection.matchupOverview && (
          <div className="matchup-overview mt-6" data-testid="matchup-overview">
            <div className="stat-label flex items-center gap-2 mb-3">
              <Shield style={{ width: 12, height: 12, color: 'var(--accent)' }} /> Matchup Overview
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: 12, alignItems: 'center', marginBottom: 12 }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 14, fontWeight: 800, color: 'var(--text-primary)' }}>{projection.matchupOverview.homeTeam || 'Home'}</div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600, letterSpacing: '0.05em' }}>HOME</div>
                {projection.matchupOverview.moneyline?.home && (
                  <div style={{ fontSize: 16, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: projection.matchupOverview.favorite === 'home' ? 'var(--accent)' : 'var(--text-primary)', marginTop: 4 }}>
                    {projection.matchupOverview.moneyline.home}
                  </div>
                )}
              </div>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)' }}>VS</div>
                {projection.matchupOverview.moneyline?.draw && (
                  <div style={{ fontSize: 12, fontFamily: "'JetBrains Mono', monospace", color: 'var(--text-muted)', marginTop: 2 }}>
                    Draw: {projection.matchupOverview.moneyline.draw}
                  </div>
                )}
              </div>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 14, fontWeight: 800, color: 'var(--text-primary)' }}>{projection.matchupOverview.awayTeam || 'Away'}</div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600, letterSpacing: '0.05em' }}>AWAY</div>
                {projection.matchupOverview.moneyline?.away && (
                  <div style={{ fontSize: 16, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: projection.matchupOverview.favorite === 'away' ? 'var(--accent)' : 'var(--text-primary)', marginTop: 4 }}>
                    {projection.matchupOverview.moneyline.away}
                  </div>
                )}
              </div>
            </div>

            {/* Expected Possession Bar */}
            {projection.matchupOverview.expectedPossession && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 4 }}>Expected Possession</div>
                <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', background: 'rgba(255,255,255,0.06)' }}>
                  <div style={{ width: `${projection.matchupOverview.expectedPossession.home || 50}%`, background: 'var(--accent)', transition: 'width 0.6s' }} />
                  <div style={{ width: `${projection.matchupOverview.expectedPossession.away || 50}%`, background: '#f43f5e', transition: 'width 0.6s' }} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
                  <span style={{ fontSize: 12, fontWeight: 800, fontFamily: "'JetBrains Mono', monospace", color: 'var(--accent)' }}>{projection.matchupOverview.expectedPossession.home || 50}%</span>
                  <span style={{ fontSize: 12, fontWeight: 800, fontFamily: "'JetBrains Mono', monospace", color: '#f43f5e' }}>{projection.matchupOverview.expectedPossession.away || 50}%</span>
                </div>
              </div>
            )}

            {/* Game Type + Key Factor */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {projection.matchupOverview.expectedGameType && (
                <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 10px' }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Game Type</div>
                  <div style={{ fontSize: 13, fontWeight: 800, color: 'var(--text-primary)', marginTop: 2, textTransform: 'capitalize' }}>{projection.matchupOverview.expectedGameType}</div>
                </div>
              )}
              {projection.matchupOverview.keyMatchupFactor && (
                <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 10px' }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Key Factor</div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', marginTop: 2 }}>{projection.matchupOverview.keyMatchupFactor}</div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Tactical Alerts from Grok web search */}
        {projection.tacticalAlerts?.length > 0 && (
          <div className="mt-4 space-y-2" data-testid="tactical-alerts">
            {projection.tacticalAlerts.map((alert, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderRadius: 8,
                background: alert.severity === 'high' ? 'rgba(244,63,94,0.08)' : alert.severity === 'medium' ? 'rgba(245,158,11,0.08)' : 'rgba(255,255,255,0.03)',
                border: `1px solid ${alert.severity === 'high' ? 'rgba(244,63,94,0.2)' : alert.severity === 'medium' ? 'rgba(245,158,11,0.2)' : 'rgba(255,255,255,0.06)'}`,
              }}>
                <div style={{ fontSize: 9, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.1em',
                  color: alert.severity === 'high' ? '#f43f5e' : alert.severity === 'medium' ? '#f59e0b' : 'var(--text-muted)',
                  flexShrink: 0, width: 60
                }}>{alert.type}</div>
                <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>{alert.message}</div>
              </div>
            ))}
          </div>
        )}

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

          {/* GK SAVES FORMULA — only for saves prop */}
          {projection.gkFormula && (
            <div className="stat-box" style={{ borderColor: 'rgba(59,130,246,0.25)', background: 'rgba(59,130,246,0.04)' }} data-testid="gk-formula">
              <div className="stat-label flex items-center gap-2 mb-3">
                <Shield style={{ width: 12, height: 12, color: '#3b82f6' }} /> GK Saves Formula
              </div>

              {/* Formula equation */}
              <div style={{ background: 'rgba(0,0,0,0.3)', borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color: 'var(--accent)', fontWeight: 700, textAlign: 'center', letterSpacing: '0.02em' }}>
                {projection.gkFormula.opponentAvgSOT} SoT &times; {projection.gkFormula.gkSaveRate}% save rate &times; {projection.gkFormula.contextMultiplier} context = <span style={{ fontSize: 16, color: '#3b82f6' }}>{projection.gkFormula.formulaProjection}</span> saves
              </div>

              {/* Opponent shooting */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
                <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 10px' }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Opp Shots/Game ({projection.gkFormula.opponentVenue})</div>
                  <div style={{ fontSize: 18, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: 'var(--text-primary)', marginTop: 2 }}>{projection.gkFormula.opponentAvgShots}</div>
                  <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>{projection.gkFormula.opponentShotsSample} games</div>
                </div>
                <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 10px' }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: '#f43f5e', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Opp SOT/Game ({projection.gkFormula.opponentVenue})</div>
                  <div style={{ fontSize: 18, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: '#f43f5e', marginTop: 2 }}>{projection.gkFormula.opponentAvgSOT}</div>
                  <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>shots on target allowed</div>
                </div>
              </div>

              {/* GK stats */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 10 }}>
                <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 10px', textAlign: 'center' }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Save Rate</div>
                  <div style={{ fontSize: 18, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: 'var(--accent)', marginTop: 2 }}>{projection.gkFormula.gkSaveRate}%</div>
                </div>
                <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 10px', textAlign: 'center' }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Avg Saves</div>
                  <div style={{ fontSize: 18, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: 'var(--text-primary)', marginTop: 2 }}>{projection.gkFormula.gkAvgSaves}</div>
                </div>
                <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 10px', textAlign: 'center' }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>GA/Game</div>
                  <div style={{ fontSize: 18, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: 'var(--text-primary)', marginTop: 2 }}>{projection.gkFormula.goalsAgainstPerGame ?? '-'}</div>
                </div>
              </div>

              {/* Context multiplier */}
              <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 10px' }}>
                <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 4 }}>Match Context ({projection.gkFormula.contextMultiplier}x)</div>
                {projection.gkFormula.contextFactors?.map((f, i) => (
                  <div key={i} style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6 }}>{f}</div>
                ))}
                {(!projection.gkFormula.contextFactors || projection.gkFormula.contextFactors.length === 0) && (
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Neutral — no significant adjustments</div>
                )}
              </div>
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

        <div style={{ marginTop: 24, textAlign: 'center' }} data-testid="whop-signup-link">
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>Not yet a premium member?</p>
          <a href="https://whop.com/checkout/plan_XZqvPZlrENzGt" target="_blank" rel="noopener noreferrer"
            style={{ fontSize: 13, fontWeight: 600, color: 'var(--neon)', textDecoration: 'none', transition: 'opacity 0.2s' }}
            onMouseEnter={e => e.currentTarget.style.opacity = '0.7'}
            onMouseLeave={e => e.currentTarget.style.opacity = '1'}>
            Join on Whop &rarr;
          </a>
        </div>
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
  const [activeTab, setActiveTab] = useState('scan');
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
  const [liveData, setLiveData] = useState({});
  const [notifications, setNotifications] = useState([]);
  const [showNotifications, setShowNotifications] = useState(false);
  const [reanalyzingPick, setReanalyzingPick] = useState(null);
  const [correctingPick, setCorrectingPick] = useState(null); // pickId being corrected
  const [correctValue, setCorrectValue] = useState('');

  // Combo mode state
  const [comboMode, setComboMode] = useState(false);
  const [comboPlayer2, setComboPlayer2] = useState(null); // {playerId, playerName, teamId, teamName}
  const [comboLine, setComboLine] = useState(0);
  const [comboProjection, setComboProjection] = useState(null);
  const [isComboProjecting, setIsComboProjecting] = useState(false);
  const [comboProgress, setComboProgress] = useState('');

  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [isChatting, setIsChatting] = useState(false);
  const [chatSessionId, setChatSessionId] = useState(null);
  const [apiStatus, setApiStatus] = useState('checking');
  const [potd, setPotd] = useState(null);
  const [potdLoading, setPotdLoading] = useState(true);

  // Scan tab state
  const [scanImage, setScanImage] = useState(null); // base64 preview
  const [scanResults, setScanResults] = useState(null); // extracted picks array
  const [isScanning, setIsScanning] = useState(false);
  const [scanError, setScanError] = useState(null);
  const [scanPrediction, setScanPrediction] = useState(null); // full projection result
  const [isScanPredicting, setIsScanPredicting] = useState(false);
  const [scanPredictingIdx, setScanPredictingIdx] = useState(null);
  const [scanExcludedIndices, setScanExcludedIndices] = useState([]);
  const [scanVenueOverrides, setScanVenueOverrides] = useState({});
  const scanFileRef = useRef(null);

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

  // Load picks from MongoDB on auth
  useEffect(() => {
    if (!auth) return;
    listPicks(auth.email, auth.token)
      .then(data => setSavedPicks(data.picks || []))
      .catch(() => {});
    checkApiStatus().then(ok => setApiStatus(ok ? 'online' : 'offline')).catch(() => setApiStatus('offline'));
    getPickOfTheDay()
      .then(data => setPotd(data))
      .catch(() => setPotd(null))
      .finally(() => setPotdLoading(false));
  }, [auth]);

  // Poll live picks every 2 minutes for real-time stats
  const livePickCount = savedPicks.filter(p => p.status === 'live').length;
  useEffect(() => {
    if (!auth || livePickCount === 0) return;

    const fetchLiveUpdates = async () => {
      try {
        const result = await liveUpdatePicks(auth.email, auth.token);
        if (result.updates && result.updates.length > 0) {
          const newLiveData = {};
          const settledIds = [];
          for (const u of result.updates) {
            newLiveData[u.pickId] = u;
            if (u.matchStatus === 'final' && u.result) {
              settledIds.push(u.pickId);
              // Find the pick to get player name
              const pick = savedPicks.find(p => p.pickId === u.pickId);
              if (pick) {
                const propLabel = PROP_TYPES.find(pt => pt.key === pick.propType)?.label || pick.propType;
                const isHit = u.result === 'hit';
                const isPush = u.result === 'push';
                const notif = {
                  id: `${u.pickId}-${Date.now()}`,
                  pickId: u.pickId,
                  playerName: pick.playerName,
                  propType: propLabel,
                  line: pick.line,
                  recommendation: pick.recommendation,
                  result: u.result,
                  actualValue: u.actualValue,
                  matchScore: u.matchScore,
                  timestamp: Date.now(),
                  read: false,
                };
                setNotifications(prev => [notif, ...prev].slice(0, 50));
                // Toast notification
                if (isHit) {
                  toast.success(`${pick.playerName} — ${pick.recommendation.toUpperCase()} ${pick.line} ${propLabel} HIT (Actual: ${u.actualValue})`, { duration: 8000 });
                } else if (isPush) {
                  toast(`${pick.playerName} — ${pick.recommendation.toUpperCase()} ${pick.line} ${propLabel} PUSH (Actual: ${u.actualValue})`, { duration: 8000 });
                } else {
                  toast.error(`${pick.playerName} — ${pick.recommendation.toUpperCase()} ${pick.line} ${propLabel} MISS (Actual: ${u.actualValue})`, { duration: 8000 });
                }
              }
            }
          }
          setLiveData(prev => ({ ...prev, ...newLiveData }));
          if (settledIds.length > 0) {
            const refreshed = await listPicks(auth.email, auth.token);
            setSavedPicks(refreshed.picks || []);
          }
        }
      } catch {}
    };

    fetchLiveUpdates();
    const interval = setInterval(fetchLiveUpdates, 2 * 60 * 1000);
    return () => clearInterval(interval);
  }, [auth, livePickCount]);

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
        setWizardError(err.message === 'Failed to fetch' ? 'Network error — check your connection and try again.' : err.message || 'Search failed. Please try again.');
      } finally {
        setIsPlayersLoading(false);
      }
    }, 500);
  };

  const handlePlayerSelect = (player) => {
    setWizardData({ ...wizardData, playerId: player.id, playerName: player.name, teamId: player.teamId, teamName: player.teamName || '' });
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

  const handlePlayer2Select = (player) => {
    setComboPlayer2({ playerId: player.id, playerName: player.name, teamId: player.teamId, teamName: player.teamName || '' });
    setWizardStep(8);
  };

  const runComboProjection = async () => {
    if (!comboPlayer2) return;
    setIsComboProjecting(true);
    setWizardError(null);
    setComboProgress('Running combo analysis...');
    try {
      const result = await predictCombo({
        leagueId: wizardData.leagueId,
        player1Id: wizardData.playerId,
        player1Name: wizardData.playerName,
        player1TeamId: wizardData.teamId,
        player2Id: comboPlayer2.playerId,
        player2Name: comboPlayer2.playerName,
        player2TeamId: comboPlayer2.teamId,
        opponentId: wizardData.opponentId,
        opponentName: wizardData.opponentName,
        venue: wizardData.venue,
        propType: wizardData.propType,
        combinedLine: comboLine,
      });

      if (!result?.player1?.player || !result?.player2?.player) {
        throw new Error('One or both predictions failed.');
      }

      setComboProjection(result);
    } catch (err) {
      setWizardError(err.message || 'Combo projection failed.');
    } finally {
      setIsComboProjecting(false);
    }
  };

  const resetCombo = () => {
    setComboMode(false);
    setComboPlayer2(null);
    setComboLine(0);
    setComboProjection(null);
    setIsComboProjecting(false);
    setWizardStep(1);
    setWizardData({});
    setWizardError(null);
  };

  const submitCorrection = async (pickId) => {
    const val = parseFloat(correctValue);
    if (isNaN(val) || val < 0) { toast.error('Enter a valid number'); return; }
    try {
      const result = await correctPick(user.email, user.token, pickId, val);
      toast.success(`Corrected → ${result.result.toUpperCase()}`);
      setSavedPicks(prev => prev.map(p => p.pickId === pickId ? { ...p, actualValue: val, result: result.result, correctedManually: true } : p));
      setCorrectingPick(null);
      setCorrectValue('');
    } catch (err) {
      toast.error(err.message || 'Correction failed');
    }
  };

  const savePickFn = async () => {
    if (!projection || !auth) return;
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
        venue: wizardData.venue || 'home',
      },
    };
    try {
      await savePick(auth.email, auth.token, newPick);
      const refreshed = await listPicks(auth.email, auth.token);
      setSavedPicks(refreshed.picks || []);
    } catch {}
    setProjection(null);
    setExcludedSampleIndices([]);
    setWizardStep(1);
    setWizardData({});
    setActiveTab('tracking');
  };

  const removePickFn = async (pickId, e) => {
    e.stopPropagation();
    if (!auth) return;
    try {
      await deletePick(auth.email, auth.token, pickId);
      setSavedPicks(prev => prev.filter(p => p.pickId !== pickId));
    } catch {}
  };

  const reanalyzePick = async (pick, e) => {
    e.stopPropagation();
    if (!auth || reanalyzingPick) return;
    setReanalyzingPick(pick.pickId);
    try {
      const result = await predict({
        playerId: pick.playerId,
        playerName: pick.playerName,
        teamId: pick.teamId,
        opponentId: pick.opponentId,
        opponentName: pick.opponentName,
        leagueId: pick.leagueId,
        propType: pick.propType,
        line: pick.line,
        matchDate: new Date().toISOString().split('T')[0],
        venue: pick.venue || 'home',
      }, auth.token);
      // Save updated pick with new projection data
      const updatedPick = {
        ...result,
        id: pick.pickId,
        player: result.player || { id: pick.playerId, name: pick.playerName, team: pick.teamName },
        opponent: pick.opponentName,
        timestamp: Date.now(),
        status: 'live',
        result: 'pending',
        _request: {
          leagueId: pick.leagueId,
          teamId: pick.teamId,
          opponentId: pick.opponentId,
          venue: pick.venue || 'home',
        },
      };
      await savePick(auth.email, auth.token, updatedPick);
      const refreshed = await listPicks(auth.email, auth.token);
      setSavedPicks(refreshed.picks || []);
      toast.success(`Re-analyzed ${pick.playerName} — ${result.recommendation?.toUpperCase()} ${pick.line} (Proj: ${result.projectedValue}, Conf: ${result.confidenceScore}%)`);
    } catch {
      toast.error('Re-analysis failed. Try again.');
    } finally {
      setReanalyzingPick(null);
    }
  };

  // =============================================
  // SCAN TAB — Image Upload & Processing
  // =============================================
  const handleScanUpload = async (file) => {
    if (!file) return;
    setScanError(null);
    setScanResults(null);
    setScanPrediction(null);
    setScanPredictingIdx(null);
    setScanVenueOverrides({});

    // Convert to base64
    const reader = new FileReader();
    reader.onload = async (e) => {
      const base64Full = e.target.result;
      setScanImage(base64Full);

      // Extract just the base64 data (remove data:image/...;base64, prefix)
      const base64Data = base64Full.split(',')[1];

      setIsScanning(true);
      try {
        const result = await scanProp(base64Data);
        if (result.picks && result.picks.length > 0) {
          setScanResults(result.picks);
          toast.success(`Found ${result.picks.length} prop${result.picks.length > 1 ? 's' : ''} in image`);
        } else {
          setScanError('No player props detected in this image. Try a clearer screenshot.');
        }
      } catch (err) {
        setScanError(err.message || 'Failed to scan image');
        toast.error('Scan failed — try a different screenshot');
      } finally {
        setIsScanning(false);
      }
    };
    reader.readAsDataURL(file);
  };

  const handleScanPredict = async (pickData, idx) => {
    if (!pickData.resolved) {
      toast.error('Player not found — cannot run prediction');
      return;
    }
    setIsScanPredicting(true);
    setScanPredictingIdx(idx);
    setScanPrediction(null);
    setScanExcludedIndices([]);
    try {
      const opponentId = pickData.resolvedOpponent?.teamId || pickData.resolved.teamId;
      const opponentName = pickData.resolvedOpponent?.teamName || pickData.extracted.opponentName || 'Unknown';
      const venue = scanVenueOverrides[idx] || pickData.extracted.venue || 'home';
      const result = await predict({
        playerId: pickData.resolved.playerId,
        playerName: pickData.resolved.playerName,
        teamId: pickData.resolved.teamId,
        opponentId: opponentId,
        opponentName: opponentName,
        leagueId: pickData.extracted.leagueId || 39,
        venue: venue,
        propType: pickData.extracted.propType,
        line: pickData.extracted.line,
      });
      setScanPrediction(result);
      toast.success('Prediction complete!');
    } catch (err) {
      toast.error(err.message || 'Prediction failed');
    } finally {
      setIsScanPredicting(false);
      setScanPredictingIdx(null);
    }
  };

  const scanSavePickFn = async () => {
    if (!scanPrediction || !auth) return;
    const newPick = {
      ...scanPrediction,
      id: Math.random().toString(36).substring(2, 9),
      timestamp: Date.now(),
      status: 'live',
      result: 'pending',
      excludedSampleIndices: scanExcludedIndices,
      _request: scanPrediction._request || {},
    };
    try {
      await savePick(auth.email, auth.token, newPick);
      const refreshed = await listPicks(auth.email, auth.token);
      setSavedPicks(refreshed.picks || []);
      toast.success('Saved to Tracking!');
      setScanPrediction(null);
      setScanExcludedIndices([]);
      setActiveTab('tracking');
    } catch (err) {
      toast.error('Failed to save pick');
    }
  };

  const backToScanResults = () => {
    setScanPrediction(null);
    setScanExcludedIndices([]);
  };

  const resetScan = () => {
    setScanImage(null);
    setScanResults(null);
    setScanError(null);
    setScanPrediction(null);
    setScanPredictingIdx(null);
    setScanVenueOverrides({});
    if (scanFileRef.current) scanFileRef.current.value = '';
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
          <div className="version-badge">v2.1</div>
          <div style={{ position: 'relative' }}>
            <button className="icon-btn" onClick={() => setShowNotifications(!showNotifications)} data-testid="notification-bell">
              <Bell />
              {notifications.filter(n => !n.read).length > 0 && (
                <div className="notif-badge" data-testid="notif-count">{notifications.filter(n => !n.read).length}</div>
              )}
            </button>
            {showNotifications && (
              <div className="notif-dropdown" data-testid="notif-dropdown">
                <div className="notif-dropdown-header">
                  <span style={{ fontWeight: 800, fontSize: 13 }}>Notifications</span>
                  {notifications.length > 0 && (
                    <button style={{ fontSize: 11, color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 700 }}
                      onClick={() => { setNotifications(prev => prev.map(n => ({ ...n, read: true }))); }}>
                      Mark all read
                    </button>
                  )}
                </div>
                <div className="notif-list">
                  {notifications.length === 0 ? (
                    <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>No notifications yet</div>
                  ) : notifications.slice(0, 20).map(n => (
                    <div key={n.id} className={`notif-item ${n.read ? 'read' : 'unread'}`} onClick={() => {
                      setNotifications(prev => prev.map(x => x.id === n.id ? { ...x, read: true } : x));
                      setActiveTab('tracking');
                      setTrackingView(n.result ? 'history' : 'live');
                      setShowNotifications(false);
                    }}>
                      <div className={`notif-result ${n.result}`}>{n.result === 'hit' ? 'HIT' : n.result === 'push' ? 'PUSH' : 'MISS'}</div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 700, fontSize: 12 }}>{n.playerName}</div>
                        <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                          {n.recommendation?.toUpperCase()} {n.line} {n.propType} — Actual: {n.actualValue}
                        </div>
                      </div>
                      <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>{new Date(n.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
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
            {!projection && !isProjecting && !comboProjection && !isComboProjecting && (
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
                      {searchMode === 'wizard' ? `Step ${wizardStep} of ${comboMode ? 8 : 6}` : 'Tactical Search'}
                    </p>
                  </div>
                  {searchMode === 'wizard' && wizardStep > 1 && (
                    <button className="back-btn" onClick={() => {
                      if (wizardStep === 7 && comboMode) {
                        setComboMode(false);
                        setWizardStep(6);
                      } else {
                        setWizardStep(wizardStep - 1);
                      }
                    }} data-testid="wizard-back-btn">
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
                      {(comboMode
                        ? ['League', 'Player 1', 'Opponent', 'Venue', 'Prop', 'Line', 'Player 2', 'Combo']
                        : ['League', 'Player', 'Opponent', 'Venue', 'Prop', 'Line']
                      ).map((label, i) => {
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
                    <div style={{ position: 'relative', textAlign: 'center', margin: '8px 0' }}>
                      <div style={{ position: 'absolute', top: '50%', left: 0, right: 0, height: 1, background: 'rgba(255,255,255,0.08)' }} />
                      <span style={{ position: 'relative', background: 'var(--bg-primary)', padding: '0 12px', fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }}>or</span>
                    </div>
                    <button className="btn-secondary" data-testid="stack-player-btn" style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}
                      onClick={() => { setComboMode(true); setWizardPlayers([]); setWizardStep(7); }}>
                      <Users style={{ width: 16, height: 16 }} /> Stack 2nd Player (Combo)
                    </button>
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 7 && comboMode && (
                  <div className="space-y-4" data-testid="combo-player2-step">
                    <div className="stat-label" style={{ marginBottom: 4 }}>Select 2nd Player</div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
                      Stacking with <span style={{ color: 'var(--accent)', fontWeight: 700 }}>{wizardData.playerName}</span> &middot; {PROP_TYPES.find(p => p.key === wizardData.propType)?.label || wizardData.propType}
                    </div>
                    <div className="search-input-wrap">
                      <Search className="search-icon" />
                      <input className="search-input" type="text" placeholder="Search 2nd player..."
                        onChange={e => handlePlayerSearch(e.target.value)} data-testid="combo-player2-search" />
                      {isPlayersLoading && <RefreshCw className="animate-spin" style={{ position: 'absolute', right: 14, top: '50%', transform: 'translateY(-50%)', width: 16, height: 16, color: 'var(--accent)' }} />}
                    </div>
                    <div className="space-y-2" style={{ maxHeight: 400, overflowY: 'auto' }}>
                      {wizardPlayers.filter(p => p.id !== wizardData.playerId).map(player => (
                        <div key={player.id} className="card card-clickable" onClick={() => handlePlayer2Select(player)}
                          data-testid={`combo-player-${player.id}`}>
                          <div className="player-item">
                            <div className="player-avatar"><User /></div>
                            <div style={{ flex: 1 }}>
                              <div className="player-name">{player.name}</div>
                              <div className="player-team">
                                {player.teamName || 'Free Agent'}
                                {player.teamId === wizardData.teamId && <span style={{ color: 'var(--accent)', marginLeft: 6, fontSize: 10 }}>SAME TEAM</span>}
                                {player.teamId === wizardData.opponentId && <span style={{ color: '#f59e0b', marginLeft: 6, fontSize: 10 }}>OPPONENT</span>}
                              </div>
                            </div>
                          </div>
                        </div>
                      ))}
                      {!wizardPlayers.length && !isPlayersLoading && (
                        <div className="text-center" style={{ padding: '32px 0', color: 'var(--text-secondary)', fontSize: 13 }}>
                          Search for the 2nd player to stack.
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 8 && comboMode && comboPlayer2 && (
                  <div className="space-y-6" data-testid="combo-line-step">
                    <div className="combo-summary-card" style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.2)', borderRadius: 12, padding: 16 }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>Combo Stack</div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
                        <div className="player-avatar" style={{ width: 28, height: 28 }}><User /></div>
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{wizardData.playerName}</div>
                          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{wizardData.teamName} &middot; {wizardData.venue?.toUpperCase()}</div>
                        </div>
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', padding: '4px 0 4px 20px' }}>+</div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <div className="player-avatar" style={{ width: 28, height: 28 }}><User /></div>
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{comboPlayer2.playerName}</div>
                          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                            {comboPlayer2.teamName}
                            {comboPlayer2.teamId === wizardData.opponentId ? ` · ${wizardData.venue === 'home' ? 'AWAY' : 'HOME'}` : ` · ${wizardData.venue?.toUpperCase()}`}
                          </div>
                        </div>
                      </div>
                      <div style={{ marginTop: 10, fontSize: 11, color: 'var(--accent)', fontWeight: 700 }}>
                        {PROP_TYPES.find(p => p.key === wizardData.propType)?.label || wizardData.propType} — Combined
                      </div>
                    </div>
                    <div className="line-setter">
                      <div className="line-setter-label">Set Combined Line</div>
                      <div className="line-setter-row">
                        <button className="line-btn" onClick={() => setComboLine(Math.max(0, comboLine - 0.5))} data-testid="combo-line-decrease">-</button>
                        <input className="line-input" type="number" step="0.5" placeholder="0.0"
                          value={comboLine === 0 ? '' : comboLine}
                          onChange={e => setComboLine(parseFloat(e.target.value) || 0)}
                          data-testid="combo-line-input" />
                        <button className="line-btn" onClick={() => setComboLine(comboLine + 0.5)} data-testid="combo-line-increase">+</button>
                      </div>
                    </div>
                    {wizardError && <div className="error-box"><ShieldAlert /><p>{wizardError}</p></div>}
                    <button className="btn-primary" onClick={runComboProjection} data-testid="generate-combo-btn">
                      <Zap style={{ fill: 'currentColor' }} /> Generate Combo Projection
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

            {isComboProjecting && (
              <div className="loading-wrap">
                <div className="spinner-ring">
                  <Users className="inner-icon" style={{ width: 28, height: 28 }} />
                </div>
                <div className="loading-title">{comboProgress || 'Running Combo Analysis...'}</div>
                <div className="loading-sub">This takes about 2 minutes — analyzing each player separately</div>
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
                  onSave={savePickFn}
                  excludedIndices={excludedSampleIndices}
                  onToggleSample={idx => setExcludedSampleIndices(prev =>
                    prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx]
                  )}
                />
              </div>
            )}

            {comboProjection && !isComboProjecting && (
              <div className="space-y-6">
                <button className="back-btn" onClick={resetCombo} data-testid="combo-back-btn">
                  <ArrowLeft /> New Analysis
                </button>
                <div className="combo-result-card" data-testid="combo-result-card">
                  {/* COMBO HEADER */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
                    <Users style={{ width: 18, height: 18, color: 'var(--accent)' }} />
                    <div style={{ fontSize: 12, fontWeight: 800, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: 1.5 }}>Combo Projection</div>
                  </div>

                  {/* COMBINED RESULT */}
                  <div style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.15)', borderRadius: 12, padding: 20, marginBottom: 20, textAlign: 'center' }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>
                      Combined {PROP_TYPES.find(p => p.key === wizardData.propType)?.label || wizardData.propType}
                    </div>
                    <div style={{ fontSize: 40, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: 'var(--text-primary)', lineHeight: 1 }}>
                      {comboProjection.combined.projectedValue}
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--text-muted)', margin: '8px 0 12px' }}>
                      vs Line: <span style={{ fontWeight: 800, color: 'var(--text-primary)', fontFamily: "'JetBrains Mono', monospace" }}>{comboProjection.combined.line}</span>
                    </div>
                    <div style={{
                      display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 20px', borderRadius: 8,
                      background: comboProjection.combined.recommendation === 'over' ? 'rgba(16,185,129,0.15)' : 'rgba(244,63,94,0.15)',
                      border: `1px solid ${comboProjection.combined.recommendation === 'over' ? 'rgba(16,185,129,0.3)' : 'rgba(244,63,94,0.3)'}`,
                    }}>
                      {comboProjection.combined.recommendation === 'over'
                        ? <TrendingUp style={{ width: 16, height: 16, color: '#10b981' }} />
                        : <TrendingDown style={{ width: 16, height: 16, color: '#f43f5e' }} />}
                      <span style={{ fontSize: 16, fontWeight: 900, color: comboProjection.combined.recommendation === 'over' ? '#10b981' : '#f43f5e', textTransform: 'uppercase' }}>
                        {comboProjection.combined.recommendation}
                      </span>
                      <span className={`badge ${comboProjection.combined.confidenceLevel === 'High' ? 'neon' : comboProjection.combined.confidenceLevel === 'Medium' ? '' : 'caution'}`} style={{ fontSize: 10, marginLeft: 4 }}>
                        {comboProjection.combined.confidenceLevel}
                      </span>
                    </div>
                  </div>

                  {/* INDIVIDUAL BREAKDOWNS */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
                    {[comboProjection.player1, comboProjection.player2].map((pred, idx) => (
                      <div key={idx} style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 10, padding: 14 }}>
                        <div style={{ fontSize: 10, fontWeight: 700, color: idx === 0 ? 'var(--accent)' : '#f59e0b', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>
                          Player {idx + 1}
                        </div>
                        <div style={{ fontSize: 14, fontWeight: 800, color: 'var(--text-primary)', marginBottom: 4 }}>
                          {pred.player?.name || 'Unknown'}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>
                          {pred.player?.team || ''} &middot; {pred.player?.position || ''}
                        </div>
                        <div style={{ fontSize: 28, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: 'var(--text-primary)' }}>
                          {pred.projectedValue}
                        </div>
                        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>projected</div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 8 }}>
                          {pred.recommendation === 'over'
                            ? <TrendingUp style={{ width: 12, height: 12, color: '#10b981' }} />
                            : <TrendingDown style={{ width: 12, height: 12, color: '#f43f5e' }} />}
                          <span style={{ fontSize: 11, fontWeight: 700, color: pred.recommendation === 'over' ? '#10b981' : '#f43f5e', textTransform: 'uppercase' }}>
                            {pred.recommendation}
                          </span>
                          <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 4 }}>{pred.confidenceScore}%</span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* SHARED MATCHUP OVERVIEW */}
                  {comboProjection.player1?.matchupOverview && (
                    <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 10, padding: 14, marginBottom: 16 }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>Matchup Overview</div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{comboProjection.player1.matchupOverview.homeTeam}</div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>vs</div>
                        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{comboProjection.player1.matchupOverview.awayTeam}</div>
                      </div>
                      {comboProjection.player1.matchupOverview.expectedPossession && (
                        <div style={{ marginBottom: 8 }}>
                          <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden' }}>
                            <div style={{ width: `${comboProjection.player1.matchupOverview.expectedPossession.home || 50}%`, background: 'var(--accent)' }} />
                            <div style={{ width: `${comboProjection.player1.matchupOverview.expectedPossession.away || 50}%`, background: '#f43f5e' }} />
                          </div>
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
                            <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)' }}>{comboProjection.player1.matchupOverview.expectedPossession.home}%</span>
                            <span style={{ fontSize: 11, fontWeight: 700, color: '#f43f5e' }}>{comboProjection.player1.matchupOverview.expectedPossession.away}%</span>
                          </div>
                        </div>
                      )}
                      {comboProjection.player1.matchupOverview.expectedGameType && (
                        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Game Type: <span style={{ color: 'var(--text-primary)', fontWeight: 700, textTransform: 'capitalize' }}>{comboProjection.player1.matchupOverview.expectedGameType}</span></div>
                      )}
                    </div>
                  )}

                  {/* SHARP TAKES */}
                  <div style={{ display: 'grid', gap: 10 }}>
                    {[comboProjection.player1, comboProjection.player2].map((pred, idx) => pred.sharpSummary && (
                      <div key={idx} style={{ background: 'rgba(99,102,241,0.04)', border: '1px solid rgba(99,102,241,0.15)', borderRadius: 10, padding: 12 }}>
                        <div style={{ fontSize: 10, fontWeight: 700, color: '#6366f1', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>
                          {pred.player?.name} — Sharp Take
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>{pred.sharpSummary}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* TRACKING TAB */}
        {activeTab === 'tracking' && (
          <div className="animate-fade-in space-y-6" data-testid="tracking-tab">
            <div className="flex justify-between items-center">
              <h2 className="section-title">Tracking</h2>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {livePickCount > 0 && (
                  <div className="badge neon" style={{ fontSize: 10 }} data-testid="auto-refresh-badge">
                    <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#10b981', animation: 'pulse 2s infinite' }} />
                    Auto 2m
                  </div>
                )}
                <div className="tab-switcher" style={{ width: 'auto' }}>
                  <button className={`tab-btn ${trackingView === 'live' ? 'active' : ''}`}
                    onClick={() => setTrackingView('live')} data-testid="tracking-live-btn">Live</button>
                  <button className={`tab-btn ${trackingView === 'history' ? 'active' : ''}`}
                    onClick={() => setTrackingView('history')} data-testid="tracking-history-btn">History</button>
                </div>
              </div>
            </div>

            <div className="space-y-4">
              {savedPicks.filter(p => trackingView === 'live' ? p.status === 'live' : p.status === 'settled').length === 0 ? (
                <div className="empty-state" data-testid="tracking-empty">
                  <div className="empty-icon"><Clock /></div>
                  <p className="empty-text">No {trackingView} picks being tracked.</p>
                </div>
              ) : (
                savedPicks.filter(p => trackingView === 'live' ? p.status === 'live' : p.status === 'settled').map(pick => {
                  const live = liveData[pick.pickId];
                  const isMatchLive = live?.matchStatus === 'live';
                  const isMatchFinal = live?.matchStatus === 'final' || pick.status === 'settled';
                  const nowVal = isMatchLive ? (live?.currentValue ?? '-') : (pick.actualValue ?? '-');
                  const paceVal = isMatchLive ? (live?.pace ?? '-') : nowVal;
                  const hitPct = live?.hitPct ?? null;
                  const elapsed = live?.elapsed ?? 0;
                  const minutesPlayed = live?.minutesPlayed || 0;
                  const matchScore = live?.matchScore || pick.matchScore || '';
                  const propLabel = PROP_TYPES.find(pt => pt.key === pick.propType)?.label || pick.propType;
                  const isOver = pick.recommendation === 'over';
                  const lineNum = pick.line || 1;
                  const nowNum = typeof nowVal === 'number' ? nowVal : 0;
                  const paceNum = typeof paceVal === 'number' ? paceVal : 0;
                  const progressPct = Math.min(100, Math.max(0, (nowNum / (lineNum * 1.3)) * 100));
                  const lineMarkerPct = Math.min(95, (lineNum / (lineNum * 1.3)) * 100);
                  const onTrack = isOver ? paceNum > lineNum : paceNum < lineNum;
                  const resultLabel = pick.result === 'hit' ? 'HIT' : pick.result === 'push' ? 'PUSH' : pick.result === 'miss' ? 'MISS' : '';
                  const isHit = pick.result === 'hit';
                  const isMiss = pick.result === 'miss';

                  return (
                    <div key={pick.pickId} className="live-pick-card" data-testid={`pick-${pick.pickId}`}
                      style={{
                        background: '#0a0a0f',
                        borderRadius: 14,
                        padding: 0,
                        border: `1.5px solid ${isMatchLive ? 'var(--accent)' : isHit ? 'rgba(16,185,129,0.5)' : isMiss ? 'rgba(244,63,94,0.4)' : 'rgba(100,100,120,0.25)'}`,
                        overflow: 'hidden',
                      }}>

                      {/* HEADER */}
                      <div style={{ padding: '14px 16px 10px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                        <div style={{ flex: 1 }}>
                          <div style={{ fontSize: 17, fontWeight: 800, color: '#fff', letterSpacing: '-0.3px' }} data-testid="pick-player-name">
                            {pick.playerName}
                          </div>
                          <div style={{ fontSize: 11, fontWeight: 600, color: 'rgba(255,255,255,0.45)', textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: 3 }}>
                            {pick.teamName || 'Team'} &middot; {(pick.venue || 'home').toUpperCase()}
                          </div>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          {isMatchLive && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, fontWeight: 800, color: '#f43f5e' }}>
                              <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#f43f5e', animation: 'pulse 1.5s infinite' }} />
                              LIVE
                            </div>
                          )}
                          {isMatchFinal && !isMatchLive && (
                            <div style={{ fontSize: 11, fontWeight: 800, color: 'rgba(255,255,255,0.4)' }}>FINAL</div>
                          )}
                          {!isMatchLive && !isMatchFinal && (
                            <div style={{ fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.3)' }}>SCHEDULED</div>
                          )}
                          {pick.status === 'live' && (
                            <button className="reanalyze-btn" onClick={e => reanalyzePick(pick, e)}
                              disabled={reanalyzingPick === pick.pickId}
                              data-testid={`reanalyze-pick-${pick.pickId}`}
                              title="Re-analyze" style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4 }}>
                              {reanalyzingPick === pick.pickId
                                ? <Loader2 style={{ width: 14, height: 14, color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
                                : <RotateCcw style={{ width: 14, height: 14, color: 'rgba(255,255,255,0.4)' }} />}
                            </button>
                          )}
                          <button style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4 }}
                            onClick={e => removePickFn(pick.pickId, e)} data-testid={`remove-pick-${pick.pickId}`}>
                            <Trash2 style={{ width: 14, height: 14, color: 'rgba(255,255,255,0.3)' }} />
                          </button>
                        </div>
                      </div>

                      {/* PICK LINE */}
                      <div style={{ padding: '0 16px 12px', fontSize: 13, fontWeight: 800, letterSpacing: '0.06em' }}>
                        <span style={{ color: 'rgba(255,255,255,0.5)' }}>PICK: </span>
                        <span style={{ color: isOver ? 'var(--accent)' : '#f43f5e' }}>
                          {isOver ? 'OVER' : 'UNDER'} {pick.line}
                        </span>
                      </div>

                      {/* STATS ROW — NOW / LINE / PACE / HIT% */}
                      {(isMatchLive || isMatchFinal) && (
                        <div style={{ padding: '0 16px 12px' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', borderTop: '1px solid rgba(255,255,255,0.06)', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                            {[
                              { label: 'NOW', value: nowVal, color: onTrack ? 'var(--accent)' : '#f43f5e' },
                              { label: 'LINE', value: pick.line, color: 'rgba(255,255,255,0.7)' },
                              { label: 'PACE', value: paceVal, color: onTrack ? 'var(--accent)' : '#f43f5e' },
                              { label: 'HIT%', value: hitPct != null ? `${hitPct}%` : '-', color: hitPct > 50 ? 'var(--accent)' : '#f43f5e' },
                            ].map((stat, i) => (
                              <div key={stat.label} style={{
                                textAlign: 'center', padding: '10px 0',
                                borderRight: i < 3 ? '1px solid rgba(255,255,255,0.06)' : 'none',
                              }}>
                                <div style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.35)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>{stat.label}</div>
                                <div style={{ fontSize: 20, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: stat.color }}>{stat.value}</div>
                              </div>
                            ))}
                          </div>

                          {/* PROGRESS BAR */}
                          <div style={{ position: 'relative', height: 6, background: 'rgba(255,255,255,0.06)', borderRadius: 3, marginTop: 10, overflow: 'visible' }}>
                            <div style={{
                              height: '100%', borderRadius: 3, transition: 'width 0.5s ease',
                              width: `${progressPct}%`,
                              background: onTrack ? 'var(--accent)' : '#f43f5e',
                            }} />
                            <div style={{
                              position: 'absolute', top: -2, width: 2, height: 10, borderRadius: 1,
                              background: 'rgba(255,255,255,0.6)',
                              left: `${lineMarkerPct}%`,
                            }} />
                          </div>
                        </div>
                      )}

                      {/* SCHEDULED STATE — PROJ / LINE / CONF */}
                      {!isMatchLive && !isMatchFinal && (
                        <div style={{ padding: '0 16px 12px' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', borderTop: '1px solid rgba(255,255,255,0.06)', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                            {[
                              { label: 'PROJ', value: pick.projectedValue, color: 'var(--accent)' },
                              { label: 'LINE', value: pick.line, color: 'rgba(255,255,255,0.7)' },
                              { label: 'CONF', value: `${pick.confidenceScore}%`, color: 'rgba(255,255,255,0.7)' },
                            ].map((stat, i) => (
                              <div key={stat.label} style={{
                                textAlign: 'center', padding: '10px 0',
                                borderRight: i < 2 ? '1px solid rgba(255,255,255,0.06)' : 'none',
                              }}>
                                <div style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.35)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>{stat.label}</div>
                                <div style={{ fontSize: 20, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: stat.color }}>{stat.value}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* FOOTER — Score, Result, Prop Label */}
                      <div style={{ padding: '8px 16px 12px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, fontFamily: "'JetBrains Mono', monospace", color: 'rgba(255,255,255,0.35)' }}>
                          {isMatchLive && <span>{elapsed}&apos;</span>}
                          {matchScore && <span>{matchScore}</span>}
                          {minutesPlayed > 0 && minutesPlayed < 90 && isMatchFinal && (
                            <span style={{ color: '#f59e0b', fontSize: 10 }}>{minutesPlayed}&apos; played</span>
                          )}
                          {resultLabel && (
                            <span data-testid="pick-result" style={{
                              padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 900, letterSpacing: '0.08em',
                              background: isHit ? 'rgba(16,185,129,0.15)' : isMiss ? 'rgba(244,63,94,0.15)' : 'rgba(245,158,11,0.15)',
                              color: isHit ? '#10b981' : isMiss ? '#f43f5e' : '#f59e0b',
                              border: `1px solid ${isHit ? 'rgba(16,185,129,0.3)' : isMiss ? 'rgba(244,63,94,0.3)' : 'rgba(245,158,11,0.3)'}`,
                            }}>
                              {resultLabel}
                            </span>
                          )}
                          {pick.correctedManually && (
                            <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)', fontStyle: 'italic' }}>corrected</span>
                          )}
                          {pick.status === 'settled' && correctingPick !== pick.pickId && (
                            <button onClick={() => { setCorrectingPick(pick.pickId); setCorrectValue(String(pick.actualValue || '')); }}
                              data-testid={`correct-pick-${pick.pickId}`}
                              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 2, display: 'flex' }}>
                              <Edit3 style={{ width: 12, height: 12, color: 'rgba(255,255,255,0.3)' }} />
                            </button>
                          )}
                        </div>
                        <span style={{ fontSize: 12, fontWeight: 800, color: 'var(--accent)', fontFamily: "'JetBrains Mono', monospace", letterSpacing: '0.06em' }}>
                          {propLabel.toUpperCase()}
                        </span>
                      </div>

                      {/* CORRECTION INPUT */}
                      {correctingPick === pick.pickId && (
                        <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.5)', whiteSpace: 'nowrap' }}>Actual:</span>
                          <input type="number" step="1" value={correctValue}
                            onChange={e => setCorrectValue(e.target.value)}
                            data-testid="correct-value-input"
                            style={{ flex: 1, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 6, padding: '6px 10px', color: '#fff', fontSize: 14, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}
                            autoFocus />
                          <button onClick={() => submitCorrection(pick.pickId)}
                            data-testid="correct-submit-btn"
                            style={{ background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, padding: '6px 12px', fontSize: 11, fontWeight: 800, cursor: 'pointer' }}>
                            Save
                          </button>
                          <button onClick={() => { setCorrectingPick(null); setCorrectValue(''); }}
                            style={{ background: 'none', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 6, padding: '6px 10px', fontSize: 11, color: 'rgba(255,255,255,0.5)', cursor: 'pointer' }}>
                            Cancel
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

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

        {/* GUIDE TAB */}
        {activeTab === 'guide' && (
          <div className="tab-content" data-testid="guide-tab" style={{ padding: '16px 16px 100px' }}>
            <div style={{ textAlign: 'center', marginBottom: 24 }}>
              <div style={{ fontSize: 24, fontWeight: 900, color: '#fff', letterSpacing: '-0.5px' }}>How to Use ReversePicks</div>
              <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.45)', marginTop: 6 }}>Follow these steps to get your first prediction</div>
            </div>

            {[
              {
                step: 1, title: 'Pick a League', icon: <Target style={{ width: 20, height: 20 }} />,
                desc: 'Tap the PREDICT tab and select the league your match is in. We support 30+ leagues including NWSL, Premier League, La Liga, and more.',
                tip: 'Start with leagues you know well for the best edge.',
              },
              {
                step: 2, title: 'Search Your Player', icon: <Search style={{ width: 20, height: 20 }} />,
                desc: 'Type at least 3 characters of the player\'s name. Select the player you want to analyze from the results.',
                tip: 'Use last names for faster results. If a player doesn\'t show up, try a different spelling.',
              },
              {
                step: 3, title: 'Select the Opponent', icon: <Shield style={{ width: 20, height: 20 }} />,
                desc: 'Choose which team your player is facing. This determines the matchup analysis and defensive stats.',
                tip: null,
              },
              {
                step: 4, title: 'Home or Away?', icon: <ChevronRight style={{ width: 20, height: 20 }} />,
                desc: 'Select whether your player\'s team is HOME or AWAY. This matters — players perform differently at home vs away.',
                tip: 'Home teams generally have higher pass counts and possession.',
              },
              {
                step: 5, title: 'Choose Your Prop', icon: <BarChart3 style={{ width: 20, height: 20 }} />,
                desc: 'Pick the stat type: Pass Attempts, Shots, Tackles, Saves, Key Passes, etc. This is the stat the AI will predict.',
                tip: null,
              },
              {
                step: 6, title: 'Set the Line & Generate', icon: <Zap style={{ width: 20, height: 20 }} />,
                desc: 'Enter the prop line (e.g. 25.5). Hit "Generate Projection" and wait ~30 seconds. The AI analyzes real stats, live news, and tactical data.',
                tip: 'Want to stack 2 players? Hit "Stack 2nd Player" to get a combined projection.',
              },
              {
                step: 7, title: 'Read Your Prediction', icon: <TrendingUp style={{ width: 20, height: 20 }} />,
                desc: 'You\'ll see: Projected Value, Over/Under recommendation, Confidence Score, Recent Form, Sharp Take, and full reasoning. Scroll down for the complete analysis.',
                tip: 'Higher confidence = stronger edge. Look for 65%+ confidence picks.',
              },
              {
                step: 8, title: 'Save & Track', icon: <Activity style={{ width: 20, height: 20 }} />,
                desc: 'Tap "Save to Tracking" to monitor your pick live during the match. Go to the TRACKING tab to see NOW/LINE/PACE/HIT% in real-time.',
                tip: 'Settled picks can be corrected if the API data was wrong — tap the pencil icon.',
              },
            ].map((item) => (
              <div key={item.step} style={{
                background: '#0a0a0f', border: '1.5px solid rgba(100,100,120,0.2)', borderRadius: 14,
                padding: 0, marginBottom: 12, overflow: 'hidden',
              }}>
                <div style={{ padding: '14px 16px', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
                  <div style={{
                    width: 40, height: 40, borderRadius: 10, flexShrink: 0,
                    background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.2)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--accent)',
                  }}>
                    {item.icon}
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <span style={{ fontSize: 10, fontWeight: 900, color: 'var(--accent)', fontFamily: "'JetBrains Mono', monospace" }}>STEP {item.step}</span>
                      <span style={{ fontSize: 15, fontWeight: 800, color: '#fff' }}>{item.title}</span>
                    </div>
                    <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.55)', lineHeight: 1.5 }}>{item.desc}</div>
                    {item.tip && (
                      <div style={{ marginTop: 8, padding: '6px 10px', borderRadius: 6, background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.15)', fontSize: 11, color: '#818cf8', lineHeight: 1.4 }}>
                        {item.tip}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))}

            {/* FAQ Section */}
            <div style={{ marginTop: 24 }}>
              <div style={{ fontSize: 18, fontWeight: 900, color: '#fff', marginBottom: 14, letterSpacing: '-0.3px' }}>FAQ</div>
              {[
                { q: 'How long does a prediction take?', a: 'About 30-45 seconds. The AI searches live news, analyzes real stats, and runs tactical simulations.' },
                { q: 'Why does it say "Data Gap Detected"?', a: 'Some leagues (especially women\'s leagues) have incomplete stats from our data provider. The AI uses web-verified data to compensate.' },
                { q: 'Can I predict two players together?', a: 'Yes! On Step 6, tap "Stack 2nd Player" to combine two players\' projections for the same stat type.' },
                { q: 'How does the Tracking tab work?', a: 'Save a pick and it tracks live during the match — showing your player\'s current stat, pace, and hit probability in real-time.' },
                { q: 'A pick settled wrong. How do I fix it?', a: 'Go to History, find the pick, and tap the pencil icon. Enter the real number from SofaScore/FotMob and hit Save.' },
              ].map((faq, i) => (
                <details key={i} style={{
                  background: '#0a0a0f', border: '1.5px solid rgba(100,100,120,0.15)', borderRadius: 10,
                  marginBottom: 8, overflow: 'hidden',
                }}>
                  <summary style={{
                    padding: '12px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 700, color: '#fff',
                    listStyle: 'none', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  }}>
                    {faq.q}
                    <ChevronDown style={{ width: 14, height: 14, color: 'rgba(255,255,255,0.3)', flexShrink: 0 }} />
                  </summary>
                  <div style={{ padding: '0 16px 12px', fontSize: 12, color: 'rgba(255,255,255,0.5)', lineHeight: 1.5 }}>{faq.a}</div>
                </details>
              ))}
            </div>
          </div>
        )}

        {/* SCAN TAB */}
        {activeTab === 'scan' && (
          <div className="animate-fade-in" data-testid="scan-tab" style={{ padding: '0 0 100px' }}>

            {/* ── FULL ANALYSIS VIEW ── */}
            {scanPrediction && !isScanPredicting && (
              <div className="space-y-6">
                <button className="back-btn" onClick={backToScanResults} data-testid="scan-back-to-results">
                  <ArrowLeft /> Back to Scan
                </button>
                <ProjectionCard
                  projection={scanPrediction}
                  onSave={scanSavePickFn}
                  excludedIndices={scanExcludedIndices}
                  onToggleSample={idx => setScanExcludedIndices(prev =>
                    prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx]
                  )}
                />
              </div>
            )}

            {/* ── SCAN UPLOAD & RESULTS VIEW ── */}
            {!scanPrediction && (
              <>
                {/* Header */}
                <div style={{ textAlign: 'center', marginBottom: 20 }}>
                  <div style={{ fontSize: 22, fontWeight: 900, color: '#fff', letterSpacing: '-0.5px' }}>Scan a Prop</div>
                  <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.4)', marginTop: 4 }}>Upload a screenshot of any player prop for instant analysis</div>
                </div>

                {/* Upload Zone */}
                {!scanImage && (
                  <div
                    data-testid="scan-upload-zone"
                    onClick={() => scanFileRef.current?.click()}
                    onDragOver={(e) => { e.preventDefault(); e.currentTarget.style.borderColor = 'var(--accent)'; }}
                    onDragLeave={(e) => { e.currentTarget.style.borderColor = 'rgba(100,100,120,0.3)'; }}
                    onDrop={(e) => {
                      e.preventDefault();
                      e.currentTarget.style.borderColor = 'rgba(100,100,120,0.3)';
                      const file = e.dataTransfer.files[0];
                      if (file && file.type.startsWith('image/')) handleScanUpload(file);
                    }}
                    style={{
                      border: '2px dashed rgba(100,100,120,0.3)', borderRadius: 16, padding: '48px 24px',
                      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16,
                      cursor: 'pointer', transition: 'border-color 0.2s',
                      background: 'rgba(255,255,255,0.02)',
                    }}
                  >
                    <div style={{
                      width: 64, height: 64, borderRadius: 16, display: 'flex', alignItems: 'center', justifyContent: 'center',
                      background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.2)',
                    }}>
                      <Camera style={{ width: 28, height: 28, color: 'var(--accent)' }} />
                    </div>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: 15, fontWeight: 700, color: '#fff' }}>Tap to upload screenshot</div>
                      <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)', marginTop: 4 }}>or drag & drop an image here</div>
                    </div>
                    <div style={{
                      padding: '8px 20px', borderRadius: 8, fontSize: 12, fontWeight: 800,
                      background: 'var(--accent)', color: '#000', letterSpacing: '0.04em',
                    }}>
                      <Upload style={{ width: 14, height: 14, display: 'inline', verticalAlign: 'middle', marginRight: 6 }} />
                      CHOOSE IMAGE
                    </div>
                    <input
                      ref={scanFileRef}
                      type="file"
                      accept="image/*"
                      style={{ display: 'none' }}
                      data-testid="scan-file-input"
                      onChange={(e) => {
                        const file = e.target.files[0];
                        if (file) handleScanUpload(file);
                      }}
                    />
                  </div>
                )}

                {/* Scanning Spinner */}
                {isScanning && (
                  <div style={{ textAlign: 'center', padding: '40px 0' }} data-testid="scan-loading">
                    <div className="spinner-ring"><Camera className="inner-icon" style={{ width: 24, height: 24 }} /></div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: '#fff', marginTop: 16 }}>Analyzing screenshot...</div>
                    <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)', marginTop: 4 }}>AI is reading your player props</div>
                  </div>
                )}

                {/* Predicting Spinner */}
                {isScanPredicting && (
                  <div style={{ textAlign: 'center', padding: '40px 0' }} data-testid="scan-predicting">
                    <div className="spinner-ring"><Zap className="inner-icon" style={{ width: 24, height: 24 }} /></div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: '#fff', marginTop: 16 }}>Running Deep Analysis...</div>
                    <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)', marginTop: 4 }}>Grok tactical research + Gemini calibration</div>
                  </div>
                )}

                {/* Error State */}
                {scanError && (
                  <div className="error-box" style={{ marginTop: 16 }} data-testid="scan-error">
                    <ShieldAlert style={{ width: 16, height: 16 }} />
                    <span>{scanError}</span>
                  </div>
                )}

                {/* Image Preview + Results */}
                {scanImage && !isScanning && !isScanPredicting && (
                  <div style={{ marginTop: 8 }}>
                    {/* Preview */}
                    <div style={{ position: 'relative', marginBottom: 16 }}>
                      <img
                        src={scanImage}
                        alt="Uploaded prop"
                        data-testid="scan-preview-image"
                        style={{
                          width: '100%', maxHeight: 300, objectFit: 'contain',
                          borderRadius: 12, border: '1px solid rgba(100,100,120,0.2)',
                        }}
                      />
                      <button
                        onClick={resetScan}
                        data-testid="scan-reset-btn"
                        style={{
                          position: 'absolute', top: 8, right: 8, width: 32, height: 32,
                          borderRadius: 8, background: 'rgba(0,0,0,0.7)', border: '1px solid rgba(255,255,255,0.15)',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
                        }}
                      >
                        <X style={{ width: 16, height: 16, color: '#fff' }} />
                      </button>
                    </div>

                    {/* Extracted Props */}
                    {scanResults && scanResults.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                        <div style={{ fontSize: 11, fontWeight: 900, letterSpacing: '0.15em', color: 'var(--accent)', textTransform: 'uppercase' }}>
                          {scanResults.length} Prop{scanResults.length > 1 ? 's' : ''} Detected
                        </div>
                        {scanResults.map((pick, idx) => {
                          const ext = pick.extracted;
                          const res = pick.resolved;
                          const isPredicting = isScanPredicting && scanPredictingIdx === idx;
                          const propLabel = PROP_TYPES.find(p => p.key === ext.propType)?.label || ext.propType;

                          return (
                            <div key={idx} data-testid={`scan-result-${idx}`} style={{
                              background: '#0a0a0f', border: '1.5px solid rgba(100,100,120,0.2)', borderRadius: 14,
                              overflow: 'hidden',
                            }}>
                              {/* Player Info */}
                              <div style={{ padding: '14px 16px', display: 'flex', gap: 12, alignItems: 'center' }}>
                                <div style={{
                                    width: 44, height: 44, borderRadius: 10, background: 'rgba(16,185,129,0.1)',
                                    border: '1px solid rgba(16,185,129,0.2)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                                  }}>
                                    <User style={{ width: 20, height: 20, color: 'var(--accent)' }} />
                                  </div>
                                <div style={{ flex: 1 }}>
                                  <div style={{ fontSize: 15, fontWeight: 800, color: '#fff' }}>
                                    {res?.playerName || ext.playerName}
                                  </div>
                                  <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)', marginTop: 2, display: 'flex', alignItems: 'center', gap: 6 }}>
                                    <span>{res?.teamName || ext.playerTeam || 'Unknown team'}</span>
                                    {ext.opponentName && (
                                      <span>
                                        {ext.venue === 'away' ? ' @ ' : ' vs '}
                                        {ext.opponentName}
                                      </span>
                                    )}
                                    <span
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setScanVenueOverrides(prev => ({
                                          ...prev,
                                          [idx]: (prev[idx] || ext.venue || 'home') === 'home' ? 'away' : 'home'
                                        }));
                                      }}
                                      data-testid={`scan-venue-toggle-${idx}`}
                                      style={{
                                        padding: '2px 8px', borderRadius: 4, fontSize: 8, fontWeight: 900, letterSpacing: '0.1em',
                                        cursor: 'pointer', userSelect: 'none', transition: 'all 0.15s',
                                        background: (scanVenueOverrides[idx] || ext.venue || 'home') === 'away' ? 'rgba(244,63,94,0.15)' : 'rgba(59,130,246,0.15)',
                                        color: (scanVenueOverrides[idx] || ext.venue || 'home') === 'away' ? '#f43f5e' : '#3b82f6',
                                        border: `1px solid ${(scanVenueOverrides[idx] || ext.venue || 'home') === 'away' ? 'rgba(244,63,94,0.3)' : 'rgba(59,130,246,0.3)'}`,
                                      }}
                                    >
                                      {(scanVenueOverrides[idx] || ext.venue || 'home') === 'away' ? 'AWAY' : 'HOME'} &#x21C5;
                                    </span>
                                  </div>
                                </div>
                                {res ? (
                                  <div style={{
                                    padding: '4px 10px', borderRadius: 6, fontSize: 9, fontWeight: 900,
                                    background: 'rgba(16,185,129,0.12)', color: '#10b981', border: '1px solid rgba(16,185,129,0.25)',
                                    letterSpacing: '0.1em',
                                  }}>
                                    MATCHED
                                  </div>
                                ) : (
                                  <div style={{
                                    padding: '4px 10px', borderRadius: 6, fontSize: 9, fontWeight: 900,
                                    background: 'rgba(245,158,11,0.12)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.25)',
                                    letterSpacing: '0.1em',
                                  }}>
                                    NO MATCH
                                  </div>
                                )}
                              </div>

                              {/* Prop Details */}
                              <div style={{
                                padding: '0 16px 14px', display: 'flex', gap: 8, alignItems: 'center',
                              }}>
                                <div style={{
                                  flex: 1, padding: '10px 12px', borderRadius: 8, background: 'rgba(255,255,255,0.03)',
                                  border: '1px solid rgba(100,100,120,0.15)', textAlign: 'center',
                                }}>
                                  <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.1em', marginBottom: 4 }}>PROP</div>
                                  <div style={{ fontSize: 13, fontWeight: 800, color: '#fff' }}>{propLabel}</div>
                                </div>
                                <div style={{
                                  flex: 1, padding: '10px 12px', borderRadius: 8, background: 'rgba(255,255,255,0.03)',
                                  border: '1px solid rgba(100,100,120,0.15)', textAlign: 'center',
                                }}>
                                  <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.1em', marginBottom: 4 }}>LINE</div>
                                  <div style={{ fontSize: 13, fontWeight: 800, color: 'var(--accent)', fontFamily: "'JetBrains Mono', monospace" }}>{ext.line}</div>
                                </div>
                                <div style={{
                                  flex: 1, padding: '10px 12px', borderRadius: 8, background: 'rgba(255,255,255,0.03)',
                                  border: '1px solid rgba(100,100,120,0.15)', textAlign: 'center',
                                }}>
                                  <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.1em', marginBottom: 4 }}>LEAGUE</div>
                                  <div style={{ fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.6)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {ext.league || 'Unknown'}
                                  </div>
                                </div>
                              </div>

                              {/* Action Button */}
                              <div style={{ padding: '0 16px 14px' }}>
                                <button
                                  onClick={() => handleScanPredict(pick, idx)}
                                  disabled={!res || isPredicting}
                                  data-testid={`scan-predict-btn-${idx}`}
                                  style={{
                                    width: '100%', padding: '12px', borderRadius: 10, border: 'none',
                                    background: res ? 'var(--accent)' : 'rgba(255,255,255,0.06)',
                                    color: res ? '#000' : 'rgba(255,255,255,0.3)',
                                    fontSize: 13, fontWeight: 900, letterSpacing: '0.06em',
                                    cursor: res ? 'pointer' : 'not-allowed',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                                  }}
                                >
                                  {isPredicting ? (
                                    <><Loader2 style={{ width: 16, height: 16, animation: 'spin 1s linear infinite' }} /> ANALYZING...</>
                                  ) : (
                                    <><Zap style={{ width: 16, height: 16 }} /> RUN PREDICTION</>
                                  )}
                                </button>
                              </div>
                            </div>
                          );
                        })}

                        {/* Scan Again */}
                        <button
                          onClick={resetScan}
                          data-testid="scan-again-btn"
                          style={{
                            width: '100%', padding: '14px', borderRadius: 12, border: '1px solid rgba(100,100,120,0.2)',
                            background: 'rgba(255,255,255,0.03)', color: 'rgba(255,255,255,0.5)',
                            fontSize: 13, fontWeight: 700, cursor: 'pointer', marginTop: 8,
                            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                          }}
                        >
                          <Camera style={{ width: 16, height: 16 }} /> Scan Another Image
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}

      </main>

      {/* Bottom Nav */}
      <nav className="bottom-nav" data-testid="bottom-nav">
        <div className="nav-items">
          <button className={`nav-item ${activeTab === 'scan' ? 'active' : ''}`}
            onClick={() => setActiveTab('scan')} data-testid="nav-scan">
            <Camera />
            <span>Scan Prop</span>
          </button>
          <button className={`nav-item ${activeTab === 'tracking' ? 'active' : ''}`}
            onClick={() => setActiveTab('tracking')} data-testid="nav-tracking">
            <Activity />
            <span>Tracking</span>
          </button>
        </div>
      </nav>
      <Toaster position="top-center" theme="dark" richColors />
    </div>
  );
}
