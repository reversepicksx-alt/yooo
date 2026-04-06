import React, { useState, useEffect, useMemo } from 'react';
import { Loader2, ArrowUpDown, ArrowUp, ArrowDown, Search, X } from 'lucide-react';
import { getIntelSheet } from '../../api';
import { toast } from 'sonner';

export function IntelTab({ auth }) {
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState({});
  const [loading, setLoading] = useState(true);
  const [sport, setSport] = useState('soccer');
  const [sortCol, setSortCol] = useState('');
  const [sortDir, setSortDir] = useState('asc');

  // Smart filters
  const [fResult, setFResult] = useState('all');
  const [fRec, setFRec] = useState('all');
  const [fPosition, setFPosition] = useState('all');
  const [fLeague, setFLeague] = useState('all');
  const [fVenue, setFVenue] = useState('all');
  const [fGameType, setFGameType] = useState('all');
  const [fProp, setFProp] = useState('all');
  const [fOpponent, setFOpponent] = useState('');

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
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortCol(col); setSortDir('asc'); }
  }

  // Extract unique values for filter dropdowns
  const opts = useMemo(() => {
    const u = (k) => [...new Set(rows.map(r => r[k]).filter(Boolean))].sort();
    // Hardcode positions so filter always works even before backfill
    const soccerPositions = ['GK','CB','LB','RB','LWB','RWB','CDM','CM','CAM','LM','RM','LW','RW','CF','ST','DEF','MID','FWD'];
    const basketballPositions = ['PG','SG','SF','PF','C','Guard','Forward','Center'];
    const dataPositions = u('position');
    const hardcoded = sport === 'basketball' ? basketballPositions : soccerPositions;
    const merged = [...new Set([...hardcoded, ...dataPositions])].sort();
    return {
      positions: merged,
      leagues: u('league'),
      venues: u('venue'),
      gameTypes: u('gameType'),
      props: u('prop'),
    };
  }, [rows, sport]);

  // Apply all filters + sorting
  const filtered = useMemo(() => {
    let r = rows;
    if (fResult !== 'all') r = r.filter(x => x.result === fResult);
    if (fRec !== 'all') r = r.filter(x => x.rec === fRec);
    if (fPosition !== 'all') r = r.filter(x => x.position === fPosition);
    if (fLeague !== 'all') r = r.filter(x => x.league === fLeague);
    if (fVenue !== 'all') r = r.filter(x => x.venue === fVenue);
    if (fGameType !== 'all') r = r.filter(x => x.gameType === fGameType);
    if (fProp !== 'all') r = r.filter(x => x.prop === fProp);
    if (fOpponent) r = r.filter(x => (x.opponent || '').toLowerCase().includes(fOpponent.toLowerCase()));
    if (sortCol) {
      r = [...r].sort((a, b) => {
        let va = a[sortCol], vb = b[sortCol];
        if (typeof va === 'number' && typeof vb === 'number') return sortDir === 'asc' ? va - vb : vb - va;
        va = String(va || '').toLowerCase(); vb = String(vb || '').toLowerCase();
        return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
      });
    }
    return r;
  }, [rows, sortCol, sortDir, fResult, fRec, fPosition, fLeague, fVenue, fGameType, fProp, fOpponent]);

  // Filtered stats
  const fStats = useMemo(() => {
    const h = filtered.filter(r => r.result === 'hit').length;
    const m = filtered.filter(r => r.result === 'miss').length;
    const t = h + m;
    return { hits: h, misses: m, total: t, rate: t > 0 ? Math.round(h / t * 1000) / 10 : 0 };
  }, [filtered]);

  const hasFilters = fResult !== 'all' || fRec !== 'all' || fPosition !== 'all' || fLeague !== 'all' || fVenue !== 'all' || fGameType !== 'all' || fProp !== 'all' || fOpponent;

  function clearAll() {
    setFResult('all'); setFRec('all'); setFPosition('all'); setFLeague('all');
    setFVenue('all'); setFGameType('all'); setFProp('all'); setFOpponent('');
  }

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
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
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

      {/* Overall Stats */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        <StatBox label="Rate" value={`${meta.rate}%`} color={meta.rate >= 65 ? 'var(--accent)' : meta.rate >= 50 ? '#f59e0b' : '#f43f5e'} sub={`${meta.total} total`} />
        <StatBox label="Hits" value={meta.hits} color="var(--accent)" />
        <StatBox label="Miss" value={meta.misses} color="#f43f5e" />
      </div>

      {/* Smart Filters */}
      <div data-testid="smart-filters" style={{
        padding: '8px', borderRadius: 10,
        background: 'rgba(255,255,255,0.015)', border: '1px solid rgba(255,255,255,0.06)',
        marginBottom: 8,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <Search style={{ width: 10, height: 10, color: 'var(--accent)' }} />
            <span style={{ fontSize: 8, fontWeight: 800, color: 'var(--accent)', letterSpacing: '0.06em' }}>SEARCH & FILTER</span>
          </div>
          {hasFilters && (
            <button onClick={clearAll} data-testid="clear-filters" style={{
              display: 'flex', alignItems: 'center', gap: 2, padding: '2px 6px', borderRadius: 4,
              background: 'rgba(244,63,94,0.1)', border: '1px solid rgba(244,63,94,0.2)',
              color: '#f43f5e', fontSize: 7, fontWeight: 800, cursor: 'pointer',
            }}><X style={{ width: 8, height: 8 }} /> CLEAR</button>
          )}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 4, marginBottom: 4 }}>
          <FilterSelect label="Position" value={fPosition} onChange={setFPosition} options={opts.positions} testId="filter-position" />
          <FilterSelect label="League" value={fLeague} onChange={setFLeague} options={opts.leagues} testId="filter-league" />
          <FilterSelect label="Venue" value={fVenue} onChange={setFVenue} options={opts.venues} fmtOpt={v => v.toUpperCase()} testId="filter-venue" />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 4, marginBottom: 4 }}>
          <FilterSelect label="Game Type" value={fGameType} onChange={setFGameType} options={opts.gameTypes} testId="filter-gametype" />
          <FilterSelect label="Prop" value={fProp} onChange={setFProp} options={opts.props} fmtOpt={v => v.replace(/_/g, ' ')} testId="filter-prop" />
          <FilterSelect label="Direction" value={fRec} onChange={setFRec} options={['over', 'under']} fmtOpt={v => v.toUpperCase()} testId="filter-rec" />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
          <FilterSelect label="Result" value={fResult} onChange={setFResult} options={['hit', 'miss', 'push']} fmtOpt={v => v.toUpperCase()} testId="filter-result" />
          <div>
            <div style={{ fontSize: 7, fontWeight: 700, color: 'var(--text-muted)', marginBottom: 2, textTransform: 'uppercase' }}>Opponent</div>
            <input
              type="text" placeholder="Search..." value={fOpponent} onChange={e => setFOpponent(e.target.value)}
              data-testid="filter-opponent"
              style={{
                width: '100%', padding: '4px 6px', borderRadius: 4, fontSize: 9, fontWeight: 700,
                background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)',
                color: '#fff', outline: 'none', boxSizing: 'border-box',
              }}
            />
          </div>
        </div>
      </div>

      {/* Filtered Stats (when filters active) */}
      {hasFilters && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <StatBox label="Filtered Rate" value={`${fStats.rate}%`} color={fStats.rate >= 65 ? 'var(--accent)' : fStats.rate >= 50 ? '#f59e0b' : '#f43f5e'} sub={`${fStats.total} picks`} />
          <StatBox label="Hits" value={fStats.hits} color="var(--accent)" />
          <StatBox label="Miss" value={fStats.misses} color="#f43f5e" />
        </div>
      )}

      <div style={{ fontSize: 8, color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600 }}>
        {filtered.length} of {rows.length} picks  |  Tap header to sort
      </div>

      {/* Spreadsheet */}
      <div data-testid="intel-sheet" style={{
        overflowX: 'auto', overflowY: 'auto',
        borderRadius: 10, border: '1px solid rgba(255,255,255,0.08)',
        maxHeight: 'calc(100vh - 420px)',
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

function FilterSelect({ label, value, onChange, options, fmtOpt, testId }) {
  return (
    <div>
      <div style={{ fontSize: 7, fontWeight: 700, color: 'var(--text-muted)', marginBottom: 2, textTransform: 'uppercase' }}>{label}</div>
      <select
        value={value} onChange={e => onChange(e.target.value)}
        data-testid={testId}
        style={{
          width: '100%', padding: '4px 4px', borderRadius: 4, fontSize: 9, fontWeight: 700,
          background: value !== 'all' ? 'rgba(16,185,129,0.08)' : 'rgba(255,255,255,0.04)',
          border: value !== 'all' ? '1px solid rgba(16,185,129,0.25)' : '1px solid rgba(255,255,255,0.1)',
          color: value !== 'all' ? 'var(--accent)' : '#fff',
          outline: 'none', appearance: 'auto', cursor: 'pointer',
        }}
      >
        <option value="all">All</option>
        {options.map(o => (
          <option key={o} value={o}>{fmtOpt ? fmtOpt(o) : o}</option>
        ))}
      </select>
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

export default IntelTab;
