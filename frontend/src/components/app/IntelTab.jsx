import React, { useState, useEffect, useMemo } from 'react';
import { Loader2, TrendingUp, TrendingDown, BarChart3 } from 'lucide-react';
import { getIntelSheet } from '../../api';
import { toast } from 'sonner';

export function IntelTab({ auth }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sport, setSport] = useState('soccer');

  function fetchData(s) {
    setLoading(true);
    getIntelSheet(auth.email, auth.token, s)
      .then(d => {
        if (d.error) { toast.error(d.error); return; }
        setRows(d.rows || []);
      })
      .catch(() => toast.error('Failed to load intel'))
      .finally(() => setLoading(false));
  }

  useEffect(() => { fetchData(sport); }, [sport]);

  // Compute aggregate stats
  const stats = useMemo(() => {
    if (!rows.length) return null;

    const settled = rows.filter(r => r.result === 'hit' || r.result === 'miss');
    const totalHits = settled.filter(r => r.result === 'hit').length;
    const totalMisses = settled.filter(r => r.result === 'miss').length;
    const total = totalHits + totalMisses;
    const overallRate = total > 0 ? Math.round(totalHits / total * 1000) / 10 : 0;

    // By direction
    const overPicks = settled.filter(r => r.rec === 'over');
    const underPicks = settled.filter(r => r.rec === 'under');
    const overHits = overPicks.filter(r => r.result === 'hit').length;
    const underHits = underPicks.filter(r => r.result === 'hit').length;
    const overRate = overPicks.length > 0 ? Math.round(overHits / overPicks.length * 1000) / 10 : 0;
    const underRate = underPicks.length > 0 ? Math.round(underHits / underPicks.length * 1000) / 10 : 0;

    // By prop type
    const propMap = {};
    settled.forEach(r => {
      const key = r.prop || 'unknown';
      if (!propMap[key]) propMap[key] = { prop: key, hits: 0, misses: 0, overHits: 0, overTotal: 0, underHits: 0, underTotal: 0 };
      if (r.result === 'hit') propMap[key].hits++;
      else propMap[key].misses++;
      if (r.rec === 'over') {
        propMap[key].overTotal++;
        if (r.result === 'hit') propMap[key].overHits++;
      } else {
        propMap[key].underTotal++;
        if (r.result === 'hit') propMap[key].underHits++;
      }
    });
    const propStats = Object.values(propMap)
      .map(p => ({
        ...p,
        total: p.hits + p.misses,
        rate: p.hits + p.misses > 0 ? Math.round(p.hits / (p.hits + p.misses) * 1000) / 10 : 0,
        overRate: p.overTotal > 0 ? Math.round(p.overHits / p.overTotal * 1000) / 10 : 0,
        underRate: p.underTotal > 0 ? Math.round(p.underHits / p.underTotal * 1000) / 10 : 0,
        bestDir: p.overTotal > 0 && p.underTotal > 0
          ? (p.overHits / p.overTotal > p.underHits / p.underTotal ? 'OVER' : 'UNDER')
          : p.overTotal > 0 ? 'OVER' : 'UNDER',
      }))
      .sort((a, b) => b.total - a.total);

    // By position
    const posMap = {};
    settled.forEach(r => {
      const key = r.position || 'Unknown';
      if (!posMap[key]) posMap[key] = { pos: key, hits: 0, misses: 0 };
      if (r.result === 'hit') posMap[key].hits++;
      else posMap[key].misses++;
    });
    const posStats = Object.values(posMap)
      .map(p => ({ ...p, total: p.hits + p.misses, rate: p.hits + p.misses > 0 ? Math.round(p.hits / (p.hits + p.misses) * 1000) / 10 : 0 }))
      .filter(p => p.total >= 2)
      .sort((a, b) => b.rate - a.rate);

    // By venue
    const homeH = settled.filter(r => r.venue === 'home' && r.result === 'hit').length;
    const homeT = settled.filter(r => r.venue === 'home').length;
    const awayH = settled.filter(r => r.venue === 'away' && r.result === 'hit').length;
    const awayT = settled.filter(r => r.venue === 'away').length;

    // Average error
    const errors = settled.filter(r => typeof r.error === 'number').map(r => r.error);
    const avgError = errors.length > 0 ? (errors.reduce((a, b) => a + b, 0) / errors.length).toFixed(1) : '—';
    const avgAbsError = errors.length > 0 ? (errors.reduce((a, b) => a + Math.abs(b), 0) / errors.length).toFixed(1) : '—';

    return {
      total, totalHits, totalMisses, overallRate,
      overRate, underRate, overTotal: overPicks.length, underTotal: underPicks.length,
      propStats, posStats,
      homeRate: homeT > 0 ? Math.round(homeH / homeT * 1000) / 10 : 0,
      awayRate: awayT > 0 ? Math.round(awayH / awayT * 1000) / 10 : 0,
      homeTotal: homeT, awayTotal: awayT,
      avgError, avgAbsError,
    };
  }, [rows]);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 200 }}>
        <Loader2 style={{ width: 20, height: 20, color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
      </div>
    );
  }

  const rateColor = (r) => r >= 65 ? 'var(--accent)' : r >= 50 ? '#f59e0b' : '#f43f5e';

  return (
    <div data-testid="intel-tab" style={{ padding: '0 0 80px' }}>
      {/* Sport Toggle */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
        {['soccer'].map(s => (
          <button key={s} data-testid={`intel-sport-${s}`} onClick={() => setSport(s)} style={{
            flex: 1, padding: '8px 0', borderRadius: 8, fontWeight: 800, fontSize: 11,
            letterSpacing: '0.06em', textTransform: 'uppercase', cursor: 'pointer',
            background: sport === s ? 'var(--accent)' : 'rgba(255,255,255,0.04)',
            color: sport === s ? '#000' : 'rgba(255,255,255,0.5)',
            border: sport === s ? '1.5px solid var(--accent)' : '1.5px solid rgba(255,255,255,0.08)',
          }}>{s}</button>
        ))}
      </div>

      {!stats || stats.total === 0 ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'rgba(255,255,255,0.3)', fontSize: 12 }}>
          No settled picks yet. Make predictions to see your intel.
        </div>
      ) : (
        <>
          {/* Overall Performance */}
          <div data-testid="overall-stats" style={{
            padding: 12, borderRadius: 10, marginBottom: 10,
            background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
              <BarChart3 style={{ width: 12, height: 12, color: 'var(--accent)' }} />
              <span style={{ fontSize: 9, fontWeight: 800, color: 'var(--accent)', letterSpacing: '0.06em' }}>OVERALL PERFORMANCE</span>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <MiniStat label="Hit Rate" value={`${stats.overallRate}%`} color={rateColor(stats.overallRate)} sub={`${stats.total} picks`} />
              <MiniStat label="Hits" value={stats.totalHits} color="var(--accent)" />
              <MiniStat label="Misses" value={stats.totalMisses} color="#f43f5e" />
              <MiniStat label="Avg Error" value={stats.avgAbsError} color="rgba(255,255,255,0.5)" sub="abs" />
            </div>
          </div>

          {/* Direction Split */}
          <div data-testid="direction-stats" style={{
            padding: 12, borderRadius: 10, marginBottom: 10,
            background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
          }}>
            <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.06em', marginBottom: 8 }}>
              OVER vs UNDER
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <DirectionCard dir="OVER" rate={stats.overRate} total={stats.overTotal} />
              <DirectionCard dir="UNDER" rate={stats.underRate} total={stats.underTotal} />
            </div>
            <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
              <DirectionCard dir="HOME" rate={stats.homeRate} total={stats.homeTotal} />
              <DirectionCard dir="AWAY" rate={stats.awayRate} total={stats.awayTotal} />
            </div>
          </div>

          {/* Prop Type Breakdown */}
          <div data-testid="prop-breakdown" style={{
            padding: 12, borderRadius: 10, marginBottom: 10,
            background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
          }}>
            <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.06em', marginBottom: 8 }}>
              HIT RATE BY PROP TYPE
            </div>
            {stats.propStats.map(p => (
              <PropRow key={p.prop} {...p} />
            ))}
          </div>

          {/* Position Breakdown */}
          {stats.posStats.length > 0 && (
            <div data-testid="position-breakdown" style={{
              padding: 12, borderRadius: 10, marginBottom: 10,
              background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
            }}>
              <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.06em', marginBottom: 8 }}>
                HIT RATE BY POSITION
              </div>
              {stats.posStats.map(p => (
                <div key={p.pos} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.03)',
                }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.6)', width: 50 }}>{p.pos}</span>
                  <div style={{ flex: 1, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.06)', margin: '0 8px', overflow: 'hidden' }}>
                    <div style={{ width: `${p.rate}%`, height: '100%', borderRadius: 2, background: rateColor(p.rate) }} />
                  </div>
                  <span style={{ fontSize: 10, fontWeight: 800, fontFamily: "'JetBrains Mono', monospace", color: rateColor(p.rate), width: 35, textAlign: 'right' }}>
                    {p.rate}%
                  </span>
                  <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.3)', width: 25, textAlign: 'right' }}>
                    {p.total}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function MiniStat({ label, value, color, sub }) {
  return (
    <div style={{
      flex: 1, padding: '8px 6px', borderRadius: 8,
      background: 'rgba(255,255,255,0.03)', textAlign: 'center',
    }}>
      <div style={{ fontSize: 8, fontWeight: 700, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.05em', marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color }}>{value}</div>
      {sub && <div style={{ fontSize: 7, color: 'rgba(255,255,255,0.25)', marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

function DirectionCard({ dir, rate, total }) {
  const isOver = dir === 'OVER';
  const isHome = dir === 'HOME';
  const color = rate >= 65 ? 'var(--accent)' : rate >= 50 ? '#f59e0b' : '#f43f5e';
  const icon = isOver || isHome
    ? <TrendingUp style={{ width: 10, height: 10, color }} />
    : <TrendingDown style={{ width: 10, height: 10, color }} />;

  return (
    <div style={{
      flex: 1, padding: '8px 10px', borderRadius: 8,
      background: 'rgba(255,255,255,0.03)', display: 'flex',
      alignItems: 'center', justifyContent: 'space-between',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        {icon}
        <span style={{ fontSize: 10, fontWeight: 800, color: 'rgba(255,255,255,0.6)' }}>{dir}</span>
      </div>
      <div style={{ textAlign: 'right' }}>
        <span style={{ fontSize: 14, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color }}>{rate}%</span>
        <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.25)', marginLeft: 4 }}>{total}</span>
      </div>
    </div>
  );
}

function PropRow({ prop, rate, total, hits, misses, overRate, underRate, overTotal, underTotal, bestDir }) {
  const rateColor = rate >= 65 ? 'var(--accent)' : rate >= 50 ? '#f59e0b' : '#f43f5e';
  const label = (prop || '').replace(/_/g, ' ');

  return (
    <div style={{
      padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.03)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.7)', textTransform: 'capitalize' }}>{label}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 12, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: rateColor }}>
            {rate}%
          </span>
          <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.3)' }}>
            {hits}/{total}
          </span>
        </div>
      </div>
      {/* Over/Under split for this prop */}
      <div style={{ display: 'flex', gap: 4 }}>
        {overTotal > 0 && (
          <div style={{
            flex: 1, padding: '3px 6px', borderRadius: 4,
            background: bestDir === 'OVER' ? 'rgba(0,255,136,0.06)' : 'rgba(255,255,255,0.02)',
            border: bestDir === 'OVER' ? '1px solid rgba(0,255,136,0.15)' : '1px solid transparent',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontSize: 8, fontWeight: 800, color: 'rgba(255,255,255,0.4)' }}>OVER</span>
            <span style={{ fontSize: 9, fontWeight: 800, fontFamily: "'JetBrains Mono', monospace", color: overRate >= 65 ? 'var(--accent)' : overRate >= 50 ? '#f59e0b' : '#f43f5e' }}>
              {overRate}% <span style={{ fontSize: 7, color: 'rgba(255,255,255,0.25)' }}>({overTotal})</span>
            </span>
          </div>
        )}
        {underTotal > 0 && (
          <div style={{
            flex: 1, padding: '3px 6px', borderRadius: 4,
            background: bestDir === 'UNDER' ? 'rgba(129,140,248,0.06)' : 'rgba(255,255,255,0.02)',
            border: bestDir === 'UNDER' ? '1px solid rgba(129,140,248,0.15)' : '1px solid transparent',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontSize: 8, fontWeight: 800, color: 'rgba(255,255,255,0.4)' }}>UNDER</span>
            <span style={{ fontSize: 9, fontWeight: 800, fontFamily: "'JetBrains Mono', monospace", color: underRate >= 65 ? 'var(--accent)' : underRate >= 50 ? '#f59e0b' : '#f43f5e' }}>
              {underRate}% <span style={{ fontSize: 7, color: 'rgba(255,255,255,0.25)' }}>({underTotal})</span>
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
