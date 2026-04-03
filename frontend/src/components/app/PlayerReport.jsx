import React, { useState } from 'react';
import { Check, ChevronDown, ChevronUp, Trophy, TrendingUp, TrendingDown, Zap, Loader2 } from 'lucide-react';

const PROP_LABELS = {
  pass_attempts: 'Pass Att.', shots: 'Shots', shots_on_target: 'SOT',
  goals: 'Goals', assists: 'Assists', tackles: 'Tackles',
  key_passes: 'Key Passes', saves: 'Saves', interceptions: 'Int.',
  blocks: 'Blocks', crosses: 'Crosses', dribbles: 'Dribbles',
  dribbles_success: 'Dribbles (S)', fouls_drawn: 'Fouls Drawn',
  fouls_committed: 'Fouls', shots_assisted: 'Shot Ast.',
  clearances: 'Clearances', duels_won: 'Duels Won',
  yellow_cards: 'Cards',
  points: 'Points', rebounds: 'Rebounds', pts_reb_ast: 'PRA',
  pts_reb: 'P+R', pts_ast: 'P+A', reb_ast: 'R+A',
  blk_stl: 'B+S', three_pointers: '3PT', steals: 'Steals',
  turnovers: 'TO', fgm: 'FGM', ftm: 'FTM', fga: 'FGA', fta: 'FTA', tpa: '3PA',
};

