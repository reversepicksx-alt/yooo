import { useEffect } from 'react';
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

  useEffect(() => {
    if (Platform.OS === 'web' && typeof window !== 'undefined' && !isLoading) {
      const hide = (window as any).__hideSplash;
      if (typeof hide === 'function') hide();
    }
  }, [isLoading]);

  if (Platform.OS !== 'web' && isLoading) {
    return <LoadingScreen />;
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
