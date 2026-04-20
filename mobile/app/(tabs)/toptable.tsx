import React, { useState, useMemo, useCallback } from 'react';
import {
  View, Text, StyleSheet, ScrollView,
  TouchableOpacity, ActivityIndicator,
  RefreshControl, Modal, Platform,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useQuery } from '@tanstack/react-query';
import Colors from '@/constants/colors';
import { getTopPropsTable, TopPropsRow } from '@/lib/api';

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Att',
  saves: 'Saves',
  shots: 'Shots',
  shots_on_target: 'SOT',
  goals: 'Goals',
  assists: 'Assists',
  key_passes: 'Key Pass',
  tackles: 'Tackles',
  dribbles: 'Dribbles',
  clearances: 'Clears',
  crosses: 'Crosses',
};

type SortKey = keyof TopPropsRow;
type SortDir = 'asc' | 'desc';

interface ColDef {
  key: SortKey;
  label: string;
  width: number;
  align: 'left' | 'center' | 'right';
}

const COLUMNS: ColDef[] = [
  { key: 'propType',  label: 'Prop Type', width: 78,  align: 'left'   },
  { key: 'direction', label: 'Dir',       width: 48,  align: 'center' },
  { key: 'venue',     label: 'Venue',     width: 56,  align: 'center' },
  { key: 'position',  label: 'Pos',       width: 44,  align: 'center' },
  { key: 'hitPct',    label: 'Hit %',     width: 58,  align: 'center' },
  { key: 'hits',      label: 'H',         width: 34,  align: 'center' },
  { key: 'misses',    label: 'M',         width: 34,  align: 'center' },
  { key: 'total',     label: 'Bets',      width: 42,  align: 'center' },
  { key: 'avgOdds',   label: 'Odds',      width: 48,  align: 'center' },
  { key: 'league',    label: 'League',    width: 88,  align: 'left'   },
];

function hitColor(pct: number) {
  if (pct >= 65) return Colors.primary;
  if (pct >= 55) return '#FFCC00';
  return Colors.error;
}

function DropdownModal({
  visible, title, options, selected, onSelect, onClose,
}: {
  visible: boolean;
  title: string;
  options: { label: string; value: string }[];
  selected: string;
  onSelect: (v: string) => void;
  onClose: () => void;
}) {
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <TouchableOpacity style={drop.overlay} activeOpacity={1} onPress={onClose}>
        <View style={drop.box}>
          <Text style={drop.title}>{title}</Text>
          {options.map(opt => (
            <TouchableOpacity
              key={opt.value}
              style={[drop.option, selected === opt.value && drop.optionActive]}
              onPress={() => { onSelect(opt.value); onClose(); }}
            >
              <Text style={[drop.optionText, selected === opt.value && drop.optionTextActive]}>
                {opt.label}
              </Text>
              {selected === opt.value && (
                <Ionicons name="checkmark" size={14} color={Colors.primary} />
              )}
            </TouchableOpacity>
          ))}
        </View>
      </TouchableOpacity>
    </Modal>
  );
}

