import { Tabs, router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { View, StyleSheet, Platform } from 'react-native';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import { useEffect } from 'react';

function TabIcon({ name, focused }: { name: keyof typeof Ionicons.glyphMap; focused: boolean }) {
  return (
    <View style={[styles.iconWrap, focused && styles.iconWrapActive]}>
      <Ionicons name={name} size={22} color={focused ? Colors.primary : Colors.textTertiary} />
    </View>
  );
}

export default function TabLayout() {
  const { session, isLoading } = useAuth();
  const isOwner = session?.accessType?.toLowerCase() === 'owner';

  useEffect(() => {
    if (!isLoading && !session) {
      router.replace('/auth');
    }
  }, [session, isLoading]);

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
        name="account"
        options={{
          title: 'Account',
          tabBarIcon: ({ focused }) => <TabIcon name="person-circle" focused={focused} />,
        }}
      />
      <Tabs.Screen name="analytics" options={{ href: null }} />
      <Tabs.Screen
        name="toptable"
        options={isOwner ? {
          title: 'Props Intel',
          tabBarIcon: ({ focused }) => <TabIcon name="grid" focused={focused} />,
        } : { href: null }}
      />
      <Tabs.Screen name="intel" options={{ href: null }} />
      <Tabs.Screen name="chat" options={{ href: null }} />
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
});
