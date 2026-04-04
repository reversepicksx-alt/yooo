import React from 'react';
import {
  Loader2, Clock, BarChart3, Edit3, Trash2, RotateCcw,
  ArrowLeft, TrendingUp, TrendingDown
} from 'lucide-react';
import { PROP_TYPES, BASKETBALL_PROP_TYPES, getPropLabel } from '../../constants';

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
