import React, { useState, useMemo, useCallback } from 'react';
import {
  View, Text, StyleSheet, ScrollView,
  TouchableOpacity, ActivityIndicator,
  RefreshControl, Modal,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useQuery } from '@tanstack/react-query';
import Colors from '@/constants/colors';
import {
  getTopPropsTable,
  PlayerPickRow,
  BandSummaryRow,
  OverallBandRow,
} from '@/lib/api';

// ─── constants ────────────────────────────────────────────────────────────────
const BAND_COLORS: Record<string, string> = {
  aligned:  '#39FF14',
  mild:     '#B5FF14',
  moderate: '#FFCC00',
  elevated: '#FF8C00',
  extreme:  '#FF3B30',
  '—':      '#555',
};

const BAND_LABELS: Record<string, string> = {
  aligned:  'ALIGNED',
  mild:     'MILD',
  moderate: 'MODERATE',
  elevated: 'ELEVATED',
  extreme:  'EXTREME',
  '—':      '—',
};

const PROP_SHORT: Record<string, string> = {
  pass_attempts: 'PassAtt',
  saves:         'Saves',
  shots:         'Shots',
  shots_on_target: 'SOT',
  goals:         'Goals',
  assists:       'Ast',
  key_passes:    'KeyPass',
  tackles:       'Tackles',
  dribbles:      'Dribs',
  clearances:    'Clears',
  crosses:       'Crosses',
};

function hitColor(pct: number) {
  if (pct >= 65) return Colors.primary;
  if (pct >= 55) return '#FFCC00';
  return '#FF3B30';
}

function bandColor(band: string) {
  return BAND_COLORS[band] ?? '#555';
}

// ─── small helpers ─────────────────────────────────────────────────────────
function BandPill({ band }: { band: string }) {
  const c = bandColor(band);
  return (
    <View style={{ borderRadius: 4, paddingHorizontal: 5, paddingVertical: 2,
        backgroundColor: c + '22', borderWidth: 1, borderColor: c + '66' }}>
      <Text style={{ fontSize: 9, fontWeight: '700', color: c, letterSpacing: 0.4 }}>
        {BAND_LABELS[band] ?? band.toUpperCase()}
      </Text>
    </View>
  );
}

function HitBadge({ pct }: { pct: number }) {
  const c = hitColor(pct);
  return (
    <View style={{ borderRadius: 5, borderWidth: 1, paddingHorizontal: 5, paddingVertical: 2,
        borderColor: c + '55', backgroundColor: c + '18', minWidth: 40, alignItems: 'center' }}>
      <Text style={{ fontSize: 11, fontWeight: '700', color: c }}>
        {pct.toFixed(0)}%
      </Text>
    </View>
  );
}

// ─── dropdown ─────────────────────────────────────────────────────────────────
function Dropdown({
  visible, title, options, selected, onSelect, onClose,
}: {
  visible: boolean; title: string;
  options: { label: string; value: string }[];
  selected: string; onSelect: (v: string) => void; onClose: () => void;
}) {
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <TouchableOpacity style={drop.overlay} activeOpacity={1} onPress={onClose}>
        <View style={drop.box}>
          <Text style={drop.title}>{title}</Text>
          <ScrollView style={{ maxHeight: 340 }} showsVerticalScrollIndicator={false}>
            {options.map(opt => (
              <TouchableOpacity
                key={opt.value}
                style={[drop.option, selected === opt.value && drop.optionActive]}
                onPress={() => { onSelect(opt.value); onClose(); }}
              >
                <Text style={[drop.optText, selected === opt.value && drop.optTextActive]}>
                  {opt.label}
                </Text>
                {selected === opt.value && (
                  <Ionicons name="checkmark" size={14} color={Colors.primary} />
                )}
              </TouchableOpacity>
            ))}
          </ScrollView>
        </View>
      </TouchableOpacity>
    </Modal>
  );
}

