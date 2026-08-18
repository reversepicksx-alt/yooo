import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, FlatList, TouchableOpacity, StyleSheet,
  ActivityIndicator, Modal, TextInput, Platform, KeyboardAvoidingView,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import { getDmInbox, markDmRead, searchUsers, deleteDmConversation, type DmConversation } from '@/lib/api';

function timeAgo(ts: string): string {
  const now = Date.now();
  const t = new Date(ts).getTime();
  const diff = Math.max(0, now - t);
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'Just now';
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}

export default function DmInboxScreen() {
  const { session } = useAuth();
  const insets = useSafeAreaInsets();
  const [conversations, setConversations] = useState<DmConversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const isOwner = session?.accessType?.toLowerCase() === 'owner';

  // ── Compose modal state (owner only) ────────────────────────────────────
  const [composeOpen, setComposeOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<{ id: string; username: string | null; displayName: string | null; label: string }[]>([]);
  const [searching, setSearching] = useState(false);

  const load = useCallback(async () => {
    if (!session?.email) return;
    try {
      const data = await getDmInbox(session.email);
      setConversations(data);
    } catch (e: any) {
      console.warn('[DM inbox]', e?.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [session?.email]);

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  // Debounced user search for compose modal
  useEffect(() => {
    if (!composeOpen) return;
    if (searchQuery.trim().length < 2) {
      setSearchResults([]);
      return;
    }
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await searchUsers(searchQuery.trim());
        // Filter out owner from results
        setSearchResults(res.filter(u => u.id !== session?.email));
      } catch {
        setSearchResults([]);
      } finally {
        setSearching(false);
      }
    }, 280);
    return () => clearTimeout(timer);
  }, [searchQuery, composeOpen, session?.email]);

  const handleDelete = async (conv: DmConversation) => {
    if (!session?.email) return;
    const confirmed = window.confirm(`Delete entire conversation with ${conv.otherName}? This cannot be undone.`);
    if (!confirmed) return;
    try {
      await deleteDmConversation(session.email, conv.otherId);
      setConversations(prev => prev.filter(c => c.otherId !== conv.otherId));
    } catch (e: any) {
      window.alert('Failed to delete conversation.');
    }
  };

  const handleOpen = (conv: DmConversation) => {
    if (session?.email) {
      markDmRead(session.email, conv.otherId).catch(() => {});
    }
    router.push(`/dm-thread?otherId=${encodeURIComponent(conv.otherId)}&name=${encodeURIComponent(conv.otherName)}&image=${encodeURIComponent(conv.otherImage || '')}`);
  };

  const handleComposeSelect = (user: { id: string; username: string | null; displayName: string | null; label: string }) => {
    setComposeOpen(false);
    setSearchQuery('');
    setSearchResults([]);
    const name = user.username ? `@${user.username}` : (user.displayName || user.label);
    router.push(`/dm-thread?otherId=${encodeURIComponent(user.id)}&name=${encodeURIComponent(name)}&image=`);
  };

  const topPad = Platform.OS === 'web' ? 67 : insets.top;

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      <View style={[styles.header, { paddingHorizontal: 20 }]}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Ionicons name="chevron-back" size={24} color={Colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Reverse Mail</Text>
        {isOwner ? (
          <TouchableOpacity
            style={styles.composeBtn}
            onPress={() => { setComposeOpen(true); setSearchQuery(''); setSearchResults([]); }}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          >
            <Ionicons name="create-outline" size={22} color={Colors.primary} />
          </TouchableOpacity>
        ) : (
          <View style={{ width: 40 }} />
        )}
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.primary} />
        </View>
      ) : conversations.length === 0 ? (
        <View style={styles.center}>
          <Text style={styles.emptyIcon}>✉️</Text>
          <Text style={styles.emptyTitle}>No messages yet</Text>
          <Text style={styles.emptyBody}>
            {isOwner
              ? 'Users will message you here. Tap ✏️ to start a conversation.'
              : 'Contact Reverse from Account settings to send a direct message.'}
          </Text>
        </View>
      ) : (
        <FlatList
          data={conversations}
          keyExtractor={(item) => item.otherId}
          contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 8, paddingBottom: insets.bottom + 20 }}
          refreshing={refreshing}
          onRefresh={() => { setRefreshing(true); load(); }}
          renderItem={({ item }) => (
            <TouchableOpacity style={styles.row} onPress={() => handleOpen(item)} activeOpacity={0.7}>
              <View style={styles.rowAvatarFallback}>
                <Text style={styles.rowAvatarText}>{item.otherName.slice(0, 1).toUpperCase()}</Text>
              </View>
              <View style={styles.rowBody}>
                <View style={styles.rowTop}>
                  <Text style={[styles.rowName, item.unreadCount > 0 && styles.rowNameUnread]}>{item.otherName}</Text>
                  <Text style={styles.rowTime}>{timeAgo(item.lastAt)}</Text>
                </View>
                <Text
                  style={[styles.rowPreview, item.unreadCount > 0 && styles.rowPreviewUnread]}
                  numberOfLines={1}
                >
                  {item.lastMessage}
                </Text>
              </View>
              {item.unreadCount > 0 && (
                <View style={styles.unreadBadge}>
                  <Text style={styles.unreadText}>{item.unreadCount}</Text>
                </View>
              )}
              {isOwner && (
                <TouchableOpacity
                  onPress={(e) => { e.stopPropagation(); handleDelete(item); }}
                  hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
                  style={styles.deleteBtn}
                >
                  <Ionicons name="trash-outline" size={17} color="rgba(255,59,48,0.6)" />
                </TouchableOpacity>
              )}
            </TouchableOpacity>
          )}
        />
      )}

      {/* ── Compose modal (owner only) ──────────────────────────────────── */}
      <Modal
        visible={composeOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setComposeOpen(false)}
      >
        <KeyboardAvoidingView
          style={styles.modalOverlay}
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        >
          <View style={[styles.modalSheet, { paddingBottom: insets.bottom + 12 }]}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>New Message</Text>
              <TouchableOpacity onPress={() => setComposeOpen(false)}>
                <Ionicons name="close" size={22} color={Colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <View style={styles.searchRow}>
              <Ionicons name="search" size={16} color={Colors.textTertiary} style={{ marginRight: 8 }} />
              <TextInput
                style={styles.searchInput}
                placeholder="Search by username or email…"
                placeholderTextColor={Colors.textTertiary}
                value={searchQuery}
                onChangeText={setSearchQuery}
                autoFocus
                autoCapitalize="none"
                autoCorrect={false}
                keyboardAppearance="dark"
              />
              {searching && <ActivityIndicator size="small" color={Colors.primary} />}
            </View>
            {searchResults.length > 0 ? (
              <FlatList
                data={searchResults}
                keyExtractor={(u) => u.id}
                style={{ maxHeight: 320 }}
                keyboardShouldPersistTaps="handled"
                renderItem={({ item }) => (
                  <TouchableOpacity style={styles.resultRow} onPress={() => handleComposeSelect(item)} activeOpacity={0.7}>
                    <View style={styles.resultAvatar}>
                      <Text style={styles.resultAvatarText}>{(item.username || item.displayName || item.label || '?').slice(0, 1).toUpperCase()}</Text>
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.resultName}>
                        {item.username ? `@${item.username}` : (item.displayName || item.label)}
                      </Text>
                      {item.username && item.displayName && (
                        <Text style={styles.resultSub}>{item.displayName}</Text>
                      )}
                    </View>
                    <Ionicons name="chevron-forward" size={16} color={Colors.textTertiary} />
                  </TouchableOpacity>
                )}
              />
            ) : searchQuery.length >= 2 && !searching ? (
              <View style={styles.center}>
                <Text style={styles.emptyBody}>No users found</Text>
              </View>
            ) : searchQuery.length < 2 ? (
              <View style={{ padding: 20, alignItems: 'center' }}>
                <Text style={styles.emptyBody}>Type at least 2 characters to search</Text>
              </View>
            ) : null}
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingBottom: 16 },
  backBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  composeBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  headerTitle: { fontSize: 20, fontWeight: '800', color: Colors.text },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12, padding: 24 },
  emptyIcon: { fontSize: 40 },
  emptyTitle: { fontSize: 17, fontWeight: '700', color: Colors.text },
  emptyBody: { fontSize: 13, color: Colors.textSecondary, textAlign: 'center', paddingHorizontal: 20 },
  row: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingVertical: 12, borderBottomWidth: 0.5, borderBottomColor: Colors.border,
  },
  rowAvatarFallback: {
    width: 48, height: 48, borderRadius: 24,
    backgroundColor: Colors.primaryDim, alignItems: 'center', justifyContent: 'center',
  },
  rowAvatarText: { fontSize: 18, fontWeight: '700', color: Colors.primary },
  rowBody: { flex: 1 },
  rowTop: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 },
  rowName: { fontSize: 15, fontWeight: '600', color: Colors.text },
  rowNameUnread: { fontWeight: '800' },
  rowTime: { fontSize: 12, color: Colors.textTertiary },
  rowPreview: { fontSize: 14, color: Colors.textSecondary },
  rowPreviewUnread: { color: Colors.text, fontWeight: '500' },
  unreadBadge: {
    minWidth: 20, height: 20, borderRadius: 10,
    backgroundColor: Colors.primary, alignItems: 'center', justifyContent: 'center',
    paddingHorizontal: 6,
  },
  unreadText: { fontSize: 12, fontWeight: '800', color: Colors.background },
  // Compose modal
  modalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.7)', justifyContent: 'flex-end' },
  modalSheet: {
    backgroundColor: Colors.card, borderTopLeftRadius: 20, borderTopRightRadius: 20,
    paddingTop: 20, maxHeight: '80%',
  },
  modalHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 20, marginBottom: 14,
  },
  modalTitle: { fontSize: 18, fontWeight: '800', color: Colors.text },
  searchRow: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: Colors.background, borderRadius: 10,
    paddingHorizontal: 12, paddingVertical: 10,
    marginHorizontal: 16, marginBottom: 8,
  },
  searchInput: { flex: 1, fontSize: 15, color: Colors.text },
  resultRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingHorizontal: 16, paddingVertical: 12,
    borderBottomWidth: 0.5, borderBottomColor: Colors.border,
  },
  resultAvatar: {
    width: 40, height: 40, borderRadius: 20,
    backgroundColor: Colors.primaryDim, alignItems: 'center', justifyContent: 'center',
  },
  resultAvatarText: { fontSize: 16, fontWeight: '700', color: Colors.primary },
  resultName: { fontSize: 15, fontWeight: '600', color: Colors.text },
  resultSub: { fontSize: 12, color: Colors.textTertiary, marginTop: 2 },
  deleteBtn: { padding: 6, marginLeft: 4 },
});
