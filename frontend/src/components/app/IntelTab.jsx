import React, { useState, useEffect, useMemo } from 'react';
import { Loader2, ArrowUpDown, ArrowUp, ArrowDown, ChevronDown, ChevronUp } from 'lucide-react';
import { getIntelSheet, backfillPositions } from '../../api';
import { toast } from 'sonner';

export function IntelTab({ auth }) {
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState({});
  const [loading, setLoading] = useState(true);
  const [sport, setSport] = useState('soccer');
  const [sortCol, setSortCol] = useState('');
  const [sortDir, setSortDir] = useState('asc');
  const [filterResult, setFilterResult] = useState('all');
  const [filterRec, setFilterRec] = useState('all');
  const [showCal, setShowCal] = useState(true);
  const [backfilling, setBackfilling] = useState(false);

  function fetchData(s) {
    setLoading(true);
    getIntelSheet(auth.email, auth.token, s)
      .then(d => {
        if (d.error) { toast.error(d.error); return; }
        setRows(d.rows || []);
        setMeta({ total: d.total || 0, hits: d.hits || 0, misses: d.misses || 0, rate: d.rate || 0 });
      })
      .catch(() => toast.error('Failed to load intel'))
      .finally(() => setLoading(false));
  }

  async function runBackfill() {
    setBackfilling(true);
    try {
      const res = await backfillPositions(auth.email, auth.token);
      if (res.success) {
        toast.success(`Backfilled ${res.picksUpdated} picks`);
        fetchData(sport);
      } else toast.error(res.error || 'Backfill failed');
    } catch { toast.error('Backfill failed'); }
    finally { setBackfilling(false); }
  }

  useEffect(() => { fetchData(sport); }, [sport]);

  function toggleSort(col) {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortCol(col); setSortDir('asc'); }
  }

  const filtered = useMemo(() => {
    let r = rows;
    if (filterResult !== 'all') r = r.filter(x => x.result === filterResult);
    if (filterRec !== 'all') r = r.filter(x => x.rec === filterRec);
    if (sortCol) {
      r = [...r].sort((a, b) => {
        let va = a[sortCol], vb = b[sortCol];
        if (typeof va === 'number' && typeof vb === 'number') return sortDir === 'asc' ? va - vb : vb - va;
        va = String(va || '').toLowerCase(); vb = String(vb || '').toLowerCase();
        return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
      });
    }
    return r;
  }, [rows, sortCol, sortDir, filterResult, filterRec]);

  // Compute calibration aggregates from rows
  const cal = useMemo(() => {
    if (!rows.length) return null;
    const agg = (key) => {
      const map = {};
      rows.forEach(r => {
        const v = r[key] || '';
        if (!v) return;
        if (!map[v]) map[v] = { hit: 0, miss: 0, push: 0, errSum: 0 };
        map[v][r.result] = (map[v][r.result] || 0) + 1;
        map[v].errSum += r.error || 0;
      });
      return Object.entries(map)
        .map(([k, v]) => {
          const t = v.hit + v.miss + v.push;
          return { label: k, hits: v.hit, total: t, rate: t > 0 ? Math.round(v.hit / t * 1000) / 10 : 0, avgErr: t > 0 ? Math.round(v.errSum / t * 10) / 10 : 0 };
        })
        .filter(x => x.total >= 2)
        .sort((a, b) => a.rate - b.rate);
    };

    // Prop + Rec combo
    const propRec = {};
    rows.forEach(r => {
      const k = `${(r.prop || '').replace(/_/g, ' ')} ${(r.rec || '').toUpperCase()}`;
      if (!propRec[k]) propRec[k] = { hit: 0, miss: 0, push: 0, errSum: 0 };
      propRec[k][r.result] = (propRec[k][r.result] || 0) + 1;
      propRec[k].errSum += r.error || 0;
    });
    const propRecArr = Object.entries(propRec)
      .map(([k, v]) => {
        const t = v.hit + v.miss + v.push;
        return { label: k, hits: v.hit, total: t, rate: t > 0 ? Math.round(v.hit / t * 1000) / 10 : 0, avgErr: t > 0 ? Math.round(v.errSum / t * 10) / 10 : 0 };
      })
      .filter(x => x.total >= 3)
      .sort((a, b) => a.rate - b.rate);

    const missingPos = rows.filter(r => !r.position).length;

    return {
      byPosition: agg('position'),
      byLeague: agg('league'),
      byVenue: agg('venue'),
      byGameType: agg('gameType'),
      byRec: agg('rec'),
      byProp: agg('prop').map(x => ({ ...x, label: x.label.replace(/_/g, ' ') })),
      byMatchResult: agg('matchResult'),
      propRec: propRecArr,
      missingPos,
    };
  }, [rows]);

  const COLS = [
    { key: 'player', label: 'Player', w: 100 },
    { key: 'position', label: 'Pos', w: 42 },
    { key: 'prop', label: 'Prop', w: 90, fmt: v => (v || '').replace(/_/g, ' ') },
    { key: 'rec', label: 'Rec', w: 48, color: v => v === 'over' ? '#10b981' : '#818cf8' },
    { key: 'line', label: 'Line', w: 44, num: true },
    { key: 'proj', label: 'Proj', w: 44, num: true },
    { key: 'actual', label: 'Act', w: 40, num: true },
    { key: 'error', label: 'Err', w: 44, num: true, color: v => v < 0 ? '#f43f5e' : v > 0 ? '#10b981' : '#fff', fmt: v => v > 0 ? `+${v}` : v },
    { key: 'result', label: 'Result', w: 42, color: v => v === 'hit' ? '#10b981' : v === 'miss' ? '#f43f5e' : '#f59e0b' },
    { key: 'league', label: 'League', w: 100 },
    { key: 'venue', label: 'Venue', w: 46, fmt: v => (v || '').toUpperCase() },
    { key: 'gameType', label: 'Game', w: 54 },
    { key: 'matchResult', label: 'Match', w: 42, color: v => v === 'win' ? '#10b981' : v === 'loss' ? '#f43f5e' : '#f59e0b' },
    { key: 'score', label: 'Score', w: 46 },
    { key: 'confidence', label: 'Conf', w: 40, num: true, fmt: v => `${v}%` },
    { key: 'errDir', label: 'Bias', w: 42, color: v => v === 'over' ? '#f43f5e' : v === 'under' ? '#10b981' : 'var(--text-muted)', fmt: v => v === 'over' ? 'OVER' : v === 'under' ? 'UNDER' : '-' },
    { key: 'role', label: 'Role', w: 90 },
    { key: 'opponent', label: 'Opp', w: 90 },
  ];

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 200 }}>
        <Loader2 style={{ width: 20, height: 20, color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
      </div>
    );
  }

  return (
    <div data-testid="intel-tab" style={{ padding: '0 0 80px' }}>
      {/* Sport Toggle */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
        {['soccer', 'basketball'].map(s => (
          <button key={s} data-testid={`intel-sport-${s}`} onClick={() => setSport(s)} style={{
            flex: 1, padding: '8px 0', borderRadius: 8, fontWeight: 800, fontSize: 11,
            letterSpacing: '0.06em', textTransform: 'uppercase', cursor: 'pointer',
            background: sport === s ? 'var(--accent)' : 'rgba(255,255,255,0.04)',
            color: sport === s ? '#000' : 'rgba(255,255,255,0.5)',
            border: sport === s ? '1.5px solid var(--accent)' : '1.5px solid rgba(255,255,255,0.08)',
          }}>{s}</button>
        ))}
      </div>

      {/* Summary Bar */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        <StatBox label="Rate" value={`${meta.rate}%`} color={meta.rate >= 65 ? 'var(--accent)' : meta.rate >= 50 ? '#f59e0b' : '#f43f5e'} sub={`${meta.total} picks`} />
        <StatBox label="Hits" value={meta.hits} color="var(--accent)" />
        <StatBox label="Misses" value={meta.misses} color="#f43f5e" />
      </div>

      {/* Calibration Summary Toggle */}
      {cal && (
        <div style={{ marginBottom: 8 }}>
          <button data-testid="toggle-calibration" onClick={() => setShowCal(!showCal)} style={{
            width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '7px 10px', borderRadius: 8, cursor: 'pointer',
            background: 'rgba(16,185,129,0.04)', border: '1px solid rgba(16,185,129,0.15)',
            color: 'var(--accent)', fontSize: 9, fontWeight: 800, letterSpacing: '0.06em', textTransform: 'uppercase',
          }}>
            <span>CALIBRATION BREAKDOWN</span>
            {showCal ? <ChevronUp style={{ width: 12, height: 12 }} /> : <ChevronDown style={{ width: 12, height: 12 }} />}
          </button>

          {showCal && (
            <div data-testid="calibration-section" style={{ padding: '8px 0 4px' }}>
              {/* Backfill button if positions missing */}
              {cal.missingPos > 0 && (
                <button onClick={runBackfill} disabled={backfilling} data-testid="backfill-btn" style={{
                  width: '100%', padding: '6px 0', marginBottom: 8, borderRadius: 6,
                  border: '1px solid rgba(245,158,11,0.3)', background: 'rgba(245,158,11,0.06)',
                  color: '#f59e0b', fontSize: 8, fontWeight: 800, cursor: backfilling ? 'wait' : 'pointer',
                  letterSpacing: '0.04em', textTransform: 'uppercase',
                }}>
                  {backfilling ? 'BACKFILLING...' : `BACKFILL ${cal.missingPos} PICKS MISSING POSITIONS`}
                </button>
              )}

              {/* By Position */}
              <CalRow title="BY POSITION" data={cal.byPosition} />

              {/* By League */}
              <CalRow title="BY LEAGUE" data={cal.byLeague} />

              {/* By Venue */}
              <CalRow title="BY VENUE" data={cal.byVenue} fmtLabel={v => v.toUpperCase()} />

              {/* By Game Type */}
              <CalRow title="BY GAME TYPE" data={cal.byGameType} />

              {/* By Match Result */}
              <CalRow title="BY MATCH RESULT" data={cal.byMatchResult} />

              {/* By Rec */}
              <CalRow title="BY DIRECTION" data={cal.byRec} fmtLabel={v => v.toUpperCase()} />

              {/* Prop + Rec combos */}
              <CalRow title="PROP + DIRECTION (FLIP CANDIDATES)" data={cal.propRec} showErr />
            </div>
          )}
        </div>
      )}

      {/* Filters */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 6, flexWrap: 'wrap' }}>
        <FilterPill label="All" active={filterResult === 'all'} onClick={() => setFilterResult('all')} />
        <FilterPill label="Hits" active={filterResult === 'hit'} onClick={() => setFilterResult('hit')} color="#10b981" />
        <FilterPill label="Misses" active={filterResult === 'miss'} onClick={() => setFilterResult('miss')} color="#f43f5e" />
        <FilterPill label="Push" active={filterResult === 'push'} onClick={() => setFilterResult('push')} color="#f59e0b" />
        <div style={{ width: 1, background: 'rgba(255,255,255,0.08)', margin: '2px 2px' }} />
        <FilterPill label="Over" active={filterRec === 'over'} onClick={() => setFilterRec(filterRec === 'over' ? 'all' : 'over')} color="#10b981" />
        <FilterPill label="Under" active={filterRec === 'under'} onClick={() => setFilterRec(filterRec === 'under' ? 'all' : 'under')} color="#818cf8" />
      </div>

      <div style={{ fontSize: 8, color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600 }}>
        {filtered.length} of {rows.length} picks  |  Tap header to sort
      </div>

      {/* Spreadsheet */}
      <div data-testid="intel-sheet" style={{
        overflowX: 'auto', overflowY: 'auto',
        borderRadius: 10, border: '1px solid rgba(255,255,255,0.08)',
        maxHeight: 'calc(100vh - 360px)',
        WebkitOverflowScrolling: 'touch',
      }}>
        <table style={{ borderCollapse: 'collapse', width: 'max-content', minWidth: '100%', fontFamily: "'JetBrains Mono', monospace", fontSize: 9 }}>
          <thead>
            <tr>
              {COLS.map(col => (
                <th key={col.key} data-testid={`col-${col.key}`} onClick={() => toggleSort(col.key)} style={{
                  position: 'sticky', top: 0, zIndex: col.key === 'player' ? 3 : 2,
                  ...(col.key === 'player' ? { left: 0, zIndex: 3 } : {}),
                  background: '#0d0d14', padding: '7px 6px', textAlign: 'left',
                  fontWeight: 900, fontSize: 7, letterSpacing: '0.06em', textTransform: 'uppercase',
                  color: sortCol === col.key ? 'var(--accent)' : 'rgba(255,255,255,0.45)',
                  borderBottom: '2px solid rgba(16,185,129,0.15)',
                  cursor: 'pointer', whiteSpace: 'nowrap', userSelect: 'none', minWidth: col.w,
                }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                    {col.label}
                    {sortCol === col.key ? (sortDir === 'asc' ? <ArrowUp style={{ width: 8, height: 8 }} /> : <ArrowDown style={{ width: 8, height: 8 }} />) : <ArrowUpDown style={{ width: 7, height: 7, opacity: 0.3 }} />}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((row, i) => (
              <tr key={i} data-testid={`row-${i}`} style={{
                background: i % 2 === 0 ? 'rgba(255,255,255,0.01)' : 'transparent',
                borderBottom: '1px solid rgba(255,255,255,0.03)',
              }}>
                {COLS.map(col => {
                  const raw = row[col.key];
                  const display = col.fmt ? col.fmt(raw) : raw;
                  const clr = typeof col.color === 'function' ? col.color(raw) : '#fff';
                  return (
                    <td key={col.key} style={{
                      padding: '6px 6px', whiteSpace: 'nowrap',
                      color: clr, fontWeight: col.key === 'result' || col.key === 'error' ? 800 : 600,
                      ...(col.key === 'player' ? { position: 'sticky', left: 0, zIndex: 1, background: i % 2 === 0 ? '#0b0b12' : '#0a0a0f', fontWeight: 800 } : {}),
                      textTransform: col.key === 'result' || col.key === 'rec' || col.key === 'venue' ? 'uppercase' : 'none',
                    }}>
                      {display ?? '-'}
                    </td>
                  );
                })}
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={COLS.length} style={{ textAlign: 'center', padding: 30, color: 'var(--text-muted)', fontSize: 11 }}>No picks match filters</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* Calibration Row — compact horizontal bar for each dimension */
function CalRow({ title, data, showErr, fmtLabel }) {
  if (!data || data.length === 0) return null;
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 7, fontWeight: 900, color: 'var(--accent)', letterSpacing: '0.06em', marginBottom: 3, opacity: 0.7 }}>{title}</div>
      {data.map((d, i) => {
        const label = fmtLabel ? fmtLabel(d.label) : d.label;
        const rateColor = d.rate >= 65 ? '#10b981' : d.rate >= 50 ? '#f59e0b' : '#f43f5e';
        const isWeak = d.rate < 50 && d.total >= 10;
        return (
          <div key={i} data-testid={`cal-${title.toLowerCase().replace(/\s+/g, '-')}-${i}`} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '4px 8px', borderRadius: 6, marginBottom: 2,
            background: isWeak ? 'rgba(244,63,94,0.04)' : 'rgba(255,255,255,0.015)',
            border: isWeak ? '1px solid rgba(244,63,94,0.1)' : '1px solid rgba(255,255,255,0.03)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 0 }}>
              <span style={{ fontSize: 9, fontWeight: 700, color: '#fff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 160 }}>{label}</span>
              {isWeak && <span style={{ fontSize: 6, fontWeight: 900, padding: '1px 4px', borderRadius: 3, background: 'rgba(244,63,94,0.15)', color: '#f43f5e', whiteSpace: 'nowrap' }}>WEAK</span>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
              {showErr && d.avgErr !== 0 && (
                <span style={{ fontSize: 8, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace", color: d.avgErr < 0 ? '#f43f5e' : '#10b981' }}>
                  {d.avgErr > 0 ? '+' : ''}{d.avgErr}
                </span>
              )}
              <span style={{ fontSize: 11, fontWeight: 900, color: rateColor, fontFamily: "'JetBrains Mono', monospace" }}>{d.rate}%</span>
              <span style={{ fontSize: 7, color: 'var(--text-muted)', fontWeight: 600 }}>({d.hits}/{d.total})</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function StatBox({ label, value, color, sub }) {
  return (
    <div style={{ flex: 1, padding: '8px 0', borderRadius: 8, textAlign: 'center', background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)' }}>
      <div style={{ fontSize: 7, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 900, color }}>{value}</div>
      {sub && <div style={{ fontSize: 7, color: 'var(--text-muted)' }}>{sub}</div>}
    </div>
  );
}

function FilterPill({ label, active, onClick, color }) {
  return (
    <button onClick={onClick} data-testid={`filter-${label.toLowerCase()}`} style={{
      padding: '4px 10px', borderRadius: 6, fontSize: 8, fontWeight: 800,
      letterSpacing: '0.04em', cursor: 'pointer', textTransform: 'uppercase',
      background: active ? (color || 'var(--accent)') + '18' : 'rgba(255,255,255,0.03)',
      color: active ? (color || 'var(--accent)') : 'rgba(255,255,255,0.35)',
      border: `1px solid ${active ? (color || 'var(--accent)') + '40' : 'rgba(255,255,255,0.06)'}`,
    }}>{label}</button>
  );
}

export default IntelTab;
