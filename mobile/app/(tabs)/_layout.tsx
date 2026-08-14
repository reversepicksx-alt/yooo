import { Tabs, Redirect, router, usePathname } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { View, StyleSheet, Platform } from 'react-native';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import { useEffect, useRef } from 'react';
import LissaVoiceAssistant from '@/components/LissaVoiceAssistant';
import {
  LissaScreenContextProvider,
  useLissaScreenContext,
} from '@/contexts/LissaScreenContext';

function TabIcon({ name, focused }: { name: keyof typeof Ionicons.glyphMap; focused: boolean }) {
  return (
    <View style={[styles.iconWrap, focused && styles.iconWrapActive]}>
      <Ionicons name={name} size={22} color={focused ? Colors.primary : Colors.textTertiary} />
    </View>
  );
}

function GlobalLissa() {
  const { session } = useAuth();
  const { context } = useLissaScreenContext();
  const pathname = usePathname();
  const isOwner = session?.accessType?.toLowerCase() === 'owner';
  if (!isOwner || !session?.email || !session.token) return null;
  const screenName = pathname.includes('/picks')
    ? 'My Picks'
    : pathname.includes('/community')
      ? 'Community'
      : pathname.includes('/account')
        ? 'Account'
        : pathname.includes('/scan')
          ? 'Predict'
          : 'Reverse Picks';
  const screenContext = {
    name: screenName,
    path: pathname,
    description: screenName === 'Predict'
      ? 'The prediction workspace where Reverse selects a sport, player, prop, and line.'
      : screenName === 'My Picks'
        ? 'Saved live and historical picks, analysis, results, and owner performance.'
        : screenName === 'Community'
          ? 'The community feed for sharing and discussing picks.'
          : screenName === 'Account'
            ? 'The owner account, subscription, and profile settings.'
            : 'The main Reverse Picks workspace.',
  };
  // Tabs stay mounted for fast switching. A pick-analysis packet from My Picks
  // must never travel with Lissa onto Predict, Community, or Account.
  const effectiveContext = screenName === 'My Picks'
    ? { screen: screenContext, ...context }
    : { screen: screenContext };

  return (
    <View pointerEvents="box-none" style={styles.lissaOverlay}>
      <LissaVoiceAssistant
        minimal
        // Do not submit every two-word recognition fragment. Continuous
        // browser/iOS recognition can emit ambient speech and partial
        // segments; Lissa must be explicitly addressed before answering.
        requireWakeWord
        email={session.email}
        token={session.token}
        sessionId="global-lissa"
        context={effectiveContext}
      />
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
    <LissaScreenContextProvider>
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
          <Tabs.Screen name="chat" options={{ href: null }} />
          <Tabs.Screen
            name="account"
            options={{
              title: 'Account',
              tabBarIcon: ({ focused }) => <TabIcon name="person-circle" focused={focused} />,
            }}
          />
          <Tabs.Screen name="notifications" options={{ href: null }} />
        </Tabs>
        <GlobalLissa />
      </View>
    </LissaScreenContextProvider>
  );
}

const styles = StyleSheet.create({
  shell: { flex: 1 },
  lissaOverlay: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: Platform.OS === 'web' ? 82 : 94,
    alignItems: 'center',
    zIndex: 100,
    elevation: 100,
  },
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
