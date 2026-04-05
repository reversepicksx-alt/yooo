import React, { useState, useEffect } from 'react';
import { BarChart3, TrendingUp, TrendingDown, AlertTriangle, Target, MapPin, Shield, Loader2, Zap, DollarSign, Activity } from 'lucide-react';
import { getIntelDashboard, backfillPositions } from '../../api';
import { toast } from 'sonner';

export function IntelTab({ auth }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sport, setSport] = useState('soccer');
  const [section, setSection] = useState('lines');
  const [backfilling, setBackfilling] = useState(false);

  function fetchData(s) {
    setLoading(true);
    getIntelDashboard(auth.email, auth.token, s)
      .then(d => { if (!d.error) setData(d); else toast.error(d.error); })
      .catch(() => toast.error('Failed to load intel'))
      .finally(() => setLoading(false));
  }

  async function runBackfill() {
    setBackfilling(true);
    try {
      const res = await backfillPositions(auth.email, auth.token);
      if (res.success) {
        toast.success(`Backfilled ${res.picksUpdated} picks with exact positions`);
        fetchData(sport);
      } else {
        toast.error(res.error || 'Backfill failed');
      }
    } catch { toast.error('Backfill failed'); }
    finally { setBackfilling(false); }
  }

  useEffect(() => { if (auth?.email) fetchData(sport); }, [sport]);

  const sections = [
    { key: 'lines', label: 'Lines', icon: Target },
    { key: 'position', label: 'Position', icon: Shield },
    { key: 'gametype', label: 'Game Type', icon: Zap },
    { key: 'moneyline', label: 'Moneyline', icon: DollarSign },
    { key: 'calibration', label: 'Calibration', icon: Activity },
    { key: 'matchup', label: 'Venue & League', icon: MapPin },
    { key: 'misses', label: 'Worst Misses', icon: AlertTriangle },
  ];

  return (
    <div data-testid="intel-tab" style={{ padding: '0 4px' }}>
      {/* Sport Toggle */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 14 }}>
        {['soccer', 'basketball'].map(s => (
          <button key={s} onClick={() => setSport(s)} data-testid={`intel-sport-${s}`} style={{
            flex: 1, padding: '8px 0', borderRadius: 10, fontSize: 11, fontWeight: 900,
            textTransform: 'uppercase', letterSpacing: '0.06em', cursor: 'pointer',
            border: '2px solid', transition: 'all 0.2s',
            borderColor: sport === s ? 'var(--accent)' : 'rgba(255,255,255,0.06)',
            background: sport === s ? 'var(--accent-dim)' : 'transparent',
            color: sport === s ? 'var(--accent)' : 'var(--text-muted)',
          }}>{s}</button>
        ))}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
          <Loader2 className="animate-spin" style={{ width: 24, height: 24, color: 'var(--accent)' }} />
        </div>
      ) : !data || !data.total ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)', fontSize: 13 }}>No settled {sport} picks yet</div>
      ) : (
        <>
          {/* Header Stats */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, marginBottom: 10 }}>
            <StatCard label="Hit Rate" value={`${data.overallRate}%`} sub={`${data.total} picks`}
              color={data.overallRate >= 65 ? 'var(--accent)' : data.overallRate >= 50 ? '#f59e0b' : '#f43f5e'} />
            <StatCard label="Hits" value={data.totalHits} color="var(--accent)" />
            <StatCard label="Misses" value={data.totalMisses} color="#f43f5e" />
          </div>

          {/* Over/Under */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 14 }}>
            {['over', 'under'].map(r => {
              const d2 = data.byRec?.[r];
              if (!d2) return null;
              return (
                <div key={r} style={{
                  padding: '10px 12px', borderRadius: 10, textAlign: 'center',
                  background: d2.rate >= 60 ? 'rgba(16,185,129,0.04)' : d2.rate >= 50 ? 'rgba(245,158,11,0.04)' : 'rgba(244,63,94,0.04)',
                  border: `1px solid ${d2.rate >= 60 ? 'rgba(16,185,129,0.12)' : d2.rate >= 50 ? 'rgba(245,158,11,0.12)' : 'rgba(244,63,94,0.12)'}`,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4, marginBottom: 4 }}>
                    {r === 'over' ? <TrendingUp style={{ width: 12, height: 12, color: 'var(--accent)' }} /> : <TrendingDown style={{ width: 12, height: 12, color: '#818cf8' }} />}
                    <span style={{ fontSize: 10, fontWeight: 900, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{r}</span>
                  </div>
                  <div style={{ fontSize: 22, fontWeight: 900, color: d2.rate >= 60 ? 'var(--accent)' : d2.rate >= 50 ? '#f59e0b' : '#f43f5e' }}>{d2.rate}%</div>
                  <div style={{ fontSize: 9, color: 'var(--text-muted)', fontWeight: 700 }}>{d2.hits}/{d2.total}</div>
                </div>
              );
            })}
          </div>

          {/* Section Tabs */}
          <div style={{ display: 'flex', gap: 2, marginBottom: 14, overflowX: 'auto', paddingBottom: 4 }}>
            {sections.map(s => (
              <button key={s.key} onClick={() => setSection(s.key)} data-testid={`intel-section-${s.key}`} style={{
                padding: '7px 10px', borderRadius: 8, border: '1.5px solid',
                borderColor: section === s.key ? 'var(--accent)' : 'transparent',
                background: section === s.key ? 'var(--accent-dim)' : 'rgba(255,255,255,0.02)',
                color: section === s.key ? 'var(--accent)' : 'var(--text-muted)',
                fontSize: 9, fontWeight: 800, cursor: 'pointer', whiteSpace: 'nowrap',
                letterSpacing: '0.04em', textTransform: 'uppercase',
                display: 'flex', alignItems: 'center', gap: 4,
              }}>
                <s.icon style={{ width: 10, height: 10 }} /> {s.label}
              </button>
            ))}
          </div>

          {/* === LINES === */}
          {section === 'lines' && (
            <div>
              <SectionHeader title="Accuracy by Prop Type" />
              <SortedBars data={data.byProp} showError />

              <SectionHeader title="Failing Lines" subtitle="Prop + line range combos below 55%" />
              <SortedBars data={data.byPropLine} splitLabel showError onlyWeak />

              <SectionHeader title="All Exact Lines" subtitle="Min 2 picks" />
              <SortedBars data={data.byExactLine} splitLabel showError minTotal={2} />
            </div>
          )}

          {/* === POSITION === */}
          {section === 'position' && (
            <div>
              <SectionHeader title="By Exact Position" subtitle="CB, LB, CM, CAM, LW, ST, etc." />
              {data.byPosition?.['Unknown'] && data.byPosition['Unknown'].total > 0 && (
                <button onClick={runBackfill} disabled={backfilling} data-testid="backfill-positions-btn" style={{
                  width: '100%', padding: '8px 0', marginBottom: 10, borderRadius: 8, border: '1.5px solid var(--accent)',
                  background: 'var(--accent-dim)', color: 'var(--accent)', fontSize: 10, fontWeight: 800,
                  cursor: backfilling ? 'wait' : 'pointer', opacity: backfilling ? 0.6 : 1,
                  letterSpacing: '0.04em', textTransform: 'uppercase',
                }}>
                  {backfilling ? 'Backfilling...' : `Backfill ${data.byPosition['Unknown'].total} picks missing positions`}
                </button>
              )}
              <SortedBars data={data.byPosition} />

              <SectionHeader title="Position + Prop Breakdown" subtitle="How each exact position performs per stat" />
              <SortedBars data={data.byPositionProp} splitLabel showError />
            </div>
          )}

          {/* === GAME TYPE === */}
          {section === 'gametype' && (
            <div>
              <SectionHeader title="By Game Flow" subtitle="Blowout / Close / Normal" />
              <SortedBars data={data.byContext} />

              <SectionHeader title="Game Flow + Prop" subtitle="Which props fail in which game types" />
              <SortedBars data={data.byContextProp} splitLabel showError />
            </div>
          )}

          {/* === MONEYLINE === */}
          {section === 'moneyline' && (
            <div>
              <SectionHeader title="By Match Result" subtitle="Did player's team win, lose, or draw?" />
              <SortedBars data={data.byResultType} />

              <SectionHeader title="Result + Prop" />
              <SortedBars data={data.byResultProp} splitLabel showError />

              <SectionHeader title="By Score Margin" subtitle="Blowout win/loss vs close game" />
              <SortedBars data={data.byMoneyline} />

              <SectionHeader title="Margin + Prop" subtitle="Which props fail in blowouts vs close games" />
              <SortedBars data={data.byMoneylineProp} splitLabel showError onlyWeak />
            </div>
          )}

          {/* === CALIBRATION ENGINE === */}
          {section === 'calibration' && data.calibration && (
            <CalibrationSection cal={data.calibration} />
          )}

          {/* === VENUE & LEAGUE === */}
          {section === 'matchup' && (
            <div>
              <SectionHeader title="By Venue" />
              <SortedBars data={data.byVenue} />

              <SectionHeader title="Venue + Prop" />
              <SortedBars data={data.byVenueProp} splitLabel showError />

              <SectionHeader title="By League" />
              <SortedBars data={data.byLeague} labelMap={data.leagueNames} />

              <SectionHeader title="By Confidence Band" />
              <SortedBars data={data.byConfBand} />
            </div>
          )}

          {/* === WORST MISSES === */}
          {section === 'misses' && (
            <div>
              <SectionHeader title="Biggest Misses" subtitle="Sorted by projection error (worst first)" />
              {(data.worstMisses || []).map((m, i) => (
                <MissCard key={i} miss={m} rank={i + 1} />
              ))}
              {(!data.worstMisses || data.worstMisses.length === 0) && (
                <div style={{ textAlign: 'center', padding: 20, color: 'var(--text-muted)', fontSize: 11 }}>No misses recorded</div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StatCard({ label, value, sub, color }) {
  return (
    <div style={{
      padding: '10px 8px', borderRadius: 10, textAlign: 'center',
      background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
    }}>
      <div style={{ fontSize: 8, fontWeight: 800, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 900, color }}>{value}</div>
      {sub && <div style={{ fontSize: 8, color: 'var(--text-muted)', marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

function SectionHeader({ title, subtitle }) {
  return (
    <div style={{ marginTop: 16, marginBottom: 8 }}>
      <div style={{ fontSize: 10, fontWeight: 900, color: '#fff', letterSpacing: '0.04em' }}>{title}</div>
      {subtitle && <div style={{ fontSize: 8, color: 'var(--text-muted)', marginTop: 2 }}>{subtitle}</div>}
    </div>
  );
}

function RateBar({ label, hits, total, rate, avgError, compact }) {
  const color = rate >= 70 ? 'var(--accent)' : rate >= 50 ? '#f59e0b' : '#f43f5e';
  const bgColor = rate >= 70 ? 'rgba(16,185,129,0.08)' : rate >= 50 ? 'rgba(245,158,11,0.06)' : 'rgba(244,63,94,0.06)';
  return (
    <div style={{
      marginBottom: compact ? 3 : 5, padding: compact ? '5px 8px' : '7px 10px',
      borderRadius: 8, background: bgColor,
      border: `1px solid ${rate >= 70 ? 'rgba(16,185,129,0.1)' : rate >= 50 ? 'rgba(245,158,11,0.08)' : 'rgba(244,63,94,0.08)'}`,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: compact ? 9 : 10, fontWeight: 700, color: 'var(--text-secondary)', maxWidth: '52%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {label}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {avgError !== undefined && avgError !== 0 && (
            <span style={{ fontSize: 8, fontWeight: 700, color: avgError > 0 ? '#f59e0b' : '#818cf8', padding: '1px 4px', borderRadius: 3, background: 'rgba(0,0,0,0.3)' }}>
              {avgError > 0 ? '+' : ''}{avgError}
            </span>
          )}
          <span style={{ fontSize: compact ? 10 : 11, fontWeight: 900, color }}>
            {rate}%
          </span>
          <span style={{ fontSize: 8, color: 'var(--text-muted)', fontWeight: 600 }}>
            ({hits}/{total})
          </span>
        </div>
      </div>
    </div>
  );
}

function SortedBars({ data, splitLabel, showError, onlyWeak, minTotal = 1, labelMap }) {
  if (!data) return null;
  let entries = Object.entries(data).filter(([, v]) => v.total >= minTotal);
  if (onlyWeak) entries = entries.filter(([, v]) => v.rate < 55);
  entries.sort((a, b) => a[1].rate - b[1].rate);

  if (entries.length === 0) return (
    <div style={{ fontSize: 10, color: 'var(--text-muted)', padding: '10px 0', textAlign: 'center', fontStyle: 'italic' }}>
      {onlyWeak ? 'No weak spots found — all above 55%' : 'No data'}
    </div>
  );

  return entries.map(([k, v]) => {
    let label = k;
    if (splitLabel && k.includes('|')) {
      const parts = k.split('|');
      label = `${parts[0].replace(/_/g, ' ')} | ${parts[1].replace(/_/g, ' ')}`;
    } else {
      label = (labelMap && labelMap[k]) || k.replace(/_/g, ' ');
    }
    return (
      <RateBar key={k} label={label} hits={v.hits} total={v.total} rate={v.rate}
        avgError={showError ? v.avgError : undefined} compact={splitLabel} />
    );
  });
}

function MissCard({ miss, rank }) {
  const errorAmt = Math.abs((miss.actual || 0) - (miss.projected || 0)).toFixed(1);
  return (
    <div data-testid={`worst-miss-${rank}`} style={{
      padding: '10px 12px', borderRadius: 10, marginBottom: 6,
      background: 'rgba(244,63,94,0.04)', border: '1px solid rgba(244,63,94,0.1)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 5 }}>
        <div style={{ fontSize: 12, fontWeight: 800, color: '#fff' }}>
          <span style={{ fontSize: 10, color: '#f43f5e', marginRight: 6, fontWeight: 900 }}>#{rank}</span>
          {miss.player}
        </div>
        <span style={{ fontSize: 9, fontWeight: 900, color: '#f43f5e', background: 'rgba(244,63,94,0.12)', padding: '3px 8px', borderRadius: 6 }}>
          OFF BY {errorAmt}
        </span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 4 }}>
        <Tag>{miss.prop.replace(/_/g, ' ')}</Tag>
        <Tag>{miss.rec?.toUpperCase()} {miss.line}</Tag>
        <Tag>Proj: {miss.projected}</Tag>
        <Tag color="#f43f5e">Actual: {miss.actual}</Tag>
      </div>
      <div style={{ fontSize: 8, color: 'var(--text-muted)', lineHeight: 1.5 }}>
        {miss.team} vs {miss.opponent} | {miss.venue?.toUpperCase()} | {miss.context} | Score: {miss.score} | {miss.position}{miss.role ? ` (${miss.role})` : ''} | Conf: {miss.confidence}%
      </div>
    </div>
  );
}

function Tag({ children, color }) {
  return (
    <span style={{
      fontSize: 8, fontWeight: 700, padding: '2px 6px', borderRadius: 4,
      background: color ? `${color}15` : 'rgba(255,255,255,0.06)',
      color: color || 'var(--text-secondary)',
      border: `1px solid ${color ? `${color}25` : 'rgba(255,255,255,0.08)'}`,
    }}>{children}</span>
  );
}


function CalibrationSection({ cal }) {
  const { confidenceAccuracy, flipCandidates, edgePerformance, errorMap, propRecBreakdown } = cal;

  return (
    <div>
      {/* Confidence Accuracy — AI says X but historically hits Y */}
      <SectionHeader title="Confidence Accuracy" subtitle="What AI says vs. what actually hits" />
      {Object.keys(confidenceAccuracy || {}).length > 0 ? (
        <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
          {Object.entries(confidenceAccuracy).sort((a, b) => b[0].localeCompare(a[0])).map(([key, v]) => {
            const isOverconf = key.startsWith('high') && v.rate < 65;
            return (
              <div key={key} style={{
                flex: 1, padding: '10px 8px', borderRadius: 10, textAlign: 'center',
                background: isOverconf ? 'rgba(244,63,94,0.04)' : 'rgba(255,255,255,0.02)',
                border: `1px solid ${isOverconf ? 'rgba(244,63,94,0.15)' : 'rgba(255,255,255,0.06)'}`,
              }}>
                <div style={{ fontSize: 8, fontWeight: 800, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>
                  {v.label || key}
                </div>
                <div style={{ fontSize: 20, fontWeight: 900, color: v.rate >= 65 ? 'var(--accent)' : v.rate >= 50 ? '#f59e0b' : '#f43f5e' }}>
                  {v.rate}%
                </div>
                <div style={{ fontSize: 8, color: 'var(--text-muted)', fontWeight: 700 }}>{v.hits}/{v.total}</div>
                {isOverconf && (
                  <div style={{ fontSize: 7, color: '#f43f5e', fontWeight: 700, marginTop: 3 }}>OVERCONFIDENT</div>
                )}
              </div>
            );
          })}
        </div>
      ) : <NoData />}

      {/* Edge Strength Performance */}
      {Object.keys(edgePerformance || {}).length > 0 && (
        <>
          <SectionHeader title="Edge Strength Performance" subtitle="STRONG vs LEAN vs LOW conviction picks" />
          <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
            {['STRONG', 'LEAN', 'LOW'].map(label => {
              const v = edgePerformance[label];
              if (!v || v.total === 0) return null;
              return (
                <div key={label} style={{
                  flex: 1, padding: '10px 8px', borderRadius: 10, textAlign: 'center',
                  background: label === 'STRONG' ? 'rgba(16,185,129,0.04)' : label === 'LEAN' ? 'rgba(245,158,11,0.04)' : 'rgba(244,63,94,0.04)',
                  border: `1px solid ${label === 'STRONG' ? 'rgba(16,185,129,0.15)' : label === 'LEAN' ? 'rgba(245,158,11,0.15)' : 'rgba(244,63,94,0.15)'}`,
                }}>
                  <div style={{ fontSize: 8, fontWeight: 800, letterSpacing: '0.06em', marginBottom: 4,
                    color: label === 'STRONG' ? 'var(--accent)' : label === 'LEAN' ? '#f59e0b' : '#f43f5e' }}>
                    {label}
                  </div>
                  <div style={{ fontSize: 20, fontWeight: 900, color: v.rate >= 65 ? 'var(--accent)' : v.rate >= 50 ? '#f59e0b' : '#f43f5e' }}>
                    {v.rate}%
                  </div>
                  <div style={{ fontSize: 8, color: 'var(--text-muted)', fontWeight: 700 }}>{v.hits}/{v.total}</div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Flip Candidates — prop+rec combos with < 50% hit rate */}
      <SectionHeader title="Flip Candidates" subtitle="Prop + direction combos below 50% (auto-flipped at &lt;45% with 15+ samples)" />
      {(flipCandidates || []).length > 0 ? (
        <div style={{ marginBottom: 14 }}>
          {flipCandidates.map((fc, i) => (
            <div key={i} data-testid={`flip-candidate-${i}`} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '8px 10px', borderRadius: 8, marginBottom: 4,
              background: fc.rate < 45 ? 'rgba(244,63,94,0.06)' : 'rgba(245,158,11,0.04)',
              border: `1px solid ${fc.rate < 45 ? 'rgba(244,63,94,0.12)' : 'rgba(245,158,11,0.08)'}`,
            }}>
              <div>
                <span style={{ fontSize: 10, fontWeight: 800, color: '#fff' }}>
                  {fc.prop.replace(/_/g, ' ')}
                </span>
                <span style={{
                  fontSize: 8, fontWeight: 800, padding: '1px 5px', borderRadius: 4, marginLeft: 6,
                  background: fc.rec === 'over' ? 'rgba(16,185,129,0.1)' : 'rgba(129,140,248,0.1)',
                  color: fc.rec === 'over' ? 'var(--accent)' : '#818cf8',
                }}>{fc.rec.toUpperCase()}</span>
                {fc.rate < 45 && fc.total >= 15 && (
                  <span style={{
                    fontSize: 7, fontWeight: 900, padding: '1px 5px', borderRadius: 4, marginLeft: 4,
                    background: 'rgba(244,63,94,0.15)', color: '#f43f5e',
                  }}>AUTO-FLIP ACTIVE</span>
                )}
              </div>
              <div style={{ textAlign: 'right' }}>
                <span style={{ fontSize: 13, fontWeight: 900, color: fc.rate < 45 ? '#f43f5e' : '#f59e0b' }}>
                  {fc.rate}%
                </span>
                <span style={{ fontSize: 8, color: 'var(--text-muted)', marginLeft: 4 }}>({fc.hits}/{fc.total})</span>
              </div>
            </div>
          ))}
        </div>
      ) : <NoData text="No weak combos detected" />}

      {/* Error Direction Map — over-projecting vs under-projecting per prop+venue */}
      <SectionHeader title="Projection Error Map" subtitle="Over-projecting (−) vs Under-projecting (+) per prop + venue" />
      {Object.keys(errorMap || {}).length > 0 ? (
        <div style={{ marginBottom: 14 }}>
          {Object.entries(errorMap)
            .sort((a, b) => Math.abs(b[1].avgError) - Math.abs(a[1].avgError))
            .map(([key, v]) => (
              <div key={key} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '6px 10px', borderRadius: 8, marginBottom: 3,
                background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: '#fff' }}>
                    {v.prop.replace(/_/g, ' ')}
                  </span>
                  <span style={{ fontSize: 8, color: 'var(--text-muted)', fontWeight: 700 }}>
                    {v.venue.toUpperCase()}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{
                    fontSize: 9, fontWeight: 800, fontFamily: "'JetBrains Mono', monospace",
                    color: v.avgError < 0 ? '#f43f5e' : 'var(--accent)',
                  }}>
                    {v.avgError > 0 ? '+' : ''}{v.avgError}
                  </span>
                  <span style={{ fontSize: 7, color: 'var(--text-muted)', fontWeight: 600 }}>
                    {v.direction} (n={v.total})
                  </span>
                </div>
              </div>
            ))}
        </div>
      ) : <NoData />}

      {/* Prop + Rec Full Breakdown */}
      <SectionHeader title="Prop + Direction Breakdown" subtitle="Full hit rates for every prop type + over/under combo" />
      <SortedBars data={propRecBreakdown} splitLabel showError />
    </div>
  );
}

function NoData({ text }) {
  return (
    <div style={{ textAlign: 'center', padding: 20, color: 'var(--text-muted)', fontSize: 10, marginBottom: 14 }}>
      {text || 'Not enough data yet'}
    </div>
  );
}
