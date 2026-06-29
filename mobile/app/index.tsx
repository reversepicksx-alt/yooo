import { Redirect } from 'expo-router';
import { useAuth } from '@/contexts/AuthContext';

export default function Index() {
  const { session, isLoading } = useAuth();
  if (isLoading) return null;
  if (!session) return <Redirect href="/auth" />;
  if (session.accessType === 'NoSubscription') return <Redirect href="/paywall" />;
  return <Redirect href="/(tabs)/scan" />;
}
