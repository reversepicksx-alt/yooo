import React, { useEffect, useState } from 'react';
import {
  Modal, View, Text, TouchableOpacity, ScrollView,
  StyleSheet, Platform, KeyboardAvoidingView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { LEAGUES } from '@/lib/api';
import FuzzySearchInput, { FuzzyLeagueResult } from './FuzzySearchInput';

interface Props {
  visible: boolean;
  onClose: () => void;
  onSelect: (league: { id: number; name: string; country?: string }) => void;
  selectedId?: number;
  title?: string;
}

// Modal that wraps the same `FuzzySearchInput` used by manual mode, so the
// scan-correction flow gets identical UX (no glitches, no custom debounce
// re-implementation, no keyboard layout surprises). KeyboardAvoidingView
// keeps the input visible above the keyboard on iOS.
export default function LeaguePickerModal({
  visible, onClose, onSelect, selectedId, title = 'Select League',
}: Props) {
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (!visible) setQuery('');
  }, [visible]);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={styles.kavRoot}
      >
        {/* Backdrop — tapping anywhere outside the sheet closes the modal. */}
        <TouchableOpacity activeOpacity={1} style={styles.backdrop} onPress={onClose} />

        <View style={styles.sheet}>
          <View style={styles.headerRow}>
            <Text style={styles.title}>{title}</Text>
            <TouchableOpacity
              onPress={onClose}
              style={styles.closeBtn}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              <Ionicons name="close" size={20} color={Colors.textSecondary} />
            </TouchableOpacity>
          </View>

          {/* Reuse the manual-mode search component verbatim — same debounce,
              same dropdown rendering, same keyboard handling. */}
          <FuzzySearchInput
            value={query}
            onChangeText={setQuery}
            searchType="leagues"
            placeholder="Search any league (NWSL, Eredivisie, J2…)"
            onSelectLeague={(l: FuzzyLeagueResult) => {
              onSelect({ id: l.id, name: l.name, country: l.country });
              onClose();
            }}
          />

          {/* Popular shortlist — visible only when the search box is empty.
              Once the user starts typing the FuzzySearchInput dropdown takes
              over and this list is hidden so the two surfaces never compete. */}
          {query.trim().length < 2 && (
            <>
              <Text style={styles.hint}>Popular leagues — type to search all 1200+</Text>
              <ScrollView style={styles.list} keyboardShouldPersistTaps="always">
                {LEAGUES.map(l => {
                  const active = l.id === selectedId;
                  return (
                    <TouchableOpacity
                      key={l.id}
                      style={[styles.item, active && styles.itemActive]}
                      onPress={() => { onSelect({ id: l.id, name: l.name }); onClose(); }}
                      activeOpacity={0.7}
                    >
                      <Text style={[styles.itemText, active && styles.itemTextActive]} numberOfLines={1}>
                        {l.name}
                      </Text>
                      {active && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                    </TouchableOpacity>
                  );
                })}
              </ScrollView>
            </>
          )}
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  kavRoot: { flex: 1, justifyContent: 'flex-end' },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.6)',
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
  hint: { fontSize: 11, color: Colors.textTertiary, marginTop: 12, marginBottom: 4, marginLeft: 4 },
  list: { marginTop: 4 },
  item: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 12, paddingHorizontal: 12,
    borderRadius: 8, marginBottom: 4,
  },
  itemActive: { backgroundColor: Colors.primaryDim },
  itemText: { flex: 1, fontSize: 14, color: Colors.text, fontWeight: '500' },
  itemTextActive: { color: Colors.primary, fontWeight: '700' },
});
