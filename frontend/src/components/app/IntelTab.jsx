import React, { useState, useEffect, useMemo } from 'react';
import { Loader2, ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react';
import { getIntelSheet } from '../../api';
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

  useEffect(() => { fetchData(sport); }, [sport]);

  function toggleSort(col) {
    if (sortCol === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(col);
      setSortDir('asc');
    }
  }

  const filtered = useMemo(() => {
    let r = rows;
    if (filterResult !== 'all') r = r.filter(x => x.result === filterResult);
    if (filterRec !== 'all') r = r.filter(x => x.rec === filterRec);
    if (sortCol) {
      r = [...r].sort((a, b) => {
        let va = a[sortCol], vb = b[sortCol];
        if (typeof va === 'number' && typeof vb === 'number') {
          return sortDir === 'asc' ? va - vb : vb - va;
        }
        va = String(va || '').toLowerCase();
        vb = String(vb || '').toLowerCase();
        return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
      });
    }
    return r;
  }, [rows, sortCol, sortDir, filterResult, filterRec]);

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
    { key: 'opponent', label: 'Opponent', w: 90 },
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
      <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
        <StatBox label="Rate" value={`${meta.rate}%`} color={meta.rate >= 65 ? 'var(--accent)' : meta.rate >= 50 ? '#f59e0b' : '#f43f5e'} sub={`${meta.total} picks`} />
        <StatBox label="Hits" value={meta.hits} color="var(--accent)" />
        <StatBox label="Misses" value={meta.misses} color="#f43f5e" />
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        <FilterPill label="All" active={filterResult === 'all'} onClick={() => setFilterResult('all')} />
        <FilterPill label="Hits" active={filterResult === 'hit'} onClick={() => setFilterResult('hit')} color="#10b981" />
        <FilterPill label="Misses" active={filterResult === 'miss'} onClick={() => setFilterResult('miss')} color="#f43f5e" />
        <FilterPill label="Push" active={filterResult === 'push'} onClick={() => setFilterResult('push')} color="#f59e0b" />
        <div style={{ width: 1, background: 'rgba(255,255,255,0.08)', margin: '2px 2px' }} />
        <FilterPill label="Over" active={filterRec === 'over'} onClick={() => setFilterRec(filterRec === 'over' ? 'all' : 'over')} color="#10b981" />
        <FilterPill label="Under" active={filterRec === 'under'} onClick={() => setFilterRec(filterRec === 'under' ? 'all' : 'under')} color="#818cf8" />
      </div>

      <div style={{ fontSize: 8, color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600 }}>
        {filtered.length} of {rows.length} picks shown  |  Tap column header to sort
      </div>

      {/* Spreadsheet Table */}
      <div data-testid="intel-sheet" style={{
        overflowX: 'auto', overflowY: 'auto',
        borderRadius: 10, border: '1px solid rgba(255,255,255,0.08)',
        maxHeight: 'calc(100vh - 340px)',
        WebkitOverflowScrolling: 'touch',
      }}>
        <table style={{
          borderCollapse: 'collapse', width: 'max-content', minWidth: '100%',
          fontFamily: "'JetBrains Mono', monospace", fontSize: 9,
        }}>
          <thead>
            <tr>
              {COLS.map(col => (
                <th key={col.key}
                  data-testid={`col-header-${col.key}`}
                  onClick={() => toggleSort(col.key)}
                  style={{
                    position: 'sticky', top: 0, zIndex: col.key === 'player' ? 3 : 2,
                    ...(col.key === 'player' ? { position: 'sticky', left: 0, zIndex: 3 } : {}),
                    background: '#0d0d14', padding: '7px 6px', textAlign: 'left',
                    fontWeight: 900, fontSize: 7, letterSpacing: '0.06em', textTransform: 'uppercase',
                    color: sortCol === col.key ? 'var(--accent)' : 'rgba(255,255,255,0.45)',
                    borderBottom: '2px solid rgba(16,185,129,0.15)',
                    cursor: 'pointer', whiteSpace: 'nowrap', userSelect: 'none',
                    minWidth: col.w,
                  }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                    {col.label}
                    {sortCol === col.key ? (
                      sortDir === 'asc' ? <ArrowUp style={{ width: 8, height: 8 }} /> : <ArrowDown style={{ width: 8, height: 8 }} />
                    ) : (
                      <ArrowUpDown style={{ width: 7, height: 7, opacity: 0.3 }} />
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((row, i) => (
              <tr key={i} data-testid={`sheet-row-${i}`} style={{
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

function StatBox({ label, value, color, sub }) {
  return (
    <div style={{
      flex: 1, padding: '8px 0', borderRadius: 8, textAlign: 'center',
      background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
    }}>
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
