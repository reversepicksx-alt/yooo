import { useEffect, useState } from 'react';
import { Platform, View } from 'react-native';
import { Stack } from 'expo-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { StatusBar } from 'expo-status-bar';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import Colors from '@/constants/colors';
import * as Notifications from 'expo-notifications';
import Constants from 'expo-constants';
import { registerPushToken } from '@/lib/api';
import LoadingScreen from '@/components/LoadingScreen';
import { initializeRevenueCat, setRevenueCatUserId, SubscriptionProvider } from '@/lib/revenuecat';

try {
  initializeRevenueCat();
} catch (err: any) {
  console.warn('[RevenueCat] init error:', err?.message ?? err);
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30000 } },
});

try {
  if (Platform.OS !== 'web') {
    Notifications.setNotificationHandler({
      handleNotification: async () => ({
        shouldShowAlert: true,
        shouldPlaySound: true,
        shouldSetBadge: false,
        shouldShowBanner: true,
        shouldShowList: true,
      }),
    });
  }
} catch (err: any) {
  console.warn('[Notifications] handler setup error:', err?.message ?? err);
}

function PushRegistrar() {
  const { email } = useAuth();

  useEffect(() => {
    if (!email || Platform.OS === 'web') return;

    (async () => {
      try {
        const { status: existing } = await Notifications.getPermissionsAsync();
        let finalStatus = existing;
        if (existing !== 'granted') {
          const { status } = await Notifications.requestPermissionsAsync();
          finalStatus = status;
        }
        if (finalStatus !== 'granted') return;

        const projectId =
          Constants.expoConfig?.extra?.eas?.projectId ??
          'cb70df32-f8c3-4bbd-9190-fb9cfd8b1599';
        const tokenData = await Notifications.getExpoPushTokenAsync({ projectId });
        const token = tokenData.data;
        if (!token) return;

        await registerPushToken({ email, token, platform: Platform.OS });
        console.log('[Push] Token registered:', token.slice(0, 30) + '…');
      } catch (e) {
        console.warn('[Push] registration error:', e);
      }
    })();
  }, [email]);

  return null;
}

function RevenueCatSync() {
  const { session } = useAuth();

  useEffect(() => {
    if (session?.email && Platform.OS !== 'web') {
      setRevenueCatUserId(session.email);
    }
  }, [session?.email]);

  return null;
}

/** Dismiss the HTML loading overlay that the proxy injected before React mounted. */
function hideWebOverlay() {
  try {
    const hide = (window as any).__rpHideLoader;
    if (typeof hide === 'function') hide();
  } catch {}
}

function AppBoot() {
  const { isLoading } = useAuth();
  const [splashDone, setSplashDone] = useState(Platform.OS === 'web');

  // ── Web: hide the HTML overlay once auth resolves ─────────────────────────
  useEffect(() => {
    if (Platform.OS !== 'web') return;
    if (isLoading) return;
    // Delay so the auth-driven route transition fully commits before overlay fades.
    const t = setTimeout(hideWebOverlay, 1200);
    return () => clearTimeout(t);
  }, [isLoading]);

  // Hard-cap: if backend is unreachable and isLoading never clears, force-hide at 8s.
  useEffect(() => {
    if (Platform.OS !== 'web') return;
    const t = setTimeout(hideWebOverlay, 8000);
    return () => clearTimeout(t);
  }, []);

  // Native: show the React animated loading screen until auth + fonts are ready.
  if (Platform.OS !== 'web' && (isLoading || !splashDone)) {
    return <LoadingScreen onDone={() => setSplashDone(true)} />;
  }

  // Always render the full Stack on web immediately — the HTML overlay
  // (z-index 99999, injected by proxy.js) covers it until hideWebOverlay() fires.
  // On native this renders after the LoadingScreen finishes.
  return (
    <>
      <StatusBar style="light" />
      <PushRegistrar />
      <RevenueCatSync />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: Colors.background } }}>
        <Stack.Screen name="index" />
        <Stack.Screen name="auth" />
        <Stack.Screen name="paywall" options={{ gestureEnabled: false }} />
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="terms" />
        <Stack.Screen name="privacy" />
        <Stack.Screen name="dm" />
        <Stack.Screen name="dm-thread" />
      </Stack>
    </>
  );
}

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <SubscriptionProvider>
            <AppBoot />
          </SubscriptionProvider>
        </AuthProvider>
      </QueryClientProvider>
    </GestureHandlerRootView>
  );
}
