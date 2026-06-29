import { Redirect } from 'expo-router';
import { Platform } from 'react-native';
import { useAuth } from '@/contexts/AuthContext';

export default function Index() {
  const { session, isLoading } = useAuth();
  if (isLoading) return null;
  if (!session) return <Redirect href="/auth" />;
  // Hard paywall is native-only (iOS App Store / RevenueCat). The website is
  // Stripe-only, so web users go straight into the app (Stripe handled in Account).
  if (Platform.OS !== 'web' && session.accessType === 'NoSubscription') {
    return <Redirect href="/paywall" />;
  }
  return <Redirect href="/(tabs)/scan" />;
}
