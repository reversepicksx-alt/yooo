import React, { useState } from 'react';
import {
  Loader2, Clock, BarChart3, Edit3, Trash2, RotateCcw,
  ArrowLeft, TrendingUp, TrendingDown, Brain, ChevronDown, ChevronUp
} from 'lucide-react';
import { PROP_TYPES, BASKETBALL_PROP_TYPES, getPropLabel } from '../../constants';
import { getPickAnalysis } from '../../api';

export function TrackingTab({
  auth, savedPicks, liveData, livePickCount,
  trackingView, setTrackingView,
  missAnalyses, reanalyzePick, reanalyzingPick,
  removePickFn,
  correctingPick, setCorrectingPick, correctValue, setCorrectValue, submitCorrection,
  selectedPick, setSelectedPick,
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
            </div>
          </div>
        </div>

        {/* USER RECORD TRACKER */}
        <RecordTracker savedPicks={savedPicks} />

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
            auth={auth}
          />
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
      background: '#0a0a0f', border: '2px solid rgba(16,185,129,0.12)',
      borderRadius: 10, padding: '8px 10px', marginBottom: 4,
      boxShadow: '0 0 10px rgba(16,185,129,0.04)',
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

function PickCard({ pick, liveData, missAnalyses, reanalyzePick, reanalyzingPick, removePickFn, correctingPick, setCorrectingPick, correctValue, setCorrectValue, submitCorrection, auth }) {
  const [expanded, setExpanded] = useState(false);
  const [analysis, setAnalysis] = useState(null);
  const [loadingAnalysis, setLoadingAnalysis] = useState(false);

  const toggleAnalysis = async (e) => {
    e.stopPropagation();
    if (expanded) { setExpanded(false); return; }
    if (analysis) { setExpanded(true); return; }
    setLoadingAnalysis(true);
    try {
      const res = await getPickAnalysis(auth.email, auth.token, pick.pickId);
      if (res.found) { setAnalysis(res.analysis); setExpanded(true); }
      else { setAnalysis({ notFound: true }); setExpanded(true); }
    } catch { setAnalysis({ notFound: true }); setExpanded(true); }
    finally { setLoadingAnalysis(false); }
  };

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
      background: '#0a0a0f', borderRadius: 14, padding: 0, overflow: 'hidden',
      border: `2px solid ${isMatchLive ? 'rgba(16,185,129,0.4)' : isHit ? 'rgba(16,185,129,0.35)' : isMiss ? 'rgba(244,63,94,0.3)' : isPush ? 'rgba(245,158,11,0.35)' : 'rgba(16,185,129,0.1)'}`,
      boxShadow: isMatchLive ? '0 0 16px rgba(16,185,129,0.1)' : isHit ? '0 0 12px rgba(16,185,129,0.08)' : isMiss ? '0 0 12px rgba(244,63,94,0.06)' : '0 0 8px rgba(16,185,129,0.04)',
      cursor: 'pointer',
    }} onClick={toggleAnalysis}>
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
            <button className="reanalyze-btn" onClick={e => { e.stopPropagation(); reanalyzePick(pick, e); }} disabled={reanalyzingPick === pick.pickId}
              data-testid={`reanalyze-pick-${pick.pickId}`} title="Re-analyze" style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 1 }}>
              {reanalyzingPick === pick.pickId
                ? <Loader2 style={{ width: 9, height: 9, color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
                : <RotateCcw style={{ width: 9, height: 9, color: 'rgba(255,255,255,0.35)' }} />}
            </button>
          )}
          <button style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 1 }}
            onClick={e => { e.stopPropagation(); removePickFn(pick.pickId, e); }} data-testid={`remove-pick-${pick.pickId}`}>
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
            <button onClick={(e) => { e.stopPropagation(); setCorrectingPick(pick.pickId); setCorrectValue(String(pick.actualValue || '')); }}
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
        <div style={{ padding: '0 8px 4px', display: 'flex', alignItems: 'center', gap: 4 }} onClick={e => e.stopPropagation()}>
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

      {/* TAP HINT + LOADING */}
      <div style={{ 
        padding: '3px 8px', textAlign: 'center', 
        borderTop: '1px solid rgba(255,255,255,0.04)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4,
      }}>
        {loadingAnalysis ? (
          <Loader2 style={{ width: 10, height: 10, color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
        ) : expanded ? (
          <ChevronUp style={{ width: 10, height: 10, color: 'var(--accent)' }} />
        ) : (
          <ChevronDown style={{ width: 10, height: 10, color: 'rgba(255,255,255,0.2)' }} />
        )}
        <span style={{ fontSize: 7, color: expanded ? 'var(--accent)' : 'rgba(255,255,255,0.2)', fontWeight: 700, letterSpacing: '0.04em' }}>
          {loadingAnalysis ? 'LOADING ANALYSIS...' : expanded ? 'HIDE ANALYSIS' : 'TAP FOR ANALYSIS'}
        </span>
      </div>

      {/* EXPANDED ANALYSIS */}
      {expanded && analysis && !analysis.notFound && (
        <AnalysisPanel analysis={analysis} />
      )}
      {expanded && analysis?.notFound && (
        <div style={{ padding: '12px 8px', textAlign: 'center', color: 'rgba(255,255,255,0.4)', fontSize: 10, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
          No stored analysis found for this pick.
        </div>
      )}

      {/* AUTO MISS ANALYSIS — REMOVED (useless for unpredictable scenarios, drains AI credits) */}
    </div>
  );
}

function PicksList({ savedPicks, trackingView, liveData, missAnalyses, reanalyzePick, reanalyzingPick, removePickFn, correctingPick, setCorrectingPick, correctValue, setCorrectValue, submitCorrection, auth }) {
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
          auth={auth}
        />
      ))}
    </div>
  );
}