// ─── Overall band summary cards ──────────────────────────────────────────────
function BandCards({ rows }: { rows: OverallBandRow[] }) {
  const byBand: Record<string, { under?: OverallBandRow; over?: OverallBandRow }> = {};
  rows.forEach(r => {
    if (!byBand[r.band]) byBand[r.band] = {};
    if (r.direction === 'UNDER') byBand[r.band].under = r;
    else byBand[r.band].over = r;
  });

  const BAND_ORDER = ['aligned', 'mild', 'moderate', 'elevated', 'extreme'];
  const cards = BAND_ORDER.filter(b => byBand[b]);

  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false}
      style={{ marginBottom: 12 }} contentContainerStyle={{ paddingHorizontal: 16, gap: 8 }}>
      {cards.map(band => {
        const u = byBand[band]?.under;
        const o = byBand[band]?.over;
        const c = bandColor(band);
        return (
          <View key={band} style={[card.wrap, { borderColor: c + '44' }]}>
            <View style={[card.header, { backgroundColor: c + '18' }]}>
              <Text style={[card.band, { color: c }]}>{BAND_LABELS[band]}</Text>
              <Text style={card.devRange}>{
                band === 'aligned' ? '0–5%' :
                band === 'mild'    ? '5–10%' :
                band === 'moderate'? '10–15%' :
                band === 'elevated'? '15–20%' : '20%+'
              }</Text>
            </View>
            <View style={card.body}>
              {u && (
                <View style={card.row}>
                  <Text style={card.dir}>U</Text>
                  <Text style={[card.pct, { color: hitColor(u.hitPct) }]}>
                    {u.hitPct.toFixed(0)}%
                  </Text>
                  <Text style={card.n}>{u.total}</Text>
                </View>
              )}
              {o && (
                <View style={card.row}>
                  <Text style={[card.dir, { color: Colors.primary }]}>O</Text>
                  <Text style={[card.pct, { color: hitColor(o.hitPct) }]}>
                    {o.hitPct.toFixed(0)}%
                  </Text>
                  <Text style={card.n}>{o.total}</Text>
                </View>
              )}
            </View>
          </View>
        );
      })}
    </ScrollView>
  );
}

// ─── Band Stats table (aggregated) ───────────────────────────────────────────
type BandSortKey = keyof BandSummaryRow;
const BAND_COLS = [
  { key: 'band' as BandSortKey,         label: 'Band',    width: 80,  align: 'left'   as const },
  { key: 'propType' as BandSortKey,     label: 'Prop',    width: 68,  align: 'left'   as const },
  { key: 'direction' as BandSortKey,    label: 'Dir',     width: 42,  align: 'center' as const },
  { key: 'position' as BandSortKey,     label: 'Pos',     width: 76,  align: 'left'   as const },
  { key: 'venue' as BandSortKey,        label: 'Venue',   width: 52,  align: 'center' as const },
  { key: 'hitPct' as BandSortKey,       label: 'Hit%',    width: 58,  align: 'center' as const },
  { key: 'hits' as BandSortKey,         label: 'H',       width: 30,  align: 'center' as const },
  { key: 'misses' as BandSortKey,       label: 'M',       width: 30,  align: 'center' as const },
  { key: 'total' as BandSortKey,        label: 'Bets',    width: 38,  align: 'center' as const },
  { key: 'uniquePlayers' as BandSortKey,label: 'Plyrs',   width: 42,  align: 'center' as const },
  { key: 'avgLine' as BandSortKey,      label: 'AvgLine', width: 60,  align: 'center' as const },
];