export default function TopTableTab() {
  const insets = useSafeAreaInsets();
  const [sortKey, setSortKey] = useState<SortKey>('hitPct');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [leagueFilter, setLeagueFilter] = useState('All');
  const [venueFilter, setVenueFilter] = useState('All');
  const [showLeaguePicker, setShowLeaguePicker] = useState(false);
  const [showVenuePicker, setShowVenuePicker] = useState(false);

  const { data, isLoading, refetch, isRefetching } = useQuery({
    queryKey: ['topPropsTable'],
    queryFn: getTopPropsTable,
    staleTime: 60_000,
  });

  const rows = data?.rows ?? [];

  const leagues = useMemo(() => {
    const set = new Set<string>();
    rows.forEach(r => set.add(r.league));
    return ['All', ...Array.from(set).sort()];
  }, [rows]);

  const VENUES = ['All', 'Home', 'Away', 'Neutral'];

  const filtered = useMemo(() => {
    let arr = [...rows];
    if (leagueFilter !== 'All') arr = arr.filter(r => r.league === leagueFilter);
    if (venueFilter !== 'All') arr = arr.filter(r => r.venue.toLowerCase() === venueFilter.toLowerCase());
    arr.sort((a, b) => {
      const av = a[sortKey] ?? '';
      const bv = b[sortKey] ?? '';
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === 'desc' ? -cmp : cmp;
    });
    return arr;
  }, [rows, leagueFilter, venueFilter, sortKey, sortDir]);

  const handleSort = useCallback((key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }, [sortKey]);

  const leagueLabel = leagueFilter === 'All' ? 'All Leagues' : leagueFilter;
  const venueLabel  = venueFilter  === 'All' ? 'All Venues'  : venueFilter;

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <Ionicons name="lock-closed" size={13} color={Colors.primary} style={{ marginRight: 6 }} />
          <Text style={styles.headerSub}>PRIVATE</Text>
        </View>
        <Text style={styles.headerTitle}>Top Props Table</Text>
      </View>

      {/* Filters */}
      <View style={styles.filters}>
        <TouchableOpacity style={styles.filterBtn} onPress={() => setShowLeaguePicker(true)}>
          <View>
            <Text style={styles.filterLabel}>League</Text>
            <Text style={styles.filterValue} numberOfLines={1}>{leagueLabel}</Text>
          </View>
          <Ionicons name="chevron-down" size={14} color={Colors.textTertiary} style={{ marginLeft: 8 }} />
        </TouchableOpacity>

        <TouchableOpacity style={styles.filterBtn} onPress={() => setShowVenuePicker(true)}>
          <View>
            <Text style={styles.filterLabel}>Venue</Text>
            <Text style={styles.filterValue}>{venueLabel}</Text>
          </View>
          <Ionicons name="chevron-down" size={14} color={Colors.textTertiary} style={{ marginLeft: 8 }} />
        </TouchableOpacity>
      </View>

      {/* Content */}
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
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: 16 }}>
            <View>
              {/* Column headers */}
              <View style={styles.thead}>
                {COLUMNS.map(col => (
                  <TouchableOpacity
                    key={col.key}
                    style={[styles.th, { width: col.width }]}
                    onPress={() => handleSort(col.key)}
                    activeOpacity={0.7}
                  >
                    <Text style={[styles.thText, { textAlign: col.align }]}>
                      {col.label}
                      {sortKey === col.key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>

              {/* Data rows */}
              {filtered.length === 0 ? (
                <View style={styles.emptyRow}>
                  <Text style={styles.emptyText}>No props match these filters.</Text>
                </View>
              ) : (
                filtered.map((row, idx) => (
                  <View
                    key={idx}
                    style={[styles.trow, idx % 2 === 1 && styles.trowAlt]}
                  >
                    {COLUMNS.map(col => {
                      const raw = row[col.key];

                      if (col.key === 'hitPct') {
                        const pct = raw as number;
                        const color = hitColor(pct);
                        return (
                          <View key={col.key} style={[styles.td, { width: col.width, justifyContent: 'center', alignItems: 'center' }]}>
                            <View style={[styles.hitBadge, { borderColor: color + '55', backgroundColor: color + '18' }]}>
                              <Text style={[styles.hitText, { color }]}>{pct.toFixed(0)}%</Text>
                            </View>
                          </View>
                        );
                      }

                      if (col.key === 'propType') {
                        return (
                          <View key={col.key} style={[styles.td, { width: col.width }]}>
                            <Text style={[styles.tdBold, { textAlign: col.align }]} numberOfLines={1}>
                              {PROP_LABELS[raw as string] ?? String(raw ?? '—')}
                            </Text>
                          </View>
                        );
                      }

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

                      if (col.key === 'avgOdds') {
                        return (
                          <View key={col.key} style={[styles.td, { width: col.width, alignItems: 'center' }]}>
                            <Text style={styles.tdMuted}>—</Text>
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
                ))
              )}
            </View>
          </ScrollView>

          <Text style={styles.footer}>
            Min 3 bets · {filtered.length} of {data?.totalRecords ?? 0} records · Sorted by {sortKey} {sortDir === 'desc' ? '↓' : '↑'}
          </Text>
        </ScrollView>
      )}

      <DropdownModal
        visible={showLeaguePicker}
        title="Filter by League"
        options={leagues.map(l => ({ label: l === 'All' ? 'All Leagues' : l, value: l }))}
        selected={leagueFilter}
        onSelect={setLeagueFilter}
        onClose={() => setShowLeaguePicker(false)}
      />
      <DropdownModal
        visible={showVenuePicker}
        title="Filter by Venue"
        options={VENUES.map(v => ({ label: v === 'All' ? 'All Venues' : v, value: v }))}
        selected={venueFilter}
        onSelect={setVenueFilter}
        onClose={() => setShowVenuePicker(false)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: Colors.background,
  },

  header: {
    paddingTop: 18,
    paddingBottom: 10,
    paddingHorizontal: 16,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 4,
  },
  headerSub: {
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1.5,
    color: Colors.primary,
    opacity: 0.7,
  },
  headerTitle: {
    fontSize: 26,
    fontWeight: '700',
    color: Colors.text,
    letterSpacing: -0.3,
  },

  filters: {
    flexDirection: 'row',
    gap: 10,
    paddingHorizontal: 16,
    paddingBottom: 12,
  },
  filterBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: Colors.card,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  filterLabel: {
    fontSize: 10,
    fontWeight: '600',
    color: Colors.textTertiary,
    letterSpacing: 0.8,
    marginBottom: 2,
  },
  filterValue: {
    fontSize: 13,
    fontWeight: '600',
    color: Colors.text,
  },

  thead: {
    flexDirection: 'row',
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
    paddingVertical: 8,
    backgroundColor: Colors.background,
  },
  th: {
    paddingHorizontal: 4,
  },
  thText: {
    fontSize: 10,
    fontWeight: '700',
    color: Colors.primary,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },

  trow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderBottomWidth: 0.5,
    borderBottomColor: Colors.borderSubtle,
    paddingVertical: 7,
  },
  trowAlt: {
    backgroundColor: 'rgba(255,255,255,0.025)',
  },

  td: {
    paddingHorizontal: 4,
    justifyContent: 'center',
  },
  tdText: {
    fontSize: 12,
    color: Colors.textSecondary,
  },
  tdBold: {
    fontSize: 12,
    fontWeight: '600',
    color: Colors.text,
  },
  tdMuted: {
    fontSize: 12,
    color: Colors.textTertiary,
    textAlign: 'center',
  },
  dirText: {
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 0.3,
  },

  hitBadge: {
    borderRadius: 5,
    borderWidth: 1,
    paddingHorizontal: 5,
    paddingVertical: 2,
    minWidth: 40,
    alignItems: 'center',
  },
  hitText: {
    fontSize: 11,
    fontWeight: '700',
  },

  emptyRow: {
    paddingVertical: 40,
    alignItems: 'center',
  },
  emptyText: {
    color: Colors.textTertiary,
    fontSize: 13,
  },

  footer: {
    textAlign: 'center',
    fontSize: 11,
    color: Colors.textTertiary,
    marginTop: 12,
    marginBottom: 32,
    paddingHorizontal: 16,
  },
});

const drop = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.6)',
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 32,
  },
  box: {
    width: '100%',
    backgroundColor: '#111',
    borderRadius: 16,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: 12,
    maxHeight: 420,
  },
  title: {
    fontSize: 12,
    fontWeight: '700',
    color: Colors.textTertiary,
    letterSpacing: 1,
    textTransform: 'uppercase',
    paddingHorizontal: 8,
    paddingBottom: 10,
  },
  option: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 12,
    paddingHorizontal: 12,
    borderRadius: 8,
  },
  optionActive: {
    backgroundColor: Colors.primaryGlow,
  },
  optionText: {
    fontSize: 14,
    color: Colors.textSecondary,
    fontWeight: '500',
  },
  optionTextActive: {
    color: Colors.primary,
    fontWeight: '700',
  },
});
