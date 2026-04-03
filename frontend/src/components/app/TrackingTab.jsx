import React from 'react';
import {
  Brain, Loader2, Clock, BarChart3, Edit3, Trash2, RotateCcw,
  ArrowLeft, TrendingUp, TrendingDown, RefreshCw
} from 'lucide-react';
import { PROP_TYPES, BASKETBALL_PROP_TYPES, getPropLabel } from '../../constants';

export function TrackingTab({
  auth, savedPicks, liveData, livePickCount,
  trackingView, setTrackingView,
  missAnalyses, reanalyzePick, reanalyzingPick,
  removePickFn,
  correctingPick, setCorrectingPick, correctValue, setCorrectValue, submitCorrection,
  selectedPick, setSelectedPick,
  calibrationInsights, setCalibrationInsights,
  calibrationLoading, setCalibrationLoading,
  getCalibrationInsights,
}) {
  return (
    <>
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
              <button className={`tab-btn ${trackingView === 'won' ? 'active' : ''}`}
                onClick={() => setTrackingView('won')} data-testid="tracking-won-btn"
                style={trackingView === 'won' ? { background: 'rgba(16,185,129,0.15)', borderColor: 'rgba(16,185,129,0.3)', color: '#10b981' } : {}}>Won</button>
              <button className={`tab-btn ${trackingView === 'lost' ? 'active' : ''}`}
                onClick={() => setTrackingView('lost')} data-testid="tracking-lost-btn"
                style={trackingView === 'lost' ? { background: 'rgba(244,63,94,0.15)', borderColor: 'rgba(244,63,94,0.3)', color: '#f43f5e' } : {}}>Lost</button>
              <button className={`tab-btn ${trackingView === 'pushed' ? 'active' : ''}`}
                onClick={() => setTrackingView('pushed')} data-testid="tracking-pushed-btn"
                style={trackingView === 'pushed' ? { background: 'rgba(245,158,11,0.15)', borderColor: 'rgba(245,158,11,0.3)', color: '#f59e0b' } : {}}>Pushed</button>
              <button className={`tab-btn ${trackingView === 'insights' ? 'active' : ''}`}
                onClick={() => {
                  setTrackingView('insights');
                  if (!calibrationInsights && auth) {
                    setCalibrationLoading(true);
                    getCalibrationInsights(auth.email, auth.token)
                      .then(data => setCalibrationInsights(data))
                      .catch(err => console.error('[CALIBRATION] Load error:', err))
                      .finally(() => setCalibrationLoading(false));
                  }
                }} data-testid="tracking-insights-btn"
                style={trackingView === 'insights' ? { background: 'rgba(139,92,246,0.15)', borderColor: 'rgba(139,92,246,0.3)', color: '#8b5cf6' } : {}}>Insights</button>
            </div>
          </div>
        </div>

        {/* USER RECORD TRACKER */}
        <RecordTracker savedPicks={savedPicks} />

        {trackingView === 'insights' ? (
          <CalibrationPanel
            auth={auth}
            calibrationInsights={calibrationInsights}
            calibrationLoading={calibrationLoading}
            setCalibrationLoading={setCalibrationLoading}
            setCalibrationInsights={setCalibrationInsights}
            getCalibrationInsights={getCalibrationInsights}
          />
        ) : (
          <PicksList
            savedPicks={savedPicks}
            trackingView={trackingView}
            liveData={liveData}
            missAnalyses={missAnalyses}
            reanalyzePick={reanalyzePick}
            reanalyzingPick={reanalyzingPick}
            removePickFn={removePickFn}
            correctingPick={correctingPick}
            setCorrectingPick={setCorrectingPick}
            correctValue={correctValue}
            setCorrectValue={setCorrectValue}
            submitCorrection={submitCorrection}
          />
        )}
      </div>

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
    </>
  );
}

