import React, { useState, useMemo } from 'react';
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
  BandSummaryRow,
  OverallBandRow,
} from '@/lib/api';

// ─── Band config ───────────────────────────────────────────────────────────────
const BAND_ORDER_MAP: Record<string, number> = {
  aligned: 0, mild: 1, moderate: 2, elevated: 3, extreme: 4,
};

const BAND_LABELS: Record<string, string> = {
  aligned:  'Aligned',
  mild:     'Mild',
  moderate: 'Moderate',
  elevated: 'Elevated',
  extreme:  'Extreme',
};

const BAND_RANGE: Record<string, string> = {
  aligned:  'at projection (0–5%)',
  mild:     '5–10% above line',
  moderate: '10–15% above line',
  elevated: '15–20% above line',
  extreme:  '20%+ above line',
};

const BAND_ACCENT: Record<string, string> = {
  aligned:  '#39FF14',
  mild:     '#B5FF14',
  moderate: '#FFCC00',
  elevated: '#FF8C00',
  extreme:  '#FF3B30',
};

const PROP_LABELS: Record<string, string> = {
  pass_attempts:    'Pass Attempts',
  saves:            'Saves',
  shots:            'Shots',
  shots_on_target:  'Shots on Target',
  goals:            'Goals',
  assists:          'Assists',
  key_passes:       'Key Passes',
  tackles:          'Tackles',
  dribbles:         'Dribbles',
  clearances:       'Clearances',
  crosses:          'Crosses',
};

function hitColor(pct: number) {
  if (pct >= 70) return '#39FF14';
  if (pct >= 55) return '#FFCC00';
  return '#FF3B30';
}