function BandStatsTable({ rows }: { rows: BandSummaryRow[] }) {
  const [sortKey, setSortKey] = useState<BandSortKey>('bandOrder');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [bandFilter, setBandFilter] = useState('All');
  const [posFilter,  setPosFilter]  = useState('All');
  const [dirFilter,  setDirFilter]  = useState('All');
  const [venueFilter, setVenueFilter] = useState('All');

  const [showBand,  setShowBand]  = useState(false);
  const [showPos,   setShowPos]   = useState(false);
  const [showDir,   setShowDir]   = useState(false);
  const [showVenue, setShowVenue] = useState(false);

  const bands     = useMemo(() => ['All', ...Array.from(new Set(rows.map(r => r.band))).sort()], [rows]);
  const positions = useMemo(() => ['All', ...Array.from(new Set(rows.map(r => r.position))).filter(Boolean).sort()], [rows]);

  const filtered = useMemo(() => {
    let arr = [...rows];
    if (bandFilter  !== 'All') arr = arr.filter(r => r.band === bandFilter);
    if (posFilter   !== 'All') arr = arr.filter(r => r.position === posFilter);
    if (dirFilter   !== 'All') arr = arr.filter(r => r.direction.toUpperCase() === dirFilter);
    if (venueFilter !== 'All') arr = arr.filter(r => r.venue.toLowerCase() === venueFilter.toLowerCase());
    arr.sort((a, b) => {
      const av = a[sortKey] ?? '';
      const bv = b[sortKey] ?? '';
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === 'desc' ? -cmp : cmp;
    });
    return arr;
  }, [rows, bandFilter, posFilter, dirFilter, venueFilter, sortKey, sortDir]);

  const handleSort = useCallback((key: BandSortKey) => {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    else { setSortKey(key); setSortDir('desc'); }
  }, [sortKey]);

  return (
    <View style={{ flex: 1 }}>
      {/* Filters row */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
        style={styles.filterBar} contentContainerStyle={styles.filterBarInner}>
        {([
          { label: 'Band', value: bandFilter, opts: bands.map(b => ({ label: b === 'All' ? 'All Bands' : BAND_LABELS[b] ?? b, value: b })), onPress: () => setShowBand(true) },
          { label: 'Position', value: posFilter, opts: positions.map(p => ({ label: p, value: p })), onPress: () => setShowPos(true) },
          { label: 'Direction', value: dirFilter, opts: ['All','UNDER','OVER'].map(v => ({ label: v, value: v })), onPress: () => setShowDir(true) },
          { label: 'Venue', value: venueFilter, opts: ['All','Home','Away'].map(v => ({ label: v, value: v })), onPress: () => setShowVenue(true) },
        ] as { label: string; value: string; opts: { label: string; value: string }[]; onPress: () => void }[]).map(f => (
          <TouchableOpacity key={f.label} style={styles.filterChip} onPress={f.onPress}>
            <Text style={styles.filterChipLabel}>{f.label}</Text>
            <Text style={styles.filterChipVal} numberOfLines={1}>
              {f.value === 'All' ? 'All' : (BAND_LABELS[f.value] ?? f.value)}
            </Text>
            <Ionicons name="chevron-down" size={11} color={Colors.textTertiary} style={{ marginLeft: 3 }} />
          </TouchableOpacity>
        ))}
      </ScrollView>

      {/* Table */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: 16 }}>
        <View>
          <View style={styles.thead}>
            {BAND_COLS.map(col => (
              <TouchableOpacity key={col.key} style={[styles.th, { width: col.width }]}
                onPress={() => handleSort(col.key)} activeOpacity={0.7}>
                <Text style={[styles.thText, { textAlign: col.align }]}>
                  {col.label}{sortKey === col.key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
          {filtered.length === 0 ? (
            <View style={styles.emptyRow}><Text style={styles.emptyText}>No data matches filters.</Text></View>
          ) : filtered.map((row, idx) => (
            <View key={idx} style={[styles.trow, idx % 2 === 1 && styles.trowAlt]}>
              {BAND_COLS.map(col => {
                const raw = row[col.key];
                if (col.key === 'band') return (
                  <View key={col.key} style={[styles.td, { width: col.width }]}>
                    <BandPill band={String(raw ?? '—')} />
                  </View>
                );
                if (col.key === 'hitPct') return (
                  <View key={col.key} style={[styles.td, { width: col.width, alignItems: 'center' }]}>
                    <HitBadge pct={raw as number} />
                  </View>
                );
                if (col.key === 'direction') {
                  const isOver = String(raw).toUpperCase() === 'OVER';
                  return (
                    <View key={col.key} style={[styles.td, { width: col.width, alignItems: 'center' }]}>
                      <Text style={[styles.dirText, { color: isOver ? Colors.primary : Colors.textSecondary }]}>
                        {String(raw ?? '—')}
                      </Text>
                    </View>
                  );
                }
                if (col.key === 'propType') return (
                  <View key={col.key} style={[styles.td, { width: col.width }]}>
                    <Text style={styles.tdBold} numberOfLines={1}>
                      {PROP_SHORT[raw as string] ?? String(raw ?? '—')}
                    </Text>
                  </View>
                );
                return (
                  <View key={col.key} style={[styles.td, { width: col.width }]}>
                    <Text style={[styles.tdText, { textAlign: col.align }]} numberOfLines={1}>
                      {raw != null ? (typeof raw === 'number' && col.key === 'avgLine' ? (raw as number).toFixed(1) : String(raw)) : '—'}
                    </Text>
                  </View>
                );
              })}
            </View>
          ))}
        </View>
      </ScrollView>
      <Text style={styles.footer}>{filtered.length} groups · {rows.reduce((s, r) => s + r.total, 0)} total picks</Text>

      <Dropdown visible={showBand}  title="Filter by Band"  options={bands.map(b => ({ label: b === 'All' ? 'All Bands' : (BAND_LABELS[b] ?? b), value: b }))}          selected={bandFilter}  onSelect={setBandFilter}  onClose={() => setShowBand(false)}  />
      <Dropdown visible={showPos}   title="Filter by Position" options={positions.map(p => ({ label: p, value: p }))}                                                    selected={posFilter}   onSelect={setPosFilter}   onClose={() => setShowPos(false)}   />
      <Dropdown visible={showDir}   title="Filter by Direction" options={['All','UNDER','OVER'].map(v => ({ label: v, value: v }))}                                       selected={dirFilter}   onSelect={setDirFilter}   onClose={() => setShowDir(false)}   />
      <Dropdown visible={showVenue} title="Filter by Venue"  options={['All','Home','Away'].map(v => ({ label: v, value: v }))}                                           selected={venueFilter} onSelect={setVenueFilter} onClose={() => setShowVenue(false)} />
    </View>
  );
}

// ─── Player Picks table (individual) ─────────────────────────────────────────
type PickSortKey = keyof PlayerPickRow;
const PICK_COLS = [
  { key: 'playerName'   as PickSortKey, label: 'Player',  width: 112, align: 'left'   as const },
  { key: 'position'     as PickSortKey, label: 'Pos',     width: 72,  align: 'left'   as const },
  { key: 'propType'     as PickSortKey, label: 'Prop',    width: 60,  align: 'left'   as const },
  { key: 'direction'    as PickSortKey, label: 'Dir',     width: 42,  align: 'center' as const },
  { key: 'line'         as PickSortKey, label: 'Line',    width: 48,  align: 'center' as const },
  { key: 'projection'   as PickSortKey, label: 'Proj',    width: 44,  align: 'center' as const },
  { key: 'deviationPct' as PickSortKey, label: 'Dev%',    width: 44,  align: 'center' as const },
  { key: 'band'         as PickSortKey, label: 'Band',    width: 80,  align: 'left'   as const },
  { key: 'venue'        as PickSortKey, label: 'Venue',   width: 50,  align: 'center' as const },
  { key: 'result'       as PickSortKey, label: 'Result',  width: 50,  align: 'center' as const },
  { key: 'actual'       as PickSortKey, label: 'Actual',  width: 46,  align: 'center' as const },
  { key: 'opponent'     as PickSortKey, label: 'Opp',     width: 90,  align: 'left'   as const },
];

function PlayerPicksTable({ rows }: { rows: PlayerPickRow[] }) {
  const [sortKey, setSortKey] = useState<PickSortKey>('bandOrder');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [bandFilter,  setBandFilter]  = useState('All');
  const [posFilter,   setPosFilter]   = useState('All');
  const [dirFilter,   setDirFilter]   = useState('All');
  const [venueFilter, setVenueFilter] = useState('All');
  const [resultFilter, setResultFilter] = useState('All');

  const [showBand,   setShowBand]   = useState(false);
  const [showPos,    setShowPos]    = useState(false);
  const [showDir,    setShowDir]    = useState(false);
  const [showVenue,  setShowVenue]  = useState(false);
  const [showResult, setShowResult] = useState(false);

  const positions = useMemo(() => ['All', ...Array.from(new Set(rows.map(r => r.position))).filter(Boolean).sort()], [rows]);

  const filtered = useMemo(() => {
    let arr = [...rows];
    if (bandFilter   !== 'All') arr = arr.filter(r => r.band === bandFilter);
    if (posFilter    !== 'All') arr = arr.filter(r => r.position === posFilter);
    if (dirFilter    !== 'All') arr = arr.filter(r => r.direction === dirFilter);
    if (venueFilter  !== 'All') arr = arr.filter(r => r.venue.toLowerCase() === venueFilter.toLowerCase());
    if (resultFilter !== 'All') arr = arr.filter(r => r.result === resultFilter);
    arr.sort((a, b) => {
      const av = a[sortKey] ?? '';
      const bv = b[sortKey] ?? '';
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === 'desc' ? -cmp : cmp;
    });
    return arr;
  }, [rows, bandFilter, posFilter, dirFilter, venueFilter, resultFilter, sortKey, sortDir]);

  const handleSort = useCallback((key: PickSortKey) => {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    else { setSortKey(key); setSortDir('desc'); }
  }, [sortKey]);

  const hitCount   = filtered.filter(r => r.result === 'HIT').length;
  const hitPct     = filtered.length > 0 ? Math.round(hitCount / filtered.length * 100) : 0;

  return (
    <View style={{ flex: 1 }}>
      {/* Filters */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
        style={styles.filterBar} contentContainerStyle={styles.filterBarInner}>
        {([
          { label: 'Band',  value: bandFilter,   opts: ['All','aligned','mild','moderate','elevated','extreme'].map(b => ({ label: b === 'All' ? 'All Bands' : (BAND_LABELS[b] ?? b), value: b })), onPress: () => setShowBand(true) },
          { label: 'Pos',   value: posFilter,    opts: positions.map(p => ({ label: p, value: p })),                                                                                                 onPress: () => setShowPos(true) },
          { label: 'Dir',   value: dirFilter,    opts: ['All','UNDER','OVER'].map(v => ({ label: v, value: v })),                                                                                    onPress: () => setShowDir(true) },
          { label: 'Venue', value: venueFilter,  opts: ['All','Home','Away'].map(v => ({ label: v, value: v })),                                                                                     onPress: () => setShowVenue(true) },
          { label: 'Result',value: resultFilter, opts: ['All','HIT','MISS'].map(v => ({ label: v, value: v })),                                                                                      onPress: () => setShowResult(true) },
        ] as { label: string; value: string; opts: { label: string; value: string }[]; onPress: () => void }[]).map(f => (
          <TouchableOpacity key={f.label} style={styles.filterChip} onPress={f.onPress}>
            <Text style={styles.filterChipLabel}>{f.label}</Text>
            <Text style={styles.filterChipVal} numberOfLines={1}>
              {f.value === 'All' ? 'All' : (BAND_LABELS[f.value] ?? f.value)}
            </Text>
            <Ionicons name="chevron-down" size={11} color={Colors.textTertiary} style={{ marginLeft: 3 }} />
          </TouchableOpacity>
        ))}
      </ScrollView>

      {/* Mini hit rate bar */}
      {filtered.length > 0 && (
        <View style={styles.miniStats}>
          <Text style={styles.miniStatsText}>
            <Text style={{ color: hitColor(hitPct), fontWeight: '700' }}>{hitPct}% hit rate</Text>
            {'  '}
            <Text style={{ color: Colors.primary }}>{hitCount} HIT</Text>
            {'  '}
            <Text style={{ color: '#FF3B30' }}>{filtered.length - hitCount} MISS</Text>
            {'  '}
            <Text style={{ color: Colors.textTertiary }}>{filtered.length} picks</Text>
          </Text>
        </View>
      )}

      {/* Table */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: 16 }}>
        <View>
          <View style={styles.thead}>
            {PICK_COLS.map(col => (
              <TouchableOpacity key={col.key} style={[styles.th, { width: col.width }]}
                onPress={() => handleSort(col.key)} activeOpacity={0.7}>
                <Text style={[styles.thText, { textAlign: col.align }]}>
                  {col.label}{sortKey === col.key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {filtered.length === 0 ? (
            <View style={styles.emptyRow}><Text style={styles.emptyText}>No picks match these filters.</Text></View>
          ) : filtered.map((row, idx) => (
            <View key={idx} style={[styles.trow, idx % 2 === 1 && styles.trowAlt]}>
              {PICK_COLS.map(col => {
                const raw = row[col.key];

                if (col.key === 'playerName') return (
                  <View key={col.key} style={[styles.td, { width: col.width }]}>
                    <Text style={styles.tdBold} numberOfLines={1}>{String(raw ?? '—')}</Text>
                    {row.teamName && row.teamName !== '—' && (
                      <Text style={styles.tdSub} numberOfLines={1}>{row.teamName}</Text>
                    )}
                  </View>
                );

                if (col.key === 'band') return (
                  <View key={col.key} style={[styles.td, { width: col.width }]}>
                    <BandPill band={String(raw ?? '—')} />
                  </View>
                );

                if (col.key === 'result') {
                  const isHit = String(raw) === 'HIT';
                  return (
                    <View key={col.key} style={[styles.td, { width: col.width, alignItems: 'center' }]}>
                      <View style={[styles.resultBadge,
                        { backgroundColor: (isHit ? '#39FF14' : '#FF3B30') + '20',
                          borderColor:      (isHit ? '#39FF14' : '#FF3B30') + '55' }]}>
                        <Text style={[styles.resultText, { color: isHit ? '#39FF14' : '#FF3B30' }]}>
                          {String(raw ?? '—')}
                        </Text>
                      </View>
                    </View>
                  );
                }

                if (col.key === 'direction') {
                  const isOver = String(raw) === 'OVER';
                  return (
                    <View key={col.key} style={[styles.td, { width: col.width, alignItems: 'center' }]}>
                      <Text style={[styles.dirText, { color: isOver ? Colors.primary : Colors.textSecondary }]}>
                        {String(raw ?? '—')}
                      </Text>
                    </View>
                  );
                }

                if (col.key === 'deviationPct') {
                  const dev = raw as number | null;
                  const bc  = dev != null ? bandColor(row.band) : Colors.textTertiary;
                  return (
                    <View key={col.key} style={[styles.td, { width: col.width, alignItems: 'center' }]}>
                      <Text style={{ fontSize: 11, fontWeight: '700', color: bc }}>
                        {dev != null ? `${dev.toFixed(0)}%` : '—'}
                      </Text>
                    </View>
                  );
                }

                if (col.key === 'propType') return (
                  <View key={col.key} style={[styles.td, { width: col.width }]}>
                    <Text style={styles.tdBold} numberOfLines={1}>
                      {PROP_SHORT[raw as string] ?? String(raw ?? '—')}
                    </Text>
                  </View>
                );

                if (col.key === 'line' || col.key === 'projection' || col.key === 'actual') {
                  const val = raw as number | null;
                  return (
                    <View key={col.key} style={[styles.td, { width: col.width, alignItems: 'center' }]}>
                      <Text style={styles.tdText}>{val != null ? val.toFixed(1) : '—'}</Text>
                    </View>
                  );
                }

                return (
                  <View key={col.key} style={[styles.td, { width: col.width }]}>
                    <Text style={[styles.tdText, { textAlign: col.align }]} numberOfLines={1}>
                      {raw != null ? String(raw) : '—'}
                    </Text>
                  </View>
                );
              })}
            </View>
          ))}
        </View>
      </ScrollView>

      <Text style={styles.footer}>{filtered.length} picks shown</Text>

      <Dropdown visible={showBand}   title="Filter by Band"      options={['All','aligned','mild','moderate','elevated','extreme'].map(b => ({ label: b === 'All' ? 'All Bands' : (BAND_LABELS[b] ?? b), value: b }))} selected={bandFilter}   onSelect={setBandFilter}   onClose={() => setShowBand(false)}   />
      <Dropdown visible={showPos}    title="Filter by Position"  options={positions.map(p => ({ label: p, value: p }))}                                                                                               selected={posFilter}    onSelect={setPosFilter}    onClose={() => setShowPos(false)}    />
      <Dropdown visible={showDir}    title="Filter by Direction" options={['All','UNDER','OVER'].map(v => ({ label: v, value: v }))}                                                                                   selected={dirFilter}    onSelect={setDirFilter}    onClose={() => setShowDir(false)}    />
      <Dropdown visible={showVenue}  title="Filter by Venue"     options={['All','Home','Away'].map(v => ({ label: v, value: v }))}                                                                                    selected={venueFilter}  onSelect={setVenueFilter}  onClose={() => setShowVenue(false)}  />
      <Dropdown visible={showResult} title="Filter by Result"    options={['All','HIT','MISS'].map(v => ({ label: v, value: v }))}                                                                                     selected={resultFilter} onSelect={setResultFilter} onClose={() => setShowResult(false)} />
    </View>
  );
}

// ─── Main screen ──────────────────────────────────────────────────────────────
export default function TopTableTab() {
  const insets = useSafeAreaInsets();
  const [view, setView] = useState<'bands' | 'picks'>('picks');

  const { data, isLoading, refetch, isRefetching } = useQuery({
    queryKey: ['topPropsTable'],
    queryFn: getTopPropsTable,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  const playerRows    = data?.playerRows    ?? [];
  const bandSummary   = data?.bandSummary   ?? [];
  const overallSummary = data?.overallSummary ?? [];
  const totalDeduped  = data?.totalDeduped  ?? 0;
  const totalRaw      = data?.totalRaw      ?? 0;

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.headerMeta}>
          <Ionicons name="lock-closed" size={11} color={Colors.primary} />
          <Text style={styles.headerMetaText}>PRIVATE</Text>
          {totalDeduped > 0 && (
            <Text style={styles.headerMetaText}>
              {'  ·  '}{totalDeduped} unique picks
              {totalRaw !== totalDeduped && ` (${totalRaw} total)`}
            </Text>
          )}
        </View>
        <Text style={styles.headerTitle}>Props Intelligence</Text>
        <Text style={styles.headerSub}>
          Line-deviation bands · all users · deduped by prediction event
        </Text>
      </View>

      {/* View toggle */}
      <View style={styles.toggle}>
        <TouchableOpacity
          style={[styles.toggleBtn, view === 'picks' && styles.toggleBtnActive]}
          onPress={() => setView('picks')}
        >
          <Text style={[styles.toggleText, view === 'picks' && styles.toggleTextActive]}>
            Player Picks
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.toggleBtn, view === 'bands' && styles.toggleBtnActive]}
          onPress={() => setView('bands')}
        >
          <Text style={[styles.toggleText, view === 'bands' && styles.toggleTextActive]}>
            Band Stats
          </Text>
        </TouchableOpacity>
      </View>

      {isLoading ? (
        <ActivityIndicator color={Colors.primary} style={{ marginTop: 60 }} />
      ) : (
        <ScrollView
          style={{ flex: 1 }}
          refreshControl={
            <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={Colors.primary} />
          }
          showsVerticalScrollIndicator={false}
        >
          {/* Band summary cards — always visible */}
          {overallSummary.length > 0 && <BandCards rows={overallSummary} />}

          {view === 'picks'
            ? <PlayerPicksTable rows={playerRows} />
            : <BandStatsTable   rows={bandSummary} />
          }
        </ScrollView>
      )}
    </View>
  );
}

// ─── styles ───────────────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  root:         { flex: 1, backgroundColor: Colors.background },

  header:       { paddingTop: 16, paddingBottom: 8, paddingHorizontal: 16 },
  headerMeta:   { flexDirection: 'row', alignItems: 'center', gap: 5, marginBottom: 4 },
  headerMetaText: { fontSize: 10, fontWeight: '700', letterSpacing: 1.2, color: Colors.primary, opacity: 0.7, textTransform: 'uppercase' },
  headerTitle:  { fontSize: 24, fontWeight: '700', color: Colors.text, letterSpacing: -0.3 },
  headerSub:    { fontSize: 11, color: Colors.textTertiary, marginTop: 2 },

  toggle:       { flexDirection: 'row', marginHorizontal: 16, marginBottom: 10, borderRadius: 10, backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border, overflow: 'hidden' },
  toggleBtn:    { flex: 1, paddingVertical: 9, alignItems: 'center' },
  toggleBtnActive: { backgroundColor: Colors.primary + '22' },
  toggleText:   { fontSize: 13, fontWeight: '600', color: Colors.textTertiary },
  toggleTextActive: { color: Colors.primary, fontWeight: '700' },

  filterBar:    { marginBottom: 8 },
  filterBarInner: { paddingHorizontal: 16, gap: 8, flexDirection: 'row', alignItems: 'center' },
  filterChip:   { flexDirection: 'row', alignItems: 'center', backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 7 },
  filterChipLabel: { fontSize: 9, fontWeight: '600', color: Colors.textTertiary, letterSpacing: 0.7, textTransform: 'uppercase', marginRight: 4 },
  filterChipVal: { fontSize: 12, fontWeight: '600', color: Colors.text, maxWidth: 70 },

  miniStats:    { marginHorizontal: 16, marginBottom: 8, paddingVertical: 6, paddingHorizontal: 10, backgroundColor: Colors.card, borderRadius: 8, borderWidth: 1, borderColor: Colors.border },
  miniStatsText: { fontSize: 12 },

  thead:        { flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: Colors.border, paddingVertical: 8, backgroundColor: Colors.background },
  th:           { paddingHorizontal: 4 },
  thText:       { fontSize: 9, fontWeight: '700', color: Colors.primary, letterSpacing: 0.5, textTransform: 'uppercase' },

  trow:         { flexDirection: 'row', alignItems: 'center', borderBottomWidth: 0.5, borderBottomColor: Colors.borderSubtle, paddingVertical: 7 },
  trowAlt:      { backgroundColor: 'rgba(255,255,255,0.025)' },

  td:           { paddingHorizontal: 4, justifyContent: 'center' },
  tdText:       { fontSize: 11, color: Colors.textSecondary },
  tdBold:       { fontSize: 11, fontWeight: '600', color: Colors.text },
  tdSub:        { fontSize: 9, color: Colors.textTertiary, marginTop: 1 },

  dirText:      { fontSize: 10, fontWeight: '700', letterSpacing: 0.3 },
  resultBadge:  { borderRadius: 5, borderWidth: 1, paddingHorizontal: 5, paddingVertical: 2, minWidth: 36, alignItems: 'center' },
  resultText:   { fontSize: 10, fontWeight: '700' },

  emptyRow:     { paddingVertical: 40, alignItems: 'center' },
  emptyText:    { color: Colors.textTertiary, fontSize: 13 },
  footer:       { textAlign: 'center', fontSize: 11, color: Colors.textTertiary, marginTop: 12, marginBottom: 32, paddingHorizontal: 16 },
});

const card = StyleSheet.create({
  wrap:   { width: 110, borderRadius: 10, borderWidth: 1, overflow: 'hidden', backgroundColor: Colors.card },
  header: { paddingHorizontal: 10, paddingVertical: 7 },
  band:   { fontSize: 10, fontWeight: '800', letterSpacing: 0.8 },
  devRange: { fontSize: 9, color: Colors.textTertiary, marginTop: 1 },
  body:   { paddingHorizontal: 10, paddingBottom: 8, gap: 4 },
  row:    { flexDirection: 'row', alignItems: 'center', gap: 5 },
  dir:    { fontSize: 11, fontWeight: '700', color: Colors.textSecondary, width: 14 },
  pct:    { fontSize: 14, fontWeight: '800', flex: 1 },
  n:      { fontSize: 10, color: Colors.textTertiary },
});

const drop = StyleSheet.create({
  overlay:     { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'center', alignItems: 'center', paddingHorizontal: 32 },
  box:         { width: '100%', backgroundColor: '#111', borderRadius: 16, borderWidth: 1, borderColor: Colors.border, padding: 12 },
  title:       { fontSize: 11, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 1, textTransform: 'uppercase', paddingHorizontal: 8, paddingBottom: 10 },
  option:      { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 11, paddingHorizontal: 12, borderRadius: 8 },
  optionActive: { backgroundColor: Colors.primaryGlow },
  optText:     { fontSize: 14, color: Colors.textSecondary, fontWeight: '500' },
  optTextActive: { color: Colors.primary, fontWeight: '700' },
});
