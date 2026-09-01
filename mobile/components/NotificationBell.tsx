import React, { useEffect, useState, useCallback } from 'react';
import { TouchableOpacity, View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import Colors from '@/constants/colors';
import { getNotificationsUnreadCount } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

export default function NotificationBell() {
  const { session } = useAuth();
  const [count, setCount] = useState(0);

  const refresh = useCallback(async () => {
    if (!session?.email) return;
    try {
      const data = await getNotificationsUnreadCount(session.email);
      setCount(data?.count ?? 0);
    } catch {}
  }, [session?.email]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 30_000);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <TouchableOpacity
      style={styles.wrap}
      onPress={() => router.push('/(tabs)/notifications')}
      activeOpacity={0.7}
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
    >
      <Ionicons
        name={count > 0 ? 'notifications' : 'notifications-outline'}
        size={22}
        color={count > 0 ? Colors.primary : Colors.textSecondary}
      />
      {count > 0 && (
        <View style={styles.badge}>
          <Text style={styles.badgeText}>{count > 99 ? '99+' : String(count)}</Text>
        </View>
      )}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  wrap: {
    width: 36,
    height: 36,
    alignItems: 'center',
    justifyContent: 'center',
  },
  badge: {
    position: 'absolute',
    top: 1,
    right: 1,
    backgroundColor: Colors.primary,
    borderRadius: 7,
    minWidth: 14,
    height: 14,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 2,
  },
  badgeText: {
    color: '#000000',
    fontSize: 9,
    fontWeight: '800',
    lineHeight: 14,
  },
});