// ─── Dropdown ─────────────────────────────────────────────────────────────────
function FilterChip({
  label, value, onPress,
}: { label: string; value: string; onPress: () => void }) {
  const active = value !== 'All';
  return (
    <TouchableOpacity style={[chip.wrap, active && chip.wrapActive]} onPress={onPress}>
      <Text style={[chip.label, active && chip.labelActive]}>{label}</Text>
      {active && <Text style={chip.val} numberOfLines={1}> · {value}</Text>}
      <Ionicons name="chevron-down" size={11} color={active ? Colors.primary : '#555'} style={{ marginLeft: 2 }} />
    </TouchableOpacity>
  );
}

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
          <ScrollView style={{ maxHeight: 320 }} showsVerticalScrollIndicator={false}>
            {options.map(opt => (
              <TouchableOpacity
                key={opt.value}
                style={[drop.option, selected === opt.value && drop.optActive]}
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

// ─── Intelligence Table Row ────────────────────────────────────────────────────
function IntelRow({
  band, lineVsModel, position, venue, oppPoss, direction, hitPct, count, isLast,
}: {
  band: string;
  lineVsModel: string;
  position: string;
  venue: string;
  oppPoss: string;
  direction: string;
  hitPct: number;
  count: number;
  isLast: boolean;
}) {
  const accent = BAND_ACCENT[band] ?? '#888';
  const isUnder = direction === 'UNDER';

  return (
    <View style={[row.wrap, !isLast && row.border]}>
      {/* Band column */}
      <View style={row.bandCol}>
        <View style={[row.dot, { backgroundColor: accent }]} />
        <View>
          <Text style={[row.bandName, { color: accent }]}>{BAND_LABELS[band] ?? band}</Text>
          <Text style={row.bandRange}>{lineVsModel}</Text>
        </View>
      </View>

      {/* Meta columns */}
      <View style={row.metaCol}>
        {position !== 'All' && (
          <View style={row.tag}>
            <Text style={row.tagText}>{position}</Text>
          </View>
        )}
        {venue !== 'All' && (
          <View style={[row.tag, { backgroundColor: '#1a1a1a' }]}>
            <Text style={row.tagText}>{venue}</Text>
          </View>
        )}
        {oppPoss !== '—' && (
          <View style={[row.tag, { backgroundColor: '#0f1f0f' }]}>
            <Text style={[row.tagText, { color: Colors.primary }]}>
              {oppPoss} poss.
            </Text>
          </View>
        )}
      </View>

      {/* Hit rate column */}
      <View style={row.hitCol}>
        <Text style={[row.hitPct, { color: hitColor(hitPct) }]}>
          {hitPct.toFixed(1)}%
        </Text>
        <Text style={row.hitCount}>
          {isUnder ? 'UNDER' : 'OVER'} · {count} picks
        </Text>
      </View>
    </View>
  );
}

// ─── Main Screen ───────────────────────────────────────────────────────────────
export default function TopTableScreen() {
  const insets = useSafeAreaInsets();
  const [posFilter,  setPosFilter]  = useState('All');
  const [venueFilter, setVenueFilter] = useState('All');
  const [dirFilter,  setDirFilter]  = useState('UNDER');
  const [propFilter, setPropFilter] = useState('All');

  const [showPos,  setShowPos]  = useState(false);
  const [showVenue, setShowVenue] = useState(false);
  const [showDir,  setShowDir]  = useState(false);
  const [showProp, setShowProp] = useState(false);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['topPropsTable'],
    queryFn: getTopPropsTable,
    staleTime: 2 * 60 * 1000,
  });

  // Derive position options from band summary
  const posOptions = useMemo(() => {
    if (!data?.bandSummary) return ['All'];
    const positions = Array.from(new Set(data.bandSummary.map(r => r.position).filter(Boolean))).sort();
    return ['All', ...positions];
  }, [data?.bandSummary]);

  // Derive prop options from band summary
  const propOptions = useMemo(() => {
    if (!data?.bandSummary) return ['All'];
    const props = Array.from(new Set(data.bandSummary.map(r => r.propType).filter(Boolean))).sort();
    return ['All', ...props.map(p => PROP_LABELS[p] ?? p)];
  }, [data?.bandSummary]);

  // Build aggregated rows for display
  const tableRows = useMemo(() => {
    if (!data) return [];

    const source = data.bandSummary;
    let filtered = [...source];

    // Direction filter
    filtered = filtered.filter(r => r.direction.toUpperCase() === dirFilter);

    // Position filter
    if (posFilter !== 'All') {
      filtered = filtered.filter(r => r.position === posFilter);
    }

    // Venue filter
    if (venueFilter !== 'All') {
      filtered = filtered.filter(r => r.venue.toLowerCase() === venueFilter.toLowerCase());
    }

    // Prop filter
    if (propFilter !== 'All') {
      const propKey = Object.entries(PROP_LABELS).find(([, v]) => v === propFilter)?.[0];
      if (propKey) filtered = filtered.filter(r => r.propType === propKey);
    }

    // Aggregate by band
    const byBand: Record<string, { hits: number; misses: number; total: number }> = {};
    for (const r of filtered) {
      if (!byBand[r.band]) byBand[r.band] = { hits: 0, misses: 0, total: 0 };
      byBand[r.band].hits += r.hits;
      byBand[r.band].misses += r.misses;
      byBand[r.band].total += r.total;
    }

    const BAND_SHOW_ORDER = ['elevated', 'moderate', 'mild', 'extreme', 'aligned'];
    return BAND_SHOW_ORDER
      .filter(b => byBand[b] && byBand[b].total > 0)
      .map(b => ({
        band: b,
        hits: byBand[b].hits,
        total: byBand[b].total,
        hitPct: byBand[b].total > 0 ? (byBand[b].hits / byBand[b].total) * 100 : 0,
      }));
  }, [data, posFilter, venueFilter, dirFilter, propFilter]);

  const totalPicks = tableRows.reduce((s, r) => s + r.total, 0);
  const totalHits = tableRows.reduce((s, r) => s + r.hits, 0);
  const overallPct = totalPicks > 0 ? Math.round(totalHits / totalPicks * 100) : 0;

  return (
    <View style={[styles.screen, { paddingTop: insets.top }]}>
      <ScrollView
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={isFetching && !isLoading}
            onRefresh={refetch}
            tintColor={Colors.primary}
          />
        }
      >
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.title}>PROPS INTELLIGENCE</Text>
          <Text style={styles.subtitle}>
            {data ? `${data.totalDeduped} settled picks · live calibration` : 'Loading data...'}
          </Text>
        </View>

        {/* Overall stat bar */}
        {!isLoading && tableRows.length > 0 && (
          <View style={styles.statBar}>
            <View style={styles.statItem}>
              <Text style={[styles.statNum, { color: hitColor(overallPct) }]}>{overallPct}%</Text>
              <Text style={styles.statLabel}>Hit Rate</Text>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.statItem}>
              <Text style={[styles.statNum, { color: Colors.primary }]}>{totalHits}</Text>
              <Text style={styles.statLabel}>Hits</Text>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.statItem}>
              <Text style={styles.statNum}>{totalPicks}</Text>
              <Text style={styles.statLabel}>Picks</Text>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.statItem}>
              <Text style={styles.statNum}>{tableRows.length}</Text>
              <Text style={styles.statLabel}>Bands</Text>
            </View>
          </View>
        )}

        {/* Filter chips */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.chipBar}
          contentContainerStyle={styles.chipBarInner}
        >
          <FilterChip
            label="Direction"
            value={dirFilter}
            onPress={() => setShowDir(true)}
          />
          <FilterChip
            label="Position"
            value={posFilter}
            onPress={() => setShowPos(true)}
          />
          <FilterChip
            label="Venue"
            value={venueFilter}
            onPress={() => setShowVenue(true)}
          />
          <FilterChip
            label="Prop"
            value={propFilter}
            onPress={() => setShowProp(true)}
          />
        </ScrollView>

        {/* Column headers */}
        <View style={styles.colHeader}>
          <Text style={[styles.colText, { flex: 2.2 }]}>Band</Text>
          <Text style={[styles.colText, { flex: 1.2, textAlign: 'center' }]}>Pos</Text>
          <Text style={[styles.colText, { flex: 1, textAlign: 'center' }]}>Venue</Text>
          <Text style={[styles.colText, { flex: 1.2, textAlign: 'center' }]}>Opp. Poss.</Text>
          <Text style={[styles.colText, { flex: 1.4, textAlign: 'right' }]}>Hit Rate</Text>
        </View>

        {/* Table */}
        {isLoading ? (
          <View style={styles.center}>
            <ActivityIndicator size="large" color={Colors.primary} />
            <Text style={styles.loadingText}>Analyzing picks...</Text>
          </View>
        ) : isError ? (
          <View style={styles.center}>
            <Text style={styles.errorText}>Failed to load data.</Text>
            <TouchableOpacity onPress={() => refetch()} style={styles.retryBtn}>
              <Text style={styles.retryText}>Retry</Text>
            </TouchableOpacity>
          </View>
        ) : tableRows.length === 0 ? (
          <View style={styles.center}>
            <Text style={styles.emptyText}>No data for these filters.</Text>
          </View>
        ) : (
          <View style={styles.tableCard}>
            {tableRows.map((r, i) => {
              // Compute dominant position + venue for display tags
              const relevant = (data?.bandSummary ?? [])
                .filter(s =>
                  s.band === r.band &&
                  s.direction.toUpperCase() === dirFilter &&
                  (posFilter === 'All' || s.position === posFilter) &&
                  (venueFilter === 'All' || s.venue.toLowerCase() === venueFilter.toLowerCase())
                )
                .sort((a, b) => b.total - a.total);

              const topPos   = posFilter !== 'All' ? posFilter : (relevant[0]?.position ?? 'All');
              const topVenue = venueFilter !== 'All' ? venueFilter : (relevant[0]?.venue ?? 'All');

              return (
                <IntelRow
                  key={r.band}
                  band={r.band}
                  lineVsModel={BAND_RANGE[r.band] ?? ''}
                  position={posFilter}
                  venue={venueFilter}
                  oppPoss="—"
                  direction={dirFilter}
                  hitPct={r.hitPct}
                  count={r.total}
                  isLast={i === tableRows.length - 1}
                />
              );
            })}
          </View>
        )}

        {/* Insight note */}
        {!isLoading && !isError && tableRows.length > 0 && (
          <View style={styles.insightBox}>
            <Text style={styles.insightTitle}>What this shows</Text>
            <Text style={styles.insightBody}>
              Each row represents how often our model's {dirFilter.toLowerCase()} picks hit
              when the sportsbook line falls into that deviation band vs our projected value.
              Higher deviation = book disagrees more with the model.
              {'\n\n'}
              <Text style={{ color: Colors.primary, fontWeight: '700' }}>Opp. Possession</Text>
              {' '}will be populated once new picks start tracking opponent avg. possession — a key edge indicator for pass attempt props.
            </Text>
          </View>
        )}

        <View style={{ height: 40 }} />
      </ScrollView>

      {/* Dropdowns */}
      <Dropdown
        visible={showDir}
        title="Direction"
        options={['UNDER', 'OVER'].map(v => ({ label: v, value: v }))}
        selected={dirFilter}
        onSelect={setDirFilter}
        onClose={() => setShowDir(false)}
      />
      <Dropdown
        visible={showPos}
        title="Filter by Position"
        options={posOptions.map(p => ({ label: p, value: p }))}
        selected={posFilter}
        onSelect={setPosFilter}
        onClose={() => setShowPos(false)}
      />
      <Dropdown
        visible={showVenue}
        title="Filter by Venue"
        options={['All', 'Home', 'Away'].map(v => ({ label: v, value: v }))}
        selected={venueFilter}
        onSelect={setVenueFilter}
        onClose={() => setShowVenue(false)}
      />
      <Dropdown
        visible={showProp}
        title="Filter by Prop"
        options={propOptions.map(p => ({ label: p, value: p }))}
        selected={propFilter}
        onSelect={setPropFilter}
        onClose={() => setShowProp(false)}
      />
    </View>
  );
}

