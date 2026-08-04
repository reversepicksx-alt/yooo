import { Tabs, Redirect, router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { View, StyleSheet, Platform } from 'react-native';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import { useEffect, useRef } from 'react';

function TabIcon({ name, focused }: { name: keyof typeof Ionicons.glyphMap; focused: boolean }) {
  return (
    <View style={[styles.iconWrap, focused && styles.iconWrapActive]}>
      <Ionicons name={name} size={22} color={focused ? Colors.primary : Colors.textTertiary} />
    </View>
  );
}

export default function TabLayout() {
  const { session, isLoading } = useAuth();
  // Guard: only redirect once per mount. Without this, a late-arriving
  // verify-session response (after the 4 s race timeout) can update session,
  // re-fire this effect, and kick an active subscriber to the paywall even
  // though they were already happily using the app on another tab.
  const redirectedRef = useRef(false);

  useEffect(() => {
    if (redirectedRef.current) return;
    if (!isLoading && !session) {
      redirectedRef.current = true;
      router.replace('/auth');
    }
    if (!isLoading && session && session.accessType === 'NoSubscription') {
      redirectedRef.current = true;
      if (Platform.OS === 'web') {
        router.replace('/(tabs)/account');
      } else {
        router.replace('/paywall');
      }
    }
  }, [session, isLoading]);

  if (isLoading) return <View style={{ flex: 1, backgroundColor: '#050505' }} />;
  if (!session) return <Redirect href="/auth" />;

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
        // Keep inactive tabs legible on the black mobile shell; Community
        // should not disappear when My Picks is selected.
        tabBarInactiveTintColor: Colors.textSecondary,
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
        name="matchups"
        options={{
          // Temporarily hidden for all users while the matchup history screen
          // is redesigned for performance. The route remains available for
          // development and can be restored by removing this href override.
          href: null,
        }}
      />
      <Tabs.Screen
        name="community"
        options={{
          title: 'Community',
          tabBarIcon: ({ focused }) => <TabIcon name="people" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="chat"
        options={{ href: null }}
      />
      <Tabs.Screen
        name="account"
        options={{
          title: 'Account',
          tabBarIcon: ({ focused }) => <TabIcon name="person-circle" focused={focused} />,
        }}
      />
      <Tabs.Screen name="notifications" options={{ href: null }} />
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
