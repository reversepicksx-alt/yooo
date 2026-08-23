import { Tabs, Redirect, router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { View, StyleSheet, Platform } from 'react-native';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import { useSubscription } from '@/lib/revenuecat';
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
  const { isSubscribed, isLoading: isSubscriptionLoading } = useSubscription();
  const redirectedRef = useRef(false);

  useEffect(() => {
    if (redirectedRef.current) return;
    if (!isLoading && !session) {
      redirectedRef.current = true;
      router.replace('/auth');
    }
    // StoreKit can report an active entitlement a moment after the authenticated
    // session is restored. Do not route a native customer to the paywall during
    // that window; ScanScreen and RevenueCatSync will reconcile the server
    // session once the entitlement is available.
    const nativeEntitlementPending = Platform.OS === 'ios'
      && (isSubscriptionLoading || isSubscribed);
    if (!isLoading && session && session.accessType === 'NoSubscription' && !nativeEntitlementPending) {
      redirectedRef.current = true;
      if (Platform.OS === 'web') {
        router.replace('/(tabs)/account');
      } else {
        router.replace('/paywall');
      }
    }
  }, [session, isLoading, isSubscribed, isSubscriptionLoading]);

  if (isLoading) return <View style={{ flex: 1, backgroundColor: '#050505' }} />;
  if (!session) return <Redirect href="/auth" />;

  return (
    <View style={styles.shell}>
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
            href: null,
          }}
        />
        <Tabs.Screen
          name="chat"
          options={{
            href: session.accessType?.toLowerCase() === 'owner' ? undefined : null,
            title: 'JARVIS',
            tabBarIcon: ({ focused }) => <TabIcon name="sparkles" focused={focused} />,
          }}
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
    </View>
  );
}

const styles = StyleSheet.create({
  shell: { flex: 1 },
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