function RecordTracker({ savedPicks }) {
  const settled = savedPicks.filter(p => p.status === 'settled' && p.result);
  const hits = settled.filter(p => p.result === 'hit').length;
  const misses = settled.filter(p => p.result === 'miss').length;
  const pushes = settled.filter(p => p.result === 'push').length;
  const total = hits + misses;
  const winRate = total > 0 ? Math.round((hits / total) * 100) : 0;
  const sorted = [...settled].sort((a, b) => (b.settledAt || b.timestamp || 0) - (a.settledAt || a.timestamp || 0));
  let streak = 0, streakType = '';
  for (const p of sorted) {
    if (p.result === 'push') continue;
    if (!streakType) { streakType = p.result; streak = 1; }
    else if (p.result === streakType) streak++;
    else break;
  }
  const streakLabel = streak > 0 ? `${streak}${streakType === 'hit' ? 'W' : 'L'}` : '-';
  if (settled.length === 0) return null;

  return (
    <div data-testid="record-tracker" style={{
      background: '#0a0a0f', border: '1px solid rgba(100,100,120,0.15)',
      borderRadius: 6, padding: '6px 8px', marginBottom: 2,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 4 }}>
        <BarChart3 style={{ width: 9, height: 9, color: 'var(--accent)' }} />
        <span style={{ fontSize: 7, fontWeight: 900, letterSpacing: '0.1em', color: 'var(--accent)', textTransform: 'uppercase' }}>Your Record</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr', gap: 2 }}>
        {[
          { label: 'HITS', value: hits, color: '#10b981' },
          { label: 'MISS', value: misses, color: '#f43f5e' },
          { label: 'PUSH', value: pushes, color: '#f59e0b' },
          { label: 'WIN%', value: `${winRate}%`, color: winRate >= 55 ? '#10b981' : winRate >= 45 ? '#f59e0b' : '#f43f5e' },
          { label: 'STRK', value: streakLabel, color: streakType === 'hit' ? '#10b981' : streakType === 'miss' ? '#f43f5e' : 'var(--text-muted)' },
        ].map(s => (
          <div key={s.label} style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 11, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: s.color, lineHeight: 1 }}>{s.value}</div>
            <div style={{ fontSize: 6, fontWeight: 800, color: 'rgba(255,255,255,0.3)', letterSpacing: '0.1em', marginTop: 2 }}>{s.label}</div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 4, height: 3, borderRadius: 2, overflow: 'hidden', background: 'rgba(255,255,255,0.05)', display: 'flex' }}>
        {hits > 0 && <div style={{ width: `${(hits / (total + pushes)) * 100}%`, background: '#10b981', transition: 'width 0.4s' }} />}
        {pushes > 0 && <div style={{ width: `${(pushes / (total + pushes)) * 100}%`, background: '#f59e0b', transition: 'width 0.4s' }} />}
        {misses > 0 && <div style={{ width: `${(misses / (total + pushes)) * 100}%`, background: '#f43f5e', transition: 'width 0.4s' }} />}
      </div>
    </div>
  );
}

