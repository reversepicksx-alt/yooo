import { useEffect, useRef, useState } from 'react';
import { Alert, Platform } from 'react-native';
import { Stack } from 'expo-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { StatusBar } from 'expo-status-bar';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import Colors from '@/constants/colors';
import * as Notifications from 'expo-notifications';
import { registerPushToken } from '@/lib/api';
import LoadingScreen from '@/components/LoadingScreen';
import { initializeRevenueCat, setRevenueCatUserId, SubscriptionProvider } from '@/lib/revenuecat';

// Initialize RevenueCat once at module load (before any component renders)
try {
  initializeRevenueCat();
} catch (err: any) {
  console.warn('[RevenueCat] init error:', err?.message ?? err);
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30000 } },
});

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

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

        const tokenData = await Notifications.getExpoPushTokenAsync();
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

// Logs user into RevenueCat whenever they authenticate
function RevenueCatSync() {
  const { session } = useAuth();

  useEffect(() => {
    if (session?.email && Platform.OS !== 'web') {
      setRevenueCatUserId(session.email);
    }
  }, [session?.email]);

  return null;
}

function AppBoot() {
  const { isLoading } = useAuth();
  const [splashDone, setSplashDone] = useState(Platform.OS === 'web');
  // Enforce minimum 4.2s web splash so the logo spin + lightning always completes
  const webSplashStart = useRef(Date.now());
  const [webSplashReady, setWebSplashReady] = useState(false);

  useEffect(() => {
    if (Platform.OS !== 'web') return;
    if (isLoading) return;
    const elapsed = Date.now() - webSplashStart.current;
    const remaining = Math.max(0, 4000 - elapsed);
    const t = setTimeout(() => {
      if (typeof window !== 'undefined') {
        const hide = (window as any).__rpHideLoader;
        if (typeof hide === 'function') hide();
      }
      setWebSplashReady(true);
    }, remaining);
    return () => clearTimeout(t);
  }, [isLoading]);

  // On web: hold until auth done AND minimum splash time passed
  if (Platform.OS === 'web' && (!webSplashReady || isLoading)) {
    return null;
  }

  // Native: show the React animated loading screen until auth + fonts are ready.
  if (Platform.OS !== 'web' && (isLoading || !splashDone)) {
    return <LoadingScreen onDone={() => setSplashDone(true)} />;
  }

  return (
    <>
      <StatusBar style="light" />
      <PushRegistrar />
      <RevenueCatSync />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: Colors.background } }}>
        <Stack.Screen name="index" />
        <Stack.Screen name="auth" />
        <Stack.Screen name="(tabs)" />
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