// ─── Styles ────────────────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: '#050505',
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: 12,
  },
  title: {
    fontSize: 22,
    fontWeight: '900',
    color: '#FFFFFF',
    letterSpacing: 2,
  },
  subtitle: {
    fontSize: 12,
    color: '#555',
    marginTop: 4,
    letterSpacing: 0.5,
  },
  statBar: {
    flexDirection: 'row',
    marginHorizontal: 20,
    marginBottom: 16,
    backgroundColor: '#0d0d0d',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#1a1a1a',
    paddingVertical: 14,
  },
  statItem: {
    flex: 1,
    alignItems: 'center',
  },
  statNum: {
    fontSize: 20,
    fontWeight: '800',
    color: '#FFFFFF',
  },
  statLabel: {
    fontSize: 10,
    color: '#555',
    marginTop: 2,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  statDivider: {
    width: 1,
    backgroundColor: '#1e1e1e',
    marginVertical: 4,
  },
  chipBar: {
    marginBottom: 12,
  },
  chipBarInner: {
    paddingHorizontal: 20,
    gap: 8,
  },
  colHeader: {
    flexDirection: 'row',
    marginHorizontal: 20,
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#1a1a1a',
    marginBottom: 4,
  },
  colText: {
    fontSize: 10,
    fontWeight: '700',
    color: '#444',
    letterSpacing: 0.8,
    textTransform: 'uppercase',
  },
  tableCard: {
    marginHorizontal: 20,
    backgroundColor: '#0d0d0d',
    borderRadius: 14,
    borderWidth: 1,
    borderColor: '#1a1a1a',
    overflow: 'hidden',
  },
  center: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 60,
  },
  loadingText: {
    color: '#444',
    marginTop: 12,
    fontSize: 13,
  },
  errorText: {
    color: '#FF3B30',
    fontSize: 14,
  },
  emptyText: {
    color: '#444',
    fontSize: 14,
  },
  retryBtn: {
    marginTop: 12,
    paddingHorizontal: 20,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: Colors.primary + '44',
  },
  retryText: {
    color: Colors.primary,
    fontSize: 13,
    fontWeight: '600',
  },
  insightBox: {
    marginHorizontal: 20,
    marginTop: 20,
    padding: 16,
    backgroundColor: '#0a0a0a',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#1a1a1a',
  },
  insightTitle: {
    fontSize: 11,
    fontWeight: '700',
    color: '#444',
    letterSpacing: 1,
    textTransform: 'uppercase',
    marginBottom: 8,
  },
  insightBody: {
    fontSize: 13,
    color: '#666',
    lineHeight: 20,
  },
});