function CalibrationPanel({ auth, calibrationInsights, calibrationLoading, setCalibrationLoading, setCalibrationInsights, getCalibrationInsights }) {
  if (calibrationLoading) {
    return (
      <div data-testid="calibration-panel" style={{ marginTop: 4, textAlign: 'center', padding: '40px 0' }}>
        <Loader2 style={{ width: 24, height: 24, color: '#8b5cf6', animation: 'spin 1s linear infinite', margin: '0 auto 12px' }} />
        <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.4)' }}>Loading calibration data...</div>
      </div>
    );
  }

  if (!calibrationInsights || calibrationInsights.insights?.length === 0) {
    return (
      <div data-testid="calibration-panel" style={{ marginTop: 4 }}>
        <div className="empty-state" data-testid="calibration-empty">
          <div className="empty-icon"><Brain /></div>
          <p className="empty-text">No calibration data yet. The system learns from missed picks automatically.</p>
        </div>
      </div>
    );
  }

  return (
    <div data-testid="calibration-panel" style={{ marginTop: 4 }}>
      <div className="space-y-4">
        {/* Summary Header */}
        <div style={{ background: '#0a0a0f', border: '1px solid rgba(139,92,246,0.2)', borderRadius: 10, padding: '12px 14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
            <Brain style={{ width: 14, height: 14, color: '#8b5cf6' }} />
            <span style={{ fontSize: 11, fontWeight: 900, letterSpacing: '0.1em', color: '#8b5cf6', textTransform: 'uppercase' }}>System Learning Summary</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
            <div style={{ textAlign: 'center', padding: '8px 0', background: 'rgba(139,92,246,0.06)', borderRadius: 8 }}>
              <div style={{ fontSize: 18, fontWeight: 900, color: '#8b5cf6', fontFamily: "'JetBrains Mono', monospace" }}>{calibrationInsights.totalAnalyzed}</div>
              <div style={{ fontSize: 8, fontWeight: 700, color: 'rgba(255,255,255,0.3)', letterSpacing: '0.1em', marginTop: 2 }}>ANALYZED</div>
            </div>
            <div style={{ textAlign: 'center', padding: '8px 0', background: 'rgba(244,63,94,0.06)', borderRadius: 8 }}>
              <div style={{ fontSize: 18, fontWeight: 900, color: '#f43f5e', fontFamily: "'JetBrains Mono', monospace" }}>{calibrationInsights.totalMisses}</div>
              <div style={{ fontSize: 8, fontWeight: 700, color: 'rgba(255,255,255,0.3)', letterSpacing: '0.1em', marginTop: 2 }}>TOTAL MISSES</div>
            </div>
            <div style={{ textAlign: 'center', padding: '8px 0', background: 'rgba(16,185,129,0.06)', borderRadius: 8 }}>
              <div style={{ fontSize: 18, fontWeight: 900, color: '#10b981', fontFamily: "'JetBrains Mono', monospace" }}>{calibrationInsights.insights.filter(i => i.activeCorrection !== 0).length}</div>
              <div style={{ fontSize: 8, fontWeight: 700, color: 'rgba(255,255,255,0.3)', letterSpacing: '0.1em', marginTop: 2 }}>ACTIVE FIXES</div>
            </div>
          </div>
        </div>

        {/* Per Prop Type Insights */}
        {calibrationInsights.insights.map(insight => {
          const propObj = [...PROP_TYPES, ...BASKETBALL_PROP_TYPES].find(p => p.key === insight.propType);
          const propLabel = propObj ? propObj.label : insight.propType.replace(/_/g, ' ');
          const isActive = insight.activeCorrection !== 0;
          const isOverProjecting = insight.biasDirection === 'over-projecting';

          return (
            <div key={`${insight.sport}-${insight.propType}`} data-testid={`calibration-${insight.sport}-${insight.propType}`} style={{
              background: '#0a0a0f',
              border: `1px solid ${isActive ? 'rgba(139,92,246,0.25)' : 'rgba(100,100,120,0.15)'}`,
              borderRadius: 10, padding: '10px 14px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{
                    fontSize: 8, fontWeight: 900, letterSpacing: '0.08em', textTransform: 'uppercase',
                    padding: '2px 6px', borderRadius: 4,
                    background: insight.sport === 'soccer' ? 'rgba(16,185,129,0.1)' : 'rgba(245,158,11,0.1)',
                    color: insight.sport === 'soccer' ? '#10b981' : '#f59e0b',
                    border: `1px solid ${insight.sport === 'soccer' ? 'rgba(16,185,129,0.2)' : 'rgba(245,158,11,0.2)'}`,
                  }}>{insight.sport}</span>
                  <span style={{ fontSize: 13, fontWeight: 800, color: '#fff' }}>{propLabel}</span>
                </div>
                {isActive && (
                  <span style={{
                    fontSize: 8, fontWeight: 900, letterSpacing: '0.08em',
                    padding: '2px 6px', borderRadius: 4,
                    background: 'rgba(139,92,246,0.15)', color: '#8b5cf6',
                    border: '1px solid rgba(139,92,246,0.25)',
                  }}>CORRECTING</span>
                )}
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 6, marginBottom: 8 }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 14, fontWeight: 900, color: '#f43f5e', fontFamily: "'JetBrains Mono', monospace" }}>{insight.missCount}</div>
                  <div style={{ fontSize: 7, fontWeight: 700, color: 'rgba(255,255,255,0.3)', letterSpacing: '0.08em' }}>MISSES</div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 14, fontWeight: 900, color: isOverProjecting ? '#f59e0b' : '#60a5fa', fontFamily: "'JetBrains Mono', monospace" }}>
                    {insight.avgErrorPct > 0 ? '+' : ''}{insight.avgErrorPct}%
                  </div>
                  <div style={{ fontSize: 7, fontWeight: 700, color: 'rgba(255,255,255,0.3)', letterSpacing: '0.08em' }}>AVG ERR</div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 14, fontWeight: 900, color: isOverProjecting ? '#f59e0b' : '#60a5fa', fontFamily: "'JetBrains Mono', monospace" }}>
                    {isOverProjecting ? 'HIGH' : 'LOW'}
                  </div>
                  <div style={{ fontSize: 7, fontWeight: 700, color: 'rgba(255,255,255,0.3)', letterSpacing: '0.08em' }}>BIAS</div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 14, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: isActive ? '#8b5cf6' : 'rgba(255,255,255,0.2)' }}>
                    {isActive ? `${insight.activeCorrection > 0 ? '+' : ''}${insight.activeCorrection}%` : '\u2014'}
                  </div>
                  <div style={{ fontSize: 7, fontWeight: 700, color: 'rgba(255,255,255,0.3)', letterSpacing: '0.08em' }}>FIX</div>
                </div>
              </div>

              <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.4)', lineHeight: 1.5 }}>
                {isActive ? (
                  <>System {insight.biasDirection} {propLabel.toLowerCase()} by avg {Math.abs(insight.avgErrorPct)}%. Applying <span style={{ color: '#8b5cf6', fontWeight: 700 }}>{insight.activeCorrection > 0 ? '+' : ''}{insight.activeCorrection}%</span> correction to future predictions.</>
                ) : insight.missCount < 3 ? (
                  <>Need {3 - insight.missCount} more miss{3 - insight.missCount > 1 ? 'es' : ''} to activate auto-correction ({insight.missCount}/3).</>
                ) : (
                  <>Bias too inconsistent to auto-correct ({insight.recentSampleSize} samples tracked).</>
                )}
              </div>
            </div>
          );
        })}

        <button data-testid="calibration-refresh-btn" onClick={() => {
          if (!auth) return;
          setCalibrationLoading(true);
          getCalibrationInsights(auth.email, auth.token)
            .then(data => setCalibrationInsights(data))
            .catch(err => console.error('[CALIBRATION] Refresh error:', err))
            .finally(() => setCalibrationLoading(false));
        }} style={{
          width: '100%', padding: '10px', borderRadius: 8, border: '1px solid rgba(139,92,246,0.2)',
          background: 'rgba(139,92,246,0.06)', color: '#8b5cf6', fontSize: 11, fontWeight: 800,
          cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
          letterSpacing: '0.06em',
        }}>
          <RefreshCw style={{ width: 12, height: 12 }} /> Refresh Insights
        </button>
      </div>
    </div>
  );
}

