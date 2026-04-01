import React from 'react';
import {
  Zap, Clock, Activity, Shield, ShieldAlert, Target,
  TrendingUp, TrendingDown, BarChart3, Users
} from 'lucide-react';
import { getPropLabel } from '../../constants';
import { ProbabilityChart } from './ProbabilityChart';
import { MatchStatZones } from './MatchStatZones';
import { H2HSection } from './H2HSection';

export function ProjectionCard({ projection, onSave, excludedIndices, onToggleSample }) {
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
            <div className="projection-matchup">
              {projection.player?.position && projection.player.position !== 'Unknown' && (
                <span style={{ display: 'inline-block', background: 'rgba(59,130,246,0.15)', color: '#60a5fa', fontSize: 10, fontWeight: 800, padding: '2px 8px', borderRadius: 4, marginRight: 6, letterSpacing: '0.05em', verticalAlign: 'middle' }} data-testid="player-position-badge">
                  {projection.player.position}
                  {projection.player.role && <span style={{ fontWeight: 600, opacity: 0.8 }}> · {projection.player.role}</span>}
                </span>
              )}
              {projection.player?.team} vs {projection.opponent}
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="stat-label">Confidence</div>
            <div className="confidence-value" data-testid="projection-confidence">{projection.confidenceScore}%</div>
          </div>
        </div>

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

        <MatchStatZones teamStats={projection.teamMatchStats} opponentStats={projection.opponentMatchStats} venue={projection._request?.venue || 'home'} />

        <H2HSection h2hData={projection.h2hPlayerStats} propType={projection.propType} />

        {projection.positionComparison && projection.positionComparison.players?.length > 0 && (
          <div className="stat-box mt-6" style={{ borderColor: 'rgba(59,130,246,0.25)', background: 'rgba(59,130,246,0.04)' }} data-testid="position-comparison">
            <div className="stat-label flex items-center gap-2 mb-3">
              <Users style={{ width: 12, height: 12, color: '#60a5fa' }} />
              {projection.positionComparison.positionShort || projection.positionComparison.position}s vs {projection.positionComparison.opponent}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10, lineHeight: 1.4 }}>
              How other {projection.positionComparison.positionShort || projection.positionComparison.position}s performed against this opponent recently
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
              <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 10px', textAlign: 'center' }}>
                <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                  Avg {getPropLabel(projection.positionComparison.propType)}
                </div>
                <div style={{ fontSize: 18, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: '#60a5fa', marginTop: 2 }}>
                  {projection.positionComparison.avgStatValue}
                </div>
                <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>
                  {projection.positionComparison.sampleSize} players
                </div>
              </div>
              <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 10px', textAlign: 'center' }}>
                <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                  {projection.sport === 'basketball' ? 'Per-36 Avg' : 'Per-90 Avg'}
                </div>
                <div style={{ fontSize: 18, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: 'var(--accent)', marginTop: 2 }}>
                  {projection.positionComparison.avgPer90 || projection.positionComparison.avgPer36 || '-'}
                </div>
                <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>normalized rate</div>
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {projection.positionComparison.players.map((p, i) => (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '6px 10px', borderRadius: 6,
                  background: i % 2 === 0 ? 'rgba(255,255,255,0.02)' : 'transparent',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', width: 14, textAlign: 'center' }}>{i + 1}</span>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 140 }}>
                        {p.name}
                      </div>
                      <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>{p.team} · {p.date}</div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
                    <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{p.minutes}'</span>
                    <span style={{
                      fontSize: 14, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace",
                      color: p.statValue > projection.line ? 'var(--accent)' : '#f43f5e',
                    }}>
                      {p.statValue}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className={`rec-banner ${rec} mt-6`} data-testid="recommendation-banner">
          <div className={`rec-label ${rec}`}>
            {rec === 'over' ? <TrendingUp /> : <TrendingDown />}
            <span>Recommend: {rec}</span>
          </div>
          <span className={`badge ${rec === 'over' ? 'neon' : 'danger'}`}>{projection.confidenceLevel}</span>
        </div>

        {/* Model Agreement — always visible */}
        <div className="stat-box mt-4" data-testid="model-agreement" style={{ borderColor: 'rgba(168,85,247,0.2)', background: 'rgba(168,85,247,0.04)' }}>
          <div className="stat-label flex items-center gap-2 mb-3">
            <BarChart3 style={{ width: 12, height: 12, color: '#a855f7' }} />
            Model Agreement
          </div>
          {projection.consensusNote && (
            <div style={{ fontSize: 12, color: 'var(--text-primary)', marginBottom: 10, lineHeight: 1.5, fontWeight: 600 }} data-testid="consensus-note">
              {projection.consensusNote}
            </div>
          )}
          {projection.modelBreakdown && projection.modelBreakdown.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {projection.modelBreakdown.map((m, i) => {
                const isOver = m.recommendation === 'over';
                return (
                  <div key={i} style={{
                    flex: 1, minWidth: 80, padding: '8px 10px', borderRadius: 8,
                    background: 'rgba(255,255,255,0.03)',
                    border: `1px solid ${isOver ? 'rgba(0,255,136,0.15)' : 'rgba(239,68,68,0.15)'}`,
                    textAlign: 'center',
                  }}>
                    <div style={{ fontSize: 9, fontWeight: 700, color: '#a855f7', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
                      {m.model === 'grok' ? 'AI-1' : m.model === 'gemini' ? 'AI-2' : m.model === 'gpt' ? 'AI-3' : `AI-${i+1}`}
                    </div>
                    <div style={{
                      fontSize: 13, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace",
                      color: isOver ? 'var(--accent)' : '#f43f5e',
                    }}>
                      {m.projectedValue}
                    </div>
                    <div style={{
                      fontSize: 9, fontWeight: 700, textTransform: 'uppercase', marginTop: 2,
                      color: isOver ? 'var(--accent)' : '#f43f5e',
                    }}>
                      {m.recommendation}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

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

        {projection.gkFormula && (
          <div className="stat-box mt-6" style={{ borderColor: 'rgba(59,130,246,0.25)', background: 'rgba(59,130,246,0.04)' }} data-testid="gk-formula">
            <div className="stat-label flex items-center gap-2 mb-3">
              <Shield style={{ width: 12, height: 12, color: '#3b82f6' }} /> GK Saves Formula
            </div>
            <div style={{ background: 'rgba(0,0,0,0.3)', borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color: 'var(--accent)', fontWeight: 700, textAlign: 'center', letterSpacing: '0.02em' }}>
              {projection.gkFormula.opponentAvgSOT} SoT &times; {projection.gkFormula.gkSaveRate}% save rate &times; {projection.gkFormula.contextMultiplier} context = <span style={{ fontSize: 16, color: '#3b82f6' }}>{projection.gkFormula.formulaProjection}</span> saves
            </div>
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
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
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
          </div>
        )}
      </div>

      <button className="btn-primary" onClick={onSave} data-testid="save-to-tracking-btn">
        Save to Tracking
      </button>
    </div>
  );
}
