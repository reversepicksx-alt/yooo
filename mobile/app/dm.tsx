import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, FlatList, TouchableOpacity, StyleSheet,
  ActivityIndicator, Alert, Platform,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import { getDmInbox, markDmRead, type DmConversation } from '@/lib/api';

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

  const handleOpen = (conv: DmConversation) => {
    if (session?.email) {
      markDmRead(session.email, conv.otherId).catch(() => {});
    }
    router.push(`/dm-thread?otherId=${encodeURIComponent(conv.otherId)}&name=${encodeURIComponent(conv.otherName)}&image=${encodeURIComponent(conv.otherImage || '')}`);
  };

  const topPad = Platform.OS === 'web' ? 67 : insets.top;

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      <View style={[styles.header, { paddingHorizontal: 20 }]}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Ionicons name="chevron-back" size={24} color={Colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Reverse Mail</Text>
        <View style={{ width: 40 }} />
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
            {session?.accessType?.toLowerCase() === 'owner'
              ? 'Users will message you here for support.'
              : 'Tap the owner\'s profile to send a message for help.'}
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
              <View style={styles.rowAvatar}>
                {item.otherImage ? (
                  <View style={{ width: 48, height: 48, borderRadius: 24, overflow: 'hidden' }}>
                    <Text style={{ display: 'none' }} />
                  </View>
                ) : (
                  <View style={styles.rowAvatarFallback}>
                    <Text style={styles.rowAvatarText}>{item.otherName.slice(0, 1).toUpperCase()}</Text>
                  </View>
                )}
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
            </TouchableOpacity>
          )}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingBottom: 16 },
  backBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  headerTitle: { fontSize: 20, fontWeight: '800', color: Colors.text },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  emptyIcon: { fontSize: 40 },
  emptyTitle: { fontSize: 17, fontWeight: '700', color: Colors.text },
  emptyBody: { fontSize: 13, color: Colors.textSecondary, textAlign: 'center', paddingHorizontal: 40 },
  row: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingVertical: 12, borderBottomWidth: 0.5, borderBottomColor: Colors.border,
  },
  rowAvatar: { width: 48, height: 48 },
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
});