function PickCard({ pick, liveData, missAnalyses, reanalyzePick, reanalyzingPick, removePickFn, correctingPick, setCorrectingPick, correctValue, setCorrectValue, submitCorrection }) {
  const live = liveData[pick.pickId];
  const isMatchLive = live?.matchStatus === 'live';
  const isMatchFinal = live?.matchStatus === 'final' || pick.status === 'settled';
  const nowVal = isMatchLive ? (live?.currentValue ?? '-') : (pick.actualValue ?? '-');
  const paceVal = isMatchLive ? (live?.pace ?? '-') : nowVal;
  const hitPct = live?.hitPct ?? null;
  const elapsed = live?.elapsed ?? 0;
  const minutesPlayed = live?.minutesPlayed || 0;
  const matchScore = live?.matchScore || pick.matchScore || '';
  const propLabel = [...PROP_TYPES, ...BASKETBALL_PROP_TYPES].find(pt => pt.key === pick.propType)?.label || pick.propType;
  const isOver = pick.recommendation === 'over';
  const lineNum = pick.line || 1;
  const nowNum = typeof nowVal === 'number' ? nowVal : 0;
  const paceNum = typeof paceVal === 'number' ? paceVal : 0;
  const progressPct = Math.min(100, Math.max(0, (nowNum / (lineNum * 1.3)) * 100));
  const lineMarkerPct = Math.min(95, (lineNum / (lineNum * 1.3)) * 100);
  const resultLabel = pick.result === 'hit' ? 'HIT' : pick.result === 'push' ? 'PUSH' : pick.result === 'miss' ? 'MISS' : '';
  const isHit = pick.result === 'hit';
  const isMiss = pick.result === 'miss';
  const isPush = pick.result === 'push';
  const onTrack = isPush ? null : (isOver ? paceNum > lineNum : paceNum < lineNum);
  const statColor = isPush ? 'rgba(255,255,255,0.45)' : (onTrack ? 'var(--accent)' : '#f43f5e');
  const barColor = isPush ? 'rgba(255,255,255,0.25)' : (onTrack ? 'var(--accent)' : '#f43f5e');

  return (
    <div className="live-pick-card" data-testid={`pick-${pick.pickId}`} style={{
      background: '#0a0a0f', borderRadius: 6, padding: 0, overflow: 'hidden',
      border: `1px solid ${isMatchLive ? 'var(--accent)' : isHit ? 'rgba(16,185,129,0.5)' : isMiss ? 'rgba(244,63,94,0.4)' : isPush ? 'rgba(245,158,11,0.5)' : 'rgba(100,100,120,0.2)'}`,
    }}>
      {/* HEADER */}
      <div style={{ padding: '5px 8px 2px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 11, fontWeight: 800, color: '#fff', letterSpacing: '-0.2px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} data-testid="pick-player-name">
            {pick.playerName}
          </div>
          <div style={{ fontSize: 7, fontWeight: 600, color: 'rgba(255,255,255,0.4)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
            {pick.teamName || 'Team'} &middot; {(pick.venue || 'home').toUpperCase()} &middot; {pick.sport === 'basketball' ? 'NBA' : 'Soccer'}
            {pick.trackingId && <span style={{ marginLeft: 4, color: 'rgba(255,255,255,0.2)', fontFamily: 'var(--font-mono)' }}>{pick.trackingId}</span>}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
          {isMatchLive && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 7, fontWeight: 800, color: '#f43f5e' }}>
              <div style={{ width: 4, height: 4, borderRadius: '50%', background: '#f43f5e', animation: 'pulse 1.5s infinite' }} />
              LIVE{live?.quarter ? ` ${live.quarter}` : (live?.period ? ` ${live.period}` : '')}
            </div>
          )}
          {isMatchFinal && !isMatchLive && <div style={{ fontSize: 7, fontWeight: 800, color: 'rgba(255,255,255,0.35)' }}>FINAL</div>}
          {!isMatchLive && !isMatchFinal && <div style={{ fontSize: 7, fontWeight: 700, color: 'rgba(255,255,255,0.25)' }}>SCHED</div>}
          {pick.status === 'live' && (
            <button className="reanalyze-btn" onClick={e => reanalyzePick(pick, e)} disabled={reanalyzingPick === pick.pickId}
              data-testid={`reanalyze-pick-${pick.pickId}`} title="Re-analyze" style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 1 }}>
              {reanalyzingPick === pick.pickId
                ? <Loader2 style={{ width: 9, height: 9, color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
                : <RotateCcw style={{ width: 9, height: 9, color: 'rgba(255,255,255,0.35)' }} />}
            </button>
          )}
          <button style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 1 }}
            onClick={e => removePickFn(pick.pickId, e)} data-testid={`remove-pick-${pick.pickId}`}>
            <Trash2 style={{ width: 9, height: 9, color: 'rgba(255,255,255,0.25)' }} />
          </button>
        </div>
      </div>

      {/* PICK LINE + OPPONENT */}
      <div style={{ padding: '0 8px 3px', fontSize: 8, fontWeight: 800, letterSpacing: '0.04em' }}>
        <span style={{ color: 'rgba(255,255,255,0.4)' }}>PICK: </span>
        <span style={{ color: isOver ? 'var(--accent)' : '#f43f5e' }}>{isOver ? 'OVER' : 'UNDER'} {pick.line}</span>
        <span style={{ color: 'rgba(255,255,255,0.3)', fontSize: 7, marginLeft: 3 }}>{propLabel}</span>
        {matchScore && (isMatchLive || isMatchFinal) && (
          <span style={{ color: 'rgba(255,255,255,0.35)', marginLeft: 6 }}>
            vs {pick.opponentName} <span style={{ color: isMatchLive ? '#f43f5e' : 'rgba(255,255,255,0.5)', fontWeight: 800 }}>{matchScore}</span>
          </span>
        )}
      </div>

      {/* STATS ROW — LIVE/FINAL */}
      {(isMatchLive || isMatchFinal) && (
        <div style={{ padding: '0 8px 3px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
            {[
              { label: 'NOW', value: nowVal, color: statColor },
              { label: 'LINE', value: pick.line, color: 'rgba(255,255,255,0.6)' },
              { label: 'PACE', value: paceVal, color: statColor },
              { label: 'HIT%', value: hitPct != null ? `${hitPct}%` : '-', color: isPush ? 'rgba(255,255,255,0.45)' : (hitPct > 50 ? 'var(--accent)' : '#f43f5e') },
            ].map((stat, i) => (
              <div key={stat.label} style={{ textAlign: 'center', padding: '3px 0', borderRight: i < 3 ? '1px solid rgba(255,255,255,0.04)' : 'none' }}>
                <div style={{ fontSize: 6, fontWeight: 700, color: 'rgba(255,255,255,0.3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{stat.label}</div>
                <div style={{ fontSize: 11, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: stat.color }}>{stat.value}</div>
              </div>
            ))}
          </div>
          <div style={{ position: 'relative', height: 3, background: 'rgba(255,255,255,0.05)', borderRadius: 2, marginTop: 3, overflow: 'visible' }}>
            <div style={{ height: '100%', borderRadius: 2, transition: 'width 0.5s', width: `${progressPct}%`, background: barColor }} />
            <div style={{ position: 'absolute', top: -1, width: 1, height: 5, background: 'rgba(255,255,255,0.5)', left: `${lineMarkerPct}%` }} />
          </div>
        </div>
      )}

      {/* SCHEDULED STATE */}
      {!isMatchLive && !isMatchFinal && (
        <div style={{ padding: '0 8px 3px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
            {[
              { label: 'PROJ', value: pick.projectedValue, color: 'var(--accent)' },
              { label: 'LINE', value: pick.line, color: 'rgba(255,255,255,0.6)' },
              { label: 'CONF', value: `${pick.confidenceScore}%`, color: 'rgba(255,255,255,0.6)' },
            ].map((stat, i) => (
              <div key={stat.label} style={{ textAlign: 'center', padding: '3px 0', borderRight: i < 2 ? '1px solid rgba(255,255,255,0.04)' : 'none' }}>
                <div style={{ fontSize: 6, fontWeight: 700, color: 'rgba(255,255,255,0.3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{stat.label}</div>
                <div style={{ fontSize: 11, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: stat.color }}>{stat.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* FOOTER */}
      <div style={{ padding: '2px 8px 4px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 7, fontFamily: "'JetBrains Mono', monospace", color: 'rgba(255,255,255,0.3)' }}>
          {isMatchLive && <span>{elapsed}&apos;</span>}
          {matchScore && <span>{matchScore}</span>}
          {minutesPlayed > 0 && minutesPlayed < 90 && isMatchFinal && <span style={{ color: '#f59e0b', fontSize: 6 }}>{minutesPlayed}&apos;</span>}
          {resultLabel && (
            <span data-testid="pick-result" style={{
              padding: '1px 4px', borderRadius: 3, fontSize: 7, fontWeight: 900,
              background: isHit ? 'rgba(16,185,129,0.15)' : isMiss ? 'rgba(244,63,94,0.15)' : 'rgba(245,158,11,0.15)',
              color: isHit ? '#10b981' : isMiss ? '#f43f5e' : '#f59e0b',
              border: `1px solid ${isHit ? 'rgba(16,185,129,0.3)' : isMiss ? 'rgba(244,63,94,0.3)' : 'rgba(245,158,11,0.3)'}`,
            }}>{resultLabel}</span>
          )}
          {pick.correctedManually && <span style={{ fontSize: 6, color: 'rgba(255,255,255,0.2)', fontStyle: 'italic' }}>corrected</span>}
          {pick.status === 'settled' && correctingPick !== pick.pickId && (
            <button onClick={() => { setCorrectingPick(pick.pickId); setCorrectValue(String(pick.actualValue || '')); }}
              data-testid={`correct-pick-${pick.pickId}`} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 1, display: 'flex' }}>
              <Edit3 style={{ width: 8, height: 8, color: 'rgba(255,255,255,0.25)' }} />
            </button>
          )}
        </div>
        <span style={{ fontSize: 7, fontWeight: 800, color: 'var(--accent)', fontFamily: "'JetBrains Mono', monospace", letterSpacing: '0.04em' }}>
          {propLabel.toUpperCase()}
        </span>
      </div>

      {/* CORRECTION INPUT */}
      {correctingPick === pick.pickId && (
        <div style={{ padding: '0 8px 4px', display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ fontSize: 7, color: 'rgba(255,255,255,0.4)', whiteSpace: 'nowrap' }}>Actual:</span>
          <input type="number" step="1" value={correctValue} onChange={e => setCorrectValue(e.target.value)}
            data-testid="correct-value-input" autoFocus
            style={{ flex: 1, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 3, padding: '2px 6px', color: '#fff', fontSize: 10, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }} />
          <button onClick={() => submitCorrection(pick.pickId)} data-testid="correct-submit-btn"
            style={{ background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, padding: '6px 12px', fontSize: 11, fontWeight: 800, cursor: 'pointer' }}>Save</button>
          <button onClick={() => { setCorrectingPick(null); setCorrectValue(''); }}
            style={{ background: 'none', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 6, padding: '6px 10px', fontSize: 11, color: 'rgba(255,255,255,0.5)', cursor: 'pointer' }}>Cancel</button>
        </div>
      )}

      {/* AUTO MISS ANALYSIS */}
      {isMiss && missAnalyses[pick.pickId] && (
        <div data-testid={`miss-analysis-${pick.pickId}`} style={{
          margin: '0 8px 6px', padding: '6px 8px', background: 'rgba(244,63,94,0.06)',
          border: '1px solid rgba(244,63,94,0.15)', borderRadius: 6,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 4 }}>
            <Brain style={{ width: 9, height: 9, color: '#f43f5e' }} />
            <span style={{ fontSize: 7, fontWeight: 800, color: '#f43f5e', letterSpacing: '0.08em', textTransform: 'uppercase' }}>Auto-Analysis</span>
            <span style={{ fontSize: 6, color: 'rgba(255,255,255,0.25)', marginLeft: 'auto' }}>{missAnalyses[pick.pickId].modelsResponded?.join(' + ') || ''}</span>
          </div>
          <p style={{ fontSize: 8, color: 'rgba(255,255,255,0.7)', margin: 0, lineHeight: 1.4 }}>{missAnalyses[pick.pickId].primaryReason}</p>
          {missAnalyses[pick.pickId].factors?.length > 0 && (
            <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 3 }}>
              {missAnalyses[pick.pickId].factors.slice(0, 3).map((f) => (
                <span key={f} style={{ fontSize: 6, padding: '1px 4px', borderRadius: 3, background: 'rgba(244,63,94,0.1)', color: 'rgba(244,63,94,0.7)', border: '1px solid rgba(244,63,94,0.15)' }}>{f}</span>
              ))}
            </div>
          )}
          {missAnalyses[pick.pickId].calibrationSuggestions?.[0] && (
            <p style={{ fontSize: 7, color: 'rgba(139,92,246,0.7)', margin: '4px 0 0', fontStyle: 'italic' }}>
              Calibration: {missAnalyses[pick.pickId].calibrationSuggestions[0]}
            </p>
          )}
        </div>
      )}
      {isMiss && !missAnalyses[pick.pickId] && pick.actualValue != null && (
        <div style={{ margin: '0 8px 6px', padding: '4px 8px', display: 'flex', alignItems: 'center', gap: 4, background: 'rgba(244,63,94,0.04)', borderRadius: 6 }}>
          <Loader2 style={{ width: 8, height: 8, color: 'rgba(244,63,94,0.4)', animation: 'spin 1s linear infinite' }} />
          <span style={{ fontSize: 7, color: 'rgba(255,255,255,0.3)' }}>Learning from this miss...</span>
        </div>
      )}
    </div>
  );
}

function PicksList({ savedPicks, trackingView, liveData, missAnalyses, reanalyzePick, reanalyzingPick, removePickFn, correctingPick, setCorrectingPick, correctValue, setCorrectValue, submitCorrection }) {
  const filtered = savedPicks.filter(p => {
    if (trackingView === 'live') return p.status === 'live';
    if (trackingView === 'won') return p.status === 'settled' && p.result === 'hit';
    if (trackingView === 'lost') return p.status === 'settled' && p.result === 'miss';
    if (trackingView === 'pushed') return p.status === 'settled' && p.result === 'push';
    return false;
  });

  if (filtered.length === 0) {
    return (
      <div className="space-y-2">
        <div className="empty-state" data-testid="tracking-empty">
          <div className="empty-icon"><Clock /></div>
          <p className="empty-text">No {trackingView === 'won' ? 'winning' : trackingView === 'lost' ? 'lost' : trackingView === 'pushed' ? 'pushed' : trackingView} picks yet.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {filtered.map(pick => (
        <PickCard
          key={pick.pickId}
          pick={pick}
          liveData={liveData}
          missAnalyses={missAnalyses}
          reanalyzePick={reanalyzePick}
          reanalyzingPick={reanalyzingPick}
          removePickFn={removePickFn}
          correctingPick={correctingPick}
          setCorrectingPick={setCorrectingPick}
          correctValue={correctValue}
          setCorrectValue={setCorrectValue}
          submitCorrection={submitCorrection}
        />
      ))}
    </div>
  );
}
