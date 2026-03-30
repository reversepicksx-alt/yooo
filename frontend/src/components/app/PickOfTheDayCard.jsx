import React from 'react';
import { Zap, Target, TrendingUp, TrendingDown } from 'lucide-react';
import { getPropLabel } from '../../constants';

export function PickOfTheDayCard({ potd, onUse }) {
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
