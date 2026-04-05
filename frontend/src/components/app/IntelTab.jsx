import React, { useState, useEffect } from 'react';
import { BarChart3, TrendingUp, TrendingDown, AlertTriangle, Target, MapPin, Shield, Loader2, ChevronDown, ChevronUp } from 'lucide-react';
import { getIntelDashboard } from '../../api';
import { toast } from 'sonner';

export function IntelTab({ auth }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [section, setSection] = useState('lines');

  useEffect(() => {
    if (auth?.email && auth?.token) {
      setLoading(true);
      getIntelDashboard(auth.email, auth.token)
        .then(d => { if (!d.error) setData(d); else toast.error(d.error); })
        .catch(() => toast.error('Failed to load intel'))
        .finally(() => setLoading(false));
    }
  }, [auth]);

  if (loading) return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
      <Loader2 className="animate-spin" style={{ width: 24, height: 24, color: 'var(--accent)' }} />
    </div>
  );
  if (!data || !data.total) return (
    <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)', fontSize: 13 }}>No settled picks yet</div>
  );

  const sections = [
    { key: 'lines', label: 'Lines', icon: Target },
    { key: 'position', label: 'Position', icon: Shield },
    { key: 'context', label: 'Game Type', icon: BarChart3 },
    { key: 'matchup', label: 'Matchup', icon: MapPin },
    { key: 'misses', label: 'Worst Misses', icon: AlertTriangle },
  ];

  return (
    <div data-testid="intel-tab" style={{ padding: '0 4px' }}>
      {/* Header Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, marginBottom: 14 }}>
        <StatBox label="Overall" value={`${data.overallRate}%`} sub={`${data.total} picks`} color={data.overallRate >= 60 ? 'var(--accent)' : '#f59e0b'} />
        <StatBox label="Hits" value={data.totalHits} sub={`${data.overallRate}%`} color="var(--accent)" />
        <StatBox label="Misses" value={data.totalMisses} sub={`${(100 - data.overallRate).toFixed(1)}%`} color="#f43f5e" />
      </div>

      {/* Over/Under split */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 14 }}>
        {['over', 'under'].map(r => {
          const d2 = data.byRec?.[r];
          if (!d2) return null;
          return (
            <div key={r} style={{ padding: '8px 10px', borderRadius: 10, background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)', textAlign: 'center' }}>
              <div style={{ fontSize: 9, fontWeight: 800, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {r === 'over' ? <TrendingUp style={{ width: 10, height: 10, display: 'inline' }} /> : <TrendingDown style={{ width: 10, height: 10, display: 'inline' }} />} {r}
              </div>
              <div style={{ fontSize: 18, fontWeight: 900, color: d2.rate >= 60 ? 'var(--accent)' : d2.rate >= 50 ? '#f59e0b' : '#f43f5e', marginTop: 2 }}>{d2.rate}%</div>
              <div style={{ fontSize: 8, color: 'var(--text-muted)' }}>{d2.hits}/{d2.total}</div>
            </div>
          );
        })}
      </div>

      {/* Section Tabs */}
      <div style={{ display: 'flex', gap: 2, marginBottom: 14, overflowX: 'auto', paddingBottom: 2 }}>
        {sections.map(s => (
          <button key={s.key} onClick={() => setSection(s.key)} data-testid={`intel-tab-${s.key}`} style={{
            padding: '6px 10px', borderRadius: 8, border: 'none',
            background: section === s.key ? 'var(--accent-dim)' : 'transparent',
            color: section === s.key ? 'var(--accent)' : 'var(--text-muted)',
            fontSize: 10, fontWeight: 800, cursor: 'pointer', whiteSpace: 'nowrap',
            letterSpacing: '0.04em', textTransform: 'uppercase',
            display: 'flex', alignItems: 'center', gap: 4,
          }}>
            <s.icon style={{ width: 11, height: 11 }} /> {s.label}
          </button>
        ))}
      </div>

      {/* === LINES === */}
      {section === 'lines' && (
        <div>
          <SectionTitle>Accuracy by Prop Type</SectionTitle>
          <SortedBars data={data.byProp} showError />

          <SectionTitle style={{ marginTop: 16 }}>Failing Lines (by prop + line range)</SectionTitle>
          <SortedBars data={data.byPropLine} splitLabel showError onlyWeak />

          <SectionTitle style={{ marginTop: 16 }}>All Lines (by prop + exact line)</SectionTitle>
          <SortedBars data={data.byExactLine} splitLabel showError minTotal={2} />
        </div>
      )}

      {/* === POSITION === */}
      {section === 'position' && (
        <div>
          <SectionTitle>By Position Group</SectionTitle>
          <SortedBars data={data.byPosition} />

          <SectionTitle style={{ marginTop: 16 }}>Position + Prop Combo</SectionTitle>
          <SortedBars data={data.byPositionProp} splitLabel showError />
        </div>
      )}

      {/* === GAME TYPE === */}
      {section === 'context' && (
        <div>
          <SectionTitle>By Game Type</SectionTitle>
          <SortedBars data={data.byContext} />

          <SectionTitle style={{ marginTop: 16 }}>Game Type + Prop</SectionTitle>
          <SortedBars data={data.byContextProp} splitLabel showError />

          <SectionTitle style={{ marginTop: 16 }}>By Result (Win/Loss/Draw)</SectionTitle>
          <SortedBars data={data.byResultType} />

          <SectionTitle style={{ marginTop: 16 }}>Result + Prop</SectionTitle>
          <SortedBars data={data.byResultProp} splitLabel showError onlyWeak />
        </div>
      )}

      {/* === MATCHUP === */}
      {section === 'matchup' && (
        <div>
          <SectionTitle>By Venue</SectionTitle>
          <SortedBars data={data.byVenue} />

          <SectionTitle style={{ marginTop: 16 }}>Venue + Prop</SectionTitle>
          <SortedBars data={data.byVenueProp} splitLabel showError />

          <SectionTitle style={{ marginTop: 16 }}>By League</SectionTitle>
          <SortedBars data={data.byLeague} labelMap={data.leagueNames} />

          <SectionTitle style={{ marginTop: 16 }}>By Confidence Band</SectionTitle>
          <SortedBars data={data.byConfBand} />
        </div>
      )}

      {/* === WORST MISSES === */}
      {section === 'misses' && (
        <div>
          <SectionTitle>Biggest Misses (by projection error)</SectionTitle>
          {(data.worstMisses || []).map((m, i) => (
            <MissCard key={i} miss={m} rank={i + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

function StatBox({ label, value, sub, color }) {
  return (
    <div style={{ padding: '10px 8px', borderRadius: 10, textAlign: 'center', background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)' }}>
      <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 900, color }}>{value}</div>
      {sub && <div style={{ fontSize: 8, color: 'var(--text-muted)', marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

function SectionTitle({ children, style }) {
  return (
    <div style={{ fontSize: 9, fontWeight: 800, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8, ...style }}>
      {children}
    </div>
  );
}

function RateBar({ label, hits, total, rate, avgError, compact }) {
  const color = rate >= 70 ? 'var(--accent)' : rate >= 50 ? '#f59e0b' : '#f43f5e';
  return (
    <div style={{ marginBottom: compact ? 4 : 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: compact ? 9 : 10, fontWeight: 700, marginBottom: 2 }}>
        <span style={{ color: 'var(--text-secondary)', maxWidth: '55%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
        <span style={{ color, whiteSpace: 'nowrap' }}>
          {rate}% <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>({hits}/{total})</span>
          {avgError !== undefined && avgError !== 0 && (
            <span style={{ color: avgError > 0 ? '#f59e0b' : '#818cf8', marginLeft: 4, fontSize: 8 }}>
              {avgError > 0 ? '+' : ''}{avgError}
            </span>
          )}
        </span>
      </div>
      <div style={{ height: compact ? 3 : 4, borderRadius: 2, background: 'rgba(255,255,255,0.06)' }}>
        <div style={{ height: '100%', borderRadius: 2, width: `${Math.min(rate, 100)}%`, background: color, transition: 'width 0.5s ease' }} />
      </div>
    </div>
  );
}

function SortedBars({ data, splitLabel, showError, onlyWeak, minTotal = 1, labelMap }) {
  if (!data) return null;
  let entries = Object.entries(data).filter(([, v]) => v.total >= minTotal);
  if (onlyWeak) entries = entries.filter(([, v]) => v.rate < 55);
  entries.sort((a, b) => a[1].rate - b[1].rate); // worst first

  if (entries.length === 0) return (
    <div style={{ fontSize: 10, color: 'var(--text-muted)', padding: '8px 0' }}>No weak spots found</div>
  );

  return entries.map(([k, v]) => {
    let label = k;
    if (splitLabel && k.includes('|')) {
      const parts = k.split('|');
      label = `${parts[0].replace(/_/g, ' ')} (${parts[1].replace(/_/g, ' ')})`;
    } else {
      label = (labelMap && labelMap[k]) || k.replace(/_/g, ' ');
    }
    return (
      <RateBar
        key={k}
        label={label}
        hits={v.hits}
        total={v.total}
        rate={v.rate}
        avgError={showError ? v.avgError : undefined}
        compact={splitLabel}
      />
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
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <div style={{ fontSize: 12, fontWeight: 800, color: '#fff' }}>
          <span style={{ color: '#f43f5e', marginRight: 6 }}>#{rank}</span>
          {miss.player}
        </div>
        <span style={{ fontSize: 10, fontWeight: 800, color: '#f43f5e', background: 'rgba(244,63,94,0.1)', padding: '2px 6px', borderRadius: 4 }}>
          OFF BY {errorAmt}
        </span>
      </div>
      <div style={{ fontSize: 10, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
        {miss.prop.replace(/_/g, ' ')} {miss.rec?.toUpperCase()} {miss.line} | Proj: {miss.projected} | Actual: {miss.actual}
      </div>
      <div style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 2 }}>
        {miss.team} vs {miss.opponent} | {miss.venue?.toUpperCase()} | {miss.context} | Score: {miss.score} | Conf: {miss.confidence}%
      </div>
    </div>
  );
}