// ─── Row styles ────────────────────────────────────────────────────────────────
const row = StyleSheet.create({
  wrap: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 16,
    paddingHorizontal: 16,
  },
  border: {
    borderBottomWidth: 1,
    borderBottomColor: '#181818',
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 10,
    marginTop: 3,
  },
  bandCol: {
    flex: 2.2,
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  bandName: {
    fontSize: 15,
    fontWeight: '700',
    letterSpacing: 0.3,
  },
  bandRange: {
    fontSize: 11,
    color: '#555',
    marginTop: 2,
  },
  metaCol: {
    flex: 3.4,
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 4,
    justifyContent: 'center',
  },
  tag: {
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 3,
    backgroundColor: '#141414',
  },
  tagText: {
    fontSize: 10,
    fontWeight: '600',
    color: '#888',
    letterSpacing: 0.3,
  },
  hitCol: {
    flex: 1.4,
    alignItems: 'flex-end',
  },
  hitPct: {
    fontSize: 18,
    fontWeight: '800',
  },
  hitCount: {
    fontSize: 10,
    color: '#444',
    marginTop: 2,
  },
});

// ─── Chip styles ───────────────────────────────────────────────────────────────
const chip = StyleSheet.create({
  wrap: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: '#0d0d0d',
    borderWidth: 1,
    borderColor: '#1e1e1e',
  },
  wrapActive: {
    borderColor: Colors.primary + '55',
    backgroundColor: Colors.primary + '10',
  },
  label: {
    fontSize: 12,
    fontWeight: '600',
    color: '#555',
    letterSpacing: 0.3,
  },
  labelActive: {
    color: Colors.primary,
  },
  val: {
    fontSize: 12,
    fontWeight: '600',
    color: Colors.primary,
  },
});

// ─── Dropdown styles ───────────────────────────────────────────────────────────
const drop = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.75)',
    justifyContent: 'flex-end',
  },
  box: {
    backgroundColor: '#111',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingTop: 20,
    paddingBottom: 34,
    paddingHorizontal: 20,
    borderTopWidth: 1,
    borderColor: '#222',
  },
  title: {
    fontSize: 13,
    fontWeight: '700',
    color: '#888',
    letterSpacing: 1,
    textTransform: 'uppercase',
    marginBottom: 16,
  },
  option: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#1a1a1a',
  },
  optActive: {
    backgroundColor: Colors.primary + '10',
    borderRadius: 8,
    paddingHorizontal: 10,
    marginHorizontal: -10,
  },
  optText: {
    fontSize: 15,
    color: '#aaa',
  },
  optTextActive: {
    color: '#fff',
    fontWeight: '700',
  },
});
