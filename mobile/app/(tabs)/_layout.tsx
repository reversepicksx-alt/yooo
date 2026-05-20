import { Tabs, router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { View, Text, StyleSheet, Platform } from 'react-native';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import { useEffect, useState } from 'react';
import { getNotificationsUnreadCount } from '@/lib/api';

function TabIcon({ name, focused }: { name: keyof typeof Ionicons.glyphMap; focused: boolean }) {
  return (
    <View style={[styles.iconWrap, focused && styles.iconWrapActive]}>
      <Ionicons name={name} size={22} color={focused ? Colors.primary : Colors.textTertiary} />
    </View>
  );
}

function BadgeTabIcon({
  name, focused, badge,
}: { name: keyof typeof Ionicons.glyphMap; focused: boolean; badge: number }) {
  return (
    <View style={[styles.iconWrap, focused && styles.iconWrapActive]}>
      <Ionicons name={name} size={22} color={focused ? Colors.primary : Colors.textTertiary} />
      {badge > 0 && (
        <View style={styles.badge}>
          <Text style={styles.badgeText}>{badge > 99 ? '99+' : String(badge)}</Text>
        </View>
      )}
    </View>
  );
}

export default function TabLayout() {
  const { session, isLoading } = useAuth();
  const isOwner = session?.accessType?.toLowerCase() === 'owner';
  const [unreadCount, setUnreadCount] = useState(0);

  useEffect(() => {
    if (!isLoading && !session) {
      router.replace('/auth');
    }
  }, [session, isLoading]);

  // Poll for unread notification count every 30 s
  useEffect(() => {
    if (!session?.email) return;
    const fetchCount = async () => {
      try {
        const data = await getNotificationsUnreadCount(session.email);
        setUnreadCount(data?.count ?? 0);
      } catch {}
    };
    fetchCount();
    const interval = setInterval(fetchCount, 30_000);
    return () => clearInterval(interval);
  }, [session?.email]);

  if (isLoading) return null;
  if (!session) return null;

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor: '#000000',
          borderTopColor: Colors.tabBarBorder,
          borderTopWidth: 0.5,
          height: Platform.OS === 'web' ? 78 : 82,
          paddingBottom: Platform.OS === 'web' ? 18 : 26,
          paddingTop: 10,
        },
        tabBarActiveTintColor: Colors.primary,
        tabBarInactiveTintColor: Colors.textTertiary,
        tabBarLabelStyle: {
          fontSize: 10,
          fontWeight: '600',
          letterSpacing: 0.3,
          marginTop: 1,
        },
        tabBarHideOnKeyboard: false,
      }}
    >
      <Tabs.Screen
        name="scan"
        options={{
          title: 'Predict',
          tabBarIcon: ({ focused }) => <TabIcon name="scan" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="picks"
        options={{
          title: 'My Picks',
          tabBarIcon: ({ focused }) => <TabIcon name="bookmark" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="community"
        options={{
          title: 'Reverse Chat',
          tabBarIcon: ({ focused }) => <TabIcon name="people" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="notifications"
        options={{
          title: 'Alerts',
          tabBarIcon: ({ focused }) => (
            <BadgeTabIcon name="notifications" focused={focused} badge={unreadCount} />
          ),
        }}
      />
      <Tabs.Screen
        name="account"
        options={{
          title: 'Account',
          tabBarIcon: ({ focused }) => <TabIcon name="person-circle" focused={focused} />,
        }}
      />
      <Tabs.Screen name="analytics"    options={{ href: null }} />
      <Tabs.Screen name="toptable"     options={{ href: null }} />
      <Tabs.Screen name="intel"        options={{ href: null }} />
      <Tabs.Screen name="chat"         options={{ href: null }} />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  iconWrap: {
    width: 44,
    height: 30,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 10,
  },
  iconWrapActive: {
    backgroundColor: Colors.primaryGlow,
  },
  badge: {
    position: 'absolute',
    top: -3,
    right: -1,
    backgroundColor: Colors.primary,
    borderRadius: 8,
    minWidth: 16,
    height: 16,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 3,
  },
  badgeText: {
    color: '#000000',
    fontSize: 9,
    fontWeight: '800',
    lineHeight: 16,
  },
});
