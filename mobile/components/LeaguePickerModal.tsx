import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Modal, View, Text, TextInput, TouchableOpacity, ScrollView,
  ActivityIndicator, StyleSheet, Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { LEAGUES, searchLeagues, LeagueSearchResult } from '@/lib/api';

interface Props {
  visible: boolean;
  onClose: () => void;
  onSelect: (league: { id: number; name: string; country?: string }) => void;
  selectedId?: number;
  title?: string;
}

const IS_WEB = Platform.OS === 'web';
const INPUT_STYLE = IS_WEB ? ({ outlineWidth: 0 } as object) : {};
const DEBOUNCE_MS = 280;

export default function LeaguePickerModal({
  visible, onClose, onSelect, selectedId, title = 'Select League',
}: Props) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<LeagueSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastQueryRef = useRef('');

  // Reset query whenever the modal closes so reopening always starts fresh.
  useEffect(() => {
    if (!visible) {
      setQuery('');
      setResults([]);
      setLoading(false);
    }
  }, [visible]);

  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  const handleChange = (text: string) => {
    setQuery(text);
    lastQueryRef.current = text;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (text.trim().length < 2) { setResults([]); setLoading(false); return; }
    setLoading(true);
    debounceRef.current = setTimeout(async () => {
      if (lastQueryRef.current !== text) return;
      try {
        const data = await searchLeagues(text.trim());
        if (lastQueryRef.current === text) setResults(data.leagues || []);
      } catch {
        if (lastQueryRef.current === text) setResults([]);
      } finally {
        if (lastQueryRef.current === text) setLoading(false);
      }
    }, DEBOUNCE_MS);
  };

  // When the query is empty, show the popular shortlist (LEAGUES) so users
  // hit a one-tap path for the common case. Once they type, switch to the
  // full-cache fuzzy results from the backend.
  const showShortlist = query.trim().length < 2;
  const items = useMemo(() => {
    if (showShortlist) {
      return LEAGUES.map(l => ({ id: l.id, name: l.name, country: '' as string }));
    }
    return results.map(l => ({ id: l.id, name: l.name, country: l.country || '' }));
  }, [showShortlist, results]);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <TouchableOpacity activeOpacity={1} style={styles.overlay} onPress={onClose}>
        <TouchableOpacity activeOpacity={1} style={styles.sheet} onPress={() => { /* swallow */ }}>
          <View style={styles.headerRow}>
            <Text style={styles.title}>{title}</Text>
            <TouchableOpacity onPress={onClose} style={styles.closeBtn} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
              <Ionicons name="close" size={20} color={Colors.textSecondary} />
            </TouchableOpacity>
          </View>

          <View style={styles.searchRow}>
            <Ionicons name="search" size={15} color={Colors.textSecondary} />
            <TextInput
              style={[styles.searchInput, INPUT_STYLE]}
              value={query}
              onChangeText={handleChange}
              placeholder="Search any league (e.g. NWSL, Eredivisie, J2)…"
              placeholderTextColor={Colors.textTertiary}
              autoCorrect={false}
              autoCapitalize="none"
              returnKeyType="search"
            />
            {loading ? (
              <ActivityIndicator size="small" color={Colors.primary} />
            ) : query.length > 0 ? (
              <TouchableOpacity onPress={() => handleChange('')} hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}>
                <Ionicons name="close-circle" size={16} color={Colors.textTertiary} />
              </TouchableOpacity>
            ) : null}
          </View>

          {showShortlist && (
            <Text style={styles.hint}>Popular leagues — type to search all 1200+</Text>
          )}
          {!showShortlist && !loading && items.length === 0 && (
            <Text style={styles.hint}>No leagues match "{query}".</Text>
          )}

          <ScrollView style={styles.list} keyboardShouldPersistTaps="always">
            {items.map(l => {
              const active = l.id === selectedId;
              return (
                <TouchableOpacity
                  key={l.id}
                  style={[styles.item, active && styles.itemActive]}
                  onPress={() => { onSelect({ id: l.id, name: l.name, country: l.country }); onClose(); }}
                  activeOpacity={0.7}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={[styles.itemText, active && styles.itemTextActive]} numberOfLines={1}>{l.name}</Text>
                    {l.country ? <Text style={styles.itemSub} numberOfLines={1}>{l.country}</Text> : null}
                  </View>
                  {active && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        </TouchableOpacity>
      </TouchableOpacity>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.6)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: Colors.card,
    borderTopLeftRadius: 16, borderTopRightRadius: 16,
    paddingHorizontal: 16, paddingTop: 16, paddingBottom: 24,
    maxHeight: '80%',
    borderTopWidth: 1, borderColor: Colors.border,
  },
  headerRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginBottom: 12,
  },
  title: { fontSize: 16, fontWeight: '700', color: Colors.text },
  closeBtn: { padding: 4 },
  searchRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: '#1a1a1a',
    borderRadius: 8, borderWidth: 1, borderColor: '#2a2a2a',
    paddingHorizontal: 10, paddingVertical: Platform.OS === 'ios' ? 10 : 6,
  },
  searchInput: {
    flex: 1, color: Colors.text, fontSize: 14, paddingVertical: 0,
  },
  hint: { fontSize: 11, color: Colors.textTertiary, marginTop: 10, marginBottom: 4, marginLeft: 4 },
  list: { marginTop: 8 },
  item: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 12, paddingHorizontal: 12,
    borderRadius: 8, marginBottom: 4,
    backgroundColor: 'transparent',
  },
  itemActive: { backgroundColor: Colors.primaryDim },
  itemText: { fontSize: 14, color: Colors.text, fontWeight: '500' },
  itemTextActive: { color: Colors.primary, fontWeight: '700' },
  itemSub: { fontSize: 11, color: Colors.textSecondary, marginTop: 2 },
});