function AnalysisPanel({ analysis }) {
  const a = analysis;
  const matchup = a.matchupOverview || {};

  // Convert decimal odds to American format
  const toAmerican = (dec) => {
    if (!dec || dec <= 1) return '';
    if (dec >= 2) return `+${Math.round((dec - 1) * 100)}`;
    return `${Math.round(-100 / (dec - 1))}`;
  };

  return (
    <div data-testid="analysis-panel" style={{
      borderTop: '1px solid rgba(16,185,129,0.15)',
      background: 'rgba(16,185,129,0.02)',
      padding: '10px 10px 12px',
      animation: 'fadeIn 0.2s ease',
    }} onClick={e => e.stopPropagation()}>
      {/* Projection Summary */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, marginBottom: 10 }}>
        <AnalysisStat label="Projected" value={a.projectedValue} color="var(--accent)" />
        <AnalysisStat label="Confidence" value={`${a.confidenceScore}%`}
          color={a.confidenceScore >= 70 ? 'var(--accent)' : a.confidenceScore >= 55 ? '#f59e0b' : '#f43f5e'} />
        <AnalysisStat label="Rec" value={a.recommendation?.toUpperCase()}
          color={a.recommendation === 'over' ? 'var(--accent)' : '#818cf8'} />
      </div>

      {/* Edge Strength Badge */}
      {a.edgeStrength && (
        <div style={{
          textAlign: 'center', marginBottom: 8, padding: '3px 0',
          borderRadius: 6, fontSize: 9, fontWeight: 900, letterSpacing: '0.08em',
          background: a.edgeStrength === 'STRONG' ? 'rgba(16,185,129,0.12)' : a.edgeStrength === 'LEAN' ? 'rgba(245,158,11,0.12)' : 'rgba(244,63,94,0.12)',
          color: a.edgeStrength === 'STRONG' ? 'var(--accent)' : a.edgeStrength === 'LEAN' ? '#f59e0b' : '#f43f5e',
          border: `1px solid ${a.edgeStrength === 'STRONG' ? 'rgba(16,185,129,0.25)' : a.edgeStrength === 'LEAN' ? 'rgba(245,158,11,0.25)' : 'rgba(244,63,94,0.25)'}`,
        }}>
          {a.edgeStrength} EDGE
        </div>
      )}

      {/* Calibration Corrections Applied */}
      {a.calibrationApplied?.corrections?.length > 0 && (
        <div style={{ marginBottom: 8, padding: '6px 8px', borderRadius: 8, background: 'rgba(129,140,248,0.06)', border: '1px solid rgba(129,140,248,0.15)' }}>
          <div style={{ fontSize: 8, fontWeight: 800, color: '#818cf8', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 3 }}>
            Calibration Applied ({a.calibrationApplied.sampleSize} samples)
          </div>
          {a.calibrationApplied.originalProjection !== a.calibrationApplied.correctedProjection && (
            <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.5)' }}>
              Projection: {a.calibrationApplied.originalProjection} → {a.calibrationApplied.correctedProjection}
            </div>
          )}
          {a.calibrationApplied.originalRecommendation !== a.calibrationApplied.finalRecommendation && (
            <div style={{ fontSize: 8, color: '#f59e0b', fontWeight: 700 }}>
              Flipped: {a.calibrationApplied.originalRecommendation.toUpperCase()} → {a.calibrationApplied.finalRecommendation.toUpperCase()}
            </div>
          )}
          {a.calibrationApplied.corrections.map((c, i) => (
            <div key={i} style={{ fontSize: 7, color: 'rgba(255,255,255,0.35)', lineHeight: 1.4 }}>• {c}</div>
          ))}
        </div>
      )}

      {/* Matchup Overview */}
      {matchup.homeTeam && (
        <div style={{ marginBottom: 10, padding: '6px 8px', borderRadius: 8, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ fontSize: 8, fontWeight: 800, color: 'var(--accent)', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 4 }}>Matchup</div>
          <div style={{ fontSize: 10, fontWeight: 700, color: '#fff' }}>
            {matchup.homeTeam} vs {matchup.awayTeam}
          </div>
          {matchup.favorite && (
            <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>
              Favorite: {matchup.favorite}
              {matchup.moneyline && ` (1: ${toAmerican(matchup.moneyline.home)} | X: ${toAmerican(matchup.moneyline.draw)} | 2: ${toAmerican(matchup.moneyline.away)})`}
            </div>
          )}
          {matchup.expectedGameType && (
            <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.4)', marginTop: 1 }}>
              Expected: {matchup.expectedGameType}{matchup.keyMatchupFactor ? ` — ${matchup.keyMatchupFactor}` : ''}
            </div>
          )}
        </div>
      )}

      {/* Sharp Summary */}
      {a.sharpSummary && (
        <AnalysisSection title="Sharp Summary" text={a.sharpSummary} />
      )}

      {/* Key Evidence */}
      {a.keyEvidence && (
        <AnalysisSection title="Key Evidence" text={a.keyEvidence} />
      )}

      {/* Reasoning */}
      {a.reasoning && (
        <AnalysisSection title="Reasoning" text={a.reasoning} />
      )}

      {/* Scenario Analysis */}
      {a.scenarioAnalysis && (
        <AnalysisSection title="Scenarios" text={a.scenarioAnalysis} />
      )}

      {/* Game Flow */}
      {a.gameFlowDynamics && (
        <AnalysisSection title="Game Flow" text={a.gameFlowDynamics} />
      )}

      {/* Sub Risk */}
      {a.subRisk && (
        <AnalysisSection title="Sub Risk" text={a.subRisk} />
      )}

      {/* Tactical Breakdown (markdown-ish) */}
      {a.tacticalBreakdown && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 8, fontWeight: 800, color: 'var(--accent)', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 4 }}>
            Tactical Breakdown
          </div>
          <div style={{
            fontSize: 9, color: 'rgba(255,255,255,0.7)', lineHeight: 1.6,
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            padding: '6px 8px', borderRadius: 8,
            background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)',
          }}>
            {a.tacticalBreakdown.replace(/\*\*/g, '').replace(/##/g, '')}
          </div>
        </div>
      )}

      {/* Consensus Note */}
      {a.consensusNote && (
        <div style={{ marginTop: 6, fontSize: 8, color: 'rgba(16,185,129,0.7)', fontWeight: 700, fontStyle: 'italic' }}>
          {a.consensusNote}
        </div>
      )}
    </div>
  );
}

function AnalysisStat({ label, value, color }) {
  return (
    <div style={{ textAlign: 'center', padding: '5px 0', borderRadius: 6, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
      <div style={{ fontSize: 7, fontWeight: 700, color: 'rgba(255,255,255,0.35)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 900, color, fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
    </div>
  );
}

function AnalysisSection({ title, text }) {
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ fontSize: 8, fontWeight: 800, color: 'var(--text-secondary)', letterSpacing: '0.04em', textTransform: 'uppercase', marginBottom: 2 }}>{title}</div>
      <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.6)', lineHeight: 1.5 }}>{text}</div>
    </div>
  );
}
