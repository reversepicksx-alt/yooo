import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity,
  ActivityIndicator, RefreshControl, Platform,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import {
  getNotifications, markNotificationsRead, AppNotification,
} from '@/lib/api';

// ── Helpers ──────────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  try {
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60)   return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  } catch { return ''; }
}

function notifIcon(n: AppNotification): { name: keyof typeof Ionicons.glyphMap; color: string } {
  if (n.type === 'mention') return { name: 'chatbubble', color: '#4DA6FF' };
  const result = (n.data?.result as string) || '';
  if (result === 'hit')  return { name: 'checkmark-circle',  color: '#39FF14' };
  if (result === 'miss') return { name: 'close-circle',      color: '#FF4444' };
  if (result === 'push') return { name: 'remove-circle',     color: '#FFB800' };
  return { name: 'notifications', color: Colors.primary };
}

function accentColor(n: AppNotification): string {
  if (n.type === 'mention') return '#4DA6FF';
  const result = (n.data?.result as string) || '';
  if (result === 'hit')  return '#39FF14';
  if (result === 'miss') return '#FF4444';
  if (result === 'push') return '#FFB800';
  return Colors.primary;
}

// ── Card component ────────────────────────────────────────────────────────────

function NotifCard({ item, onPress }: { item: AppNotification; onPress: (n: AppNotification) => void }) {
  const icon   = notifIcon(item);
  const accent = accentColor(item);

  return (
    <TouchableOpacity
      style={[styles.card, !item.read && styles.cardUnread]}
      onPress={() => onPress(item)}
      activeOpacity={0.75}
    >
      {/* Left accent bar */}
      <View style={[styles.accentBar, { backgroundColor: accent }]} />

      {/* Icon */}
      <View style={[styles.iconWrap, { backgroundColor: accent + '1A' }]}>
        <Ionicons name={icon.name} size={20} color={icon.color} />
      </View>

      {/* Content */}
      <View style={styles.content}>
        <Text style={styles.title} numberOfLines={2}>{item.title}</Text>
        <Text style={styles.body}  numberOfLines={2}>{item.body}</Text>
        <Text style={styles.time}>{timeAgo(item.createdAt)}</Text>
      </View>

      {/* Unread dot */}
      {!item.read && <View style={[styles.unreadDot, { backgroundColor: accent }]} />}
    </TouchableOpacity>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function NotificationsScreen() {
  const { session } = useAuth();
  const insets = useSafeAreaInsets();
  const topPad = Platform.OS === 'web' ? 67 : insets.top;

  const [items,      setItems]      = useState<AppNotification[]>([]);
  const [loading,    setLoading]    = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [markingAll, setMarkingAll] = useState(false);
  // Hides locally-cleared notifications: ids the user has tapped "Clear" on
  // since the last fresh load. Pull-to-refresh resets this set.
  const [hiddenIds,  setHiddenIds]  = useState<Set<string>>(new Set());

  const load = useCallback(async (isRefresh = false) => {
    if (!session?.email) return;
    if (isRefresh) setRefreshing(true); else setLoading(true);
    try {
      const data = await getNotifications(session.email, 60);
      setItems(data || []);
      if (isRefresh) setHiddenIds(new Set()); // refresh shows everything again
    } catch {}
    finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [session?.email]);

  useEffect(() => { load(); }, [load]);

  // Clear: mark every visible notif as read AND hide them from the list.
  // (They stay in the DB so pull-to-refresh can still surface them.)
  const handleClearAll = async () => {
    if (!session?.email || items.length === 0) return;
    setMarkingAll(true);
    const visibleIds = items.filter(n => !hiddenIds.has(n.notificationId)).map(n => n.notificationId);
    try {
      await markNotificationsRead(session.email, visibleIds);
    } catch {}
    setHiddenIds(prev => {
      const next = new Set(prev);
      visibleIds.forEach(id => next.add(id));
      return next;
    });
    setMarkingAll(false);
  };

  const handleCardPress = async (n: AppNotification) => {
    if (!n.read && session?.email) {
      try {
        await markNotificationsRead(session.email, [n.notificationId]);
        setItems(prev => prev.map(x => x.notificationId === n.notificationId ? { ...x, read: true } : x));
      } catch {}
    }
  };

  const visibleItems = items.filter(n => !hiddenIds.has(n.notificationId));
  const unreadCount  = visibleItems.filter(n => !n.read).length;

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <View style={[styles.root, { paddingTop: topPad }]}>

      {/* Header */}
      <View style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>ALERTS</Text>
          {visibleItems.length > 0 && (
            <Text style={styles.headerSub}>
              {unreadCount > 0 ? `${unreadCount} unread` : `${visibleItems.length} total`}
            </Text>
          )}
        </View>
        {visibleItems.length > 0 && (
          <TouchableOpacity
            style={styles.markAllBtn}
            onPress={handleClearAll}
            disabled={markingAll}
            activeOpacity={0.75}
          >
            {markingAll
              ? <ActivityIndicator size="small" color={Colors.primary} />
              : <Text style={styles.markAllText}>Clear</Text>
            }
          </TouchableOpacity>
        )}
      </View>

      {/* Divider */}
      <View style={styles.divider} />

      {/* Content */}
      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator color={Colors.primary} size="large" />
        </View>
      ) : visibleItems.length === 0 ? (
        <View style={styles.centered}>
          <Ionicons name="notifications-off-outline" size={48} color={Colors.textTertiary} />
          <Text style={styles.emptyTitle}>{items.length === 0 ? 'No alerts yet' : 'All caught up'}</Text>
          <Text style={styles.emptyBody}>
            {items.length === 0
              ? "You'll be notified here when a pick settles or someone mentions you in chat."
              : 'Pull to refresh to see past alerts again.'}
          </Text>
        </View>
      ) : (
        <FlatList
          data={visibleItems}
          keyExtractor={n => n.notificationId}
          renderItem={({ item }) => <NotifCard item={item} onPress={handleCardPress} />}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => load(true)}
              tintColor={Colors.primary}
              colors={[Colors.primary]}
            />
          }
          showsVerticalScrollIndicator={false}
        />
      )}
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#050505',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 10,
  },
  headerTitle: {
    fontSize: 22,
    fontWeight: '800',
    color: '#FFFFFF',
    letterSpacing: 2,
    fontFamily: 'JetBrainsMono_700Bold',
  },
  headerSub: {
    fontSize: 11,
    color: Colors.primary,
    fontFamily: 'JetBrainsMono_400Regular',
    marginTop: 2,
    letterSpacing: 0.5,
  },
  markAllBtn: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: Colors.primary + '40',
    backgroundColor: Colors.primary + '10',
    minWidth: 44,
    alignItems: 'center',
  },
  markAllText: {
    fontSize: 11,
    color: Colors.primary,
    fontFamily: 'JetBrainsMono_600SemiBold',
    letterSpacing: 0.3,
  },
  divider: {
    height: 0.5,
    backgroundColor: 'rgba(57,255,20,0.15)',
    marginHorizontal: 0,
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
    gap: 12,
  },
  emptyTitle: {
    fontSize: 16,
    color: Colors.textSecondary,
    fontFamily: 'JetBrainsMono_600SemiBold',
    letterSpacing: 0.5,
  },
  emptyBody: {
    fontSize: 13,
    color: Colors.textTertiary,
    textAlign: 'center',
    lineHeight: 20,
    fontFamily: 'JetBrainsMono_400Regular',
  },
  list: {
    paddingHorizontal: 14,
    paddingTop: 10,
    paddingBottom: 20,
    gap: 8,
  },
  card: {
    backgroundColor: '#111111',
    borderRadius: 12,
    flexDirection: 'row',
    alignItems: 'center',
    overflow: 'hidden',
    borderWidth: 0.5,
    borderColor: '#222',
    minHeight: 72,
  },
  cardUnread: {
    backgroundColor: '#141814',
    borderColor: 'rgba(57,255,20,0.18)',
  },
  accentBar: {
    width: 3,
    alignSelf: 'stretch',
  },
  iconWrap: {
    width: 40,
    height: 40,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    marginHorizontal: 12,
    flexShrink: 0,
  },
  content: {
    flex: 1,
    paddingVertical: 12,
    paddingRight: 20,
    gap: 3,
  },
  title: {
    fontSize: 13,
    fontWeight: '700',
    color: '#EFEFEF',
    fontFamily: 'JetBrainsMono_700Bold',
    letterSpacing: 0.2,
    lineHeight: 18,
  },
  body: {
    fontSize: 11,
    color: Colors.textSecondary,
    fontFamily: 'JetBrainsMono_400Regular',
    lineHeight: 16,
  },
  time: {
    fontSize: 10,
    color: Colors.textTertiary,
    fontFamily: 'JetBrainsMono_400Regular',
    marginTop: 2,
    letterSpacing: 0.3,
  },
  unreadDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    marginRight: 14,
    flexShrink: 0,
  },
});