export const PlayerReport = ({
  playerName, teamName, opponentName, venue, position, role,
  props, predictions, loading, onToggleProp, onPredictAll, selectedProps,
}) => {
  const [expandedProp, setExpandedProp] = useState(null);

  const completedPredictions = Object.keys(predictions).length;
  const totalSelected = selectedProps.length;
  const allDone = completedPredictions >= totalSelected && totalSelected > 0;

  // Find best value prop
  let bestProp = null;
  if (allDone) {
    let bestEdge = -Infinity;
    for (const [propType, pred] of Object.entries(predictions)) {
      const prop = props.find(p => p.propType === propType);
      if (!prop || !pred) continue;
      const edge = Math.abs(pred.projectedValue - prop.line);
      const conf = pred.confidenceScore || 0;
      const score = edge * (conf / 100);
      if (score > bestEdge) {
        bestEdge = score;
        bestProp = propType;
      }
    }
  }

  return (
    <div data-testid="player-report" style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: 12,
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '12px 14px',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        background: 'rgba(255,255,255,0.02)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 800, color: '#fff', letterSpacing: '-0.02em' }}>
              {playerName}
            </div>
            <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.4)', marginTop: 2, letterSpacing: '0.05em' }}>
              {teamName} {venue === 'home' ? 'vs' : '@'} {opponentName}
              {position && <span style={{ marginLeft: 8, color: 'rgba(139,92,246,0.6)' }}>{position}{role ? ` · ${role}` : ''}</span>}
            </div>
          </div>
          <div style={{
            fontSize: 8, fontWeight: 700, letterSpacing: '0.1em',
            padding: '3px 8px', borderRadius: 4,
            background: 'rgba(139,92,246,0.1)', color: '#8b5cf6',
            border: '1px solid rgba(139,92,246,0.2)',
          }}>
            BATCH ANALYSIS
          </div>
        </div>
      </div>

      {/* Prop Selection Checklist */}
      {!allDone && !loading && (
        <div style={{ padding: '8px 14px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.3)', marginBottom: 6, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Select Props to Analyze
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {props.map(p => {
              const selected = selectedProps.includes(p.propType);
              return (
                <button
                  key={p.propType}
                  data-testid={`batch-prop-${p.propType}`}
                  onClick={() => onToggleProp(p.propType)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 4,
                    padding: '4px 8px', borderRadius: 6, cursor: 'pointer',
                    fontSize: 10, fontWeight: 700,
                    background: selected ? 'rgba(139,92,246,0.12)' : 'rgba(255,255,255,0.04)',
                    border: `1px solid ${selected ? 'rgba(139,92,246,0.3)' : 'rgba(255,255,255,0.08)'}`,
                    color: selected ? '#a78bfa' : 'rgba(255,255,255,0.5)',
                    transition: 'all 0.15s',
                  }}
                >
                  {selected && <Check style={{ width: 10, height: 10 }} />}
                  {PROP_LABELS[p.propType] || p.propType} {p.line}
                </button>
              );
            })}
          </div>
          {selectedProps.length > 0 && (
            <button
              data-testid="batch-predict-all-btn"
              onClick={onPredictAll}
              style={{
                marginTop: 10, width: '100%', padding: '8px',
                borderRadius: 8, border: 'none', cursor: 'pointer',
                background: 'linear-gradient(135deg, #8b5cf6, #6d28d9)',
                color: '#fff', fontSize: 11, fontWeight: 800,
                letterSpacing: '0.06em',
              }}
            >
              ANALYZE {selectedProps.length} PROPS
            </button>
          )}
        </div>
      )}

      {/* Progress Bar */}
      {loading && (
        <div style={{ padding: '8px 14px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
            <Loader2 style={{ width: 10, height: 10, color: '#8b5cf6', animation: 'spin 1s linear infinite' }} />
            <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.5)' }}>
              Analyzing {completedPredictions}/{totalSelected} props...
            </span>
          </div>
          <div style={{ height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 2, overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 2,
              background: 'linear-gradient(90deg, #8b5cf6, #6d28d9)',
              width: `${(completedPredictions / Math.max(totalSelected, 1)) * 100}%`,
              transition: 'width 0.5s ease',
            }} />
          </div>
        </div>
      )}

      {/* Comparison Table */}
      {completedPredictions > 0 && (
        <div style={{ padding: '0 14px' }}>
          {/* Table Header */}
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 52px 52px 48px 48px 52px',
            gap: 4, padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.06)',
          }}>
            {['PROP', 'LINE', 'PROJ', 'REC', 'CONF', 'EDGE'].map(h => (
              <div key={h} style={{
                fontSize: 7, fontWeight: 700, color: 'rgba(255,255,255,0.25)',
                letterSpacing: '0.1em', textAlign: h === 'PROP' ? 'left' : 'center',
              }}>{h}</div>
            ))}
          </div>

          {/* Table Rows */}
          {selectedProps.map(propType => {
            const prop = props.find(p => p.propType === propType);
            const pred = predictions[propType];
            if (!prop) return null;
            const isOver = pred?.recommendation === 'over';
            const edge = pred ? (pred.projectedValue - prop.line).toFixed(1) : '—';
            const isBest = propType === bestProp;
            const isExpanded = expandedProp === propType;

            return (
              <div key={propType}>
                <div
                  data-testid={`report-row-${propType}`}
                  onClick={() => pred && setExpandedProp(isExpanded ? null : propType)}
                  style={{
                    display: 'grid', gridTemplateColumns: '1fr 52px 52px 48px 48px 52px',
                    gap: 4, padding: '6px 0', cursor: pred ? 'pointer' : 'default',
                    borderBottom: '1px solid rgba(255,255,255,0.03)',
                    background: isBest ? 'rgba(250,204,21,0.04)' : 'transparent',
                    borderLeft: isBest ? '2px solid #facc15' : '2px solid transparent',
                    paddingLeft: 4,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    {isBest && <Trophy style={{ width: 9, height: 9, color: '#facc15' }} />}
                    <span style={{ fontSize: 10, fontWeight: 700, color: isBest ? '#facc15' : '#fff' }}>
                      {PROP_LABELS[propType] || propType}
                    </span>
                    {pred && (isExpanded ? <ChevronUp style={{ width: 8, height: 8, color: 'rgba(255,255,255,0.3)' }} /> : <ChevronDown style={{ width: 8, height: 8, color: 'rgba(255,255,255,0.15)' }} />)}
                  </div>
                  <div style={{ fontSize: 10, fontWeight: 600, color: 'rgba(255,255,255,0.5)', textAlign: 'center', fontFamily: "'JetBrains Mono', monospace" }}>
                    {prop.line}
                  </div>
                  <div style={{ fontSize: 10, fontWeight: 800, textAlign: 'center', fontFamily: "'JetBrains Mono', monospace", color: pred ? (isOver ? '#4ade80' : '#f87171') : 'rgba(255,255,255,0.2)' }}>
                    {pred ? pred.projectedValue : '...'}
                  </div>
                  <div style={{ textAlign: 'center' }}>
                    {pred ? (
                      <span style={{
                        fontSize: 8, fontWeight: 800, padding: '2px 5px', borderRadius: 3,
                        background: isOver ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)',
                        color: isOver ? '#4ade80' : '#f87171',
                      }}>
                        {isOver ? 'OVER' : 'UNDER'}
                      </span>
                    ) : (
                      <Loader2 style={{ width: 10, height: 10, color: 'rgba(255,255,255,0.15)', animation: 'spin 1s linear infinite', margin: '0 auto' }} />
                    )}
                  </div>
                  <div style={{ fontSize: 10, fontWeight: 700, textAlign: 'center', fontFamily: "'JetBrains Mono', monospace", color: pred ? (pred.confidenceScore >= 65 ? '#4ade80' : pred.confidenceScore >= 50 ? '#fbbf24' : '#f87171') : 'rgba(255,255,255,0.2)' }}>
                    {pred ? `${pred.confidenceScore}%` : '—'}
                  </div>
                  <div style={{
                    fontSize: 10, fontWeight: 800, textAlign: 'center',
                    fontFamily: "'JetBrains Mono', monospace",
                    color: pred ? (parseFloat(edge) > 0 ? '#4ade80' : '#f87171') : 'rgba(255,255,255,0.2)',
                  }}>
                    {pred ? (parseFloat(edge) > 0 ? '+' : '') + edge : '—'}
                  </div>
                </div>

                {/* Expanded Detail */}
                {isExpanded && pred && (
                  <div style={{
                    padding: '8px 6px', background: 'rgba(255,255,255,0.02)',
                    borderBottom: '1px solid rgba(255,255,255,0.06)',
                  }}>
                    {pred.reasoning && (
                      <p style={{ fontSize: 9, color: 'rgba(255,255,255,0.6)', margin: '0 0 6px', lineHeight: 1.5 }}>
                        {pred.reasoning}
                      </p>
                    )}
                    {pred.sharpSummary && (
                      <p style={{ fontSize: 8, color: 'rgba(139,92,246,0.7)', margin: 0, fontStyle: 'italic' }}>
                        {pred.sharpSummary}
                      </p>
                    )}
                    {pred.calibration?.applied && (
                      <div style={{ fontSize: 8, color: 'rgba(139,92,246,0.5)', marginTop: 4 }}>
                        Calibration: {pred.calibration.adjustment > 0 ? '+' : ''}{pred.calibration.adjustment}% ({pred.calibration.missCount} misses learned)
                      </div>
                    )}
                    {pred.matchDominance?.applied && (
                      <div style={{ fontSize: 8, color: 'rgba(251,191,36,0.5)', marginTop: 2 }}>
                        Dominance: x{pred.matchDominance.multiplier} (poss {pred.matchDominance.expectedPoss}%)
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Best Value Summary */}
      {allDone && bestProp && (
        <div data-testid="best-value-summary" style={{
          margin: '8px 14px 12px', padding: '8px 10px',
          background: 'rgba(250,204,21,0.06)',
          border: '1px solid rgba(250,204,21,0.15)',
          borderRadius: 8,
          display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <Zap style={{ width: 12, height: 12, color: '#facc15' }} />
          <div>
            <div style={{ fontSize: 9, fontWeight: 800, color: '#facc15', letterSpacing: '0.06em' }}>
              BEST VALUE
            </div>
            <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.7)', marginTop: 1 }}>
              <span style={{ fontWeight: 800, color: '#fff' }}>
                {PROP_LABELS[bestProp] || bestProp}
              </span>
              {' — '}
              {predictions[bestProp]?.recommendation?.toUpperCase()} {predictions[bestProp]?.projectedValue} (
              {predictions[bestProp]?.confidenceScore}% confidence,{' '}
              {((predictions[bestProp]?.projectedValue || 0) - (props.find(p => p.propType === bestProp)?.line || 0)).toFixed(1)} edge)
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
