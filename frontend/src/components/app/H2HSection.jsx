import React from 'react';
import { Activity } from 'lucide-react';

export function H2HSection({ h2hData, propType }) {
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
                {m.targetStat != null ? m.targetStat : '\u2014'}
              </span>
              <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>{propLabel}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
