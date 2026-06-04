import { useEffect, useState } from 'react';
import { router } from 'expo-router';
import { useAuth } from '@/contexts/AuthContext';
import LoadingScreen from '@/components/LoadingScreen';

export default function Index() {
  const { session, isLoading } = useAuth();
  const [showLoading, setShowLoading] = useState(true);

  useEffect(() => {
    if (!isLoading) {
      // Show loading screen for at least 1.5s so the splash animation plays
      const timer = setTimeout(() => {
        setShowLoading(false);
        if (session) {
          router.replace('/(tabs)/scan');
        } else {
          router.replace('/auth');
        }
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [session, isLoading]);

  if (showLoading) {
    return (
      <LoadingScreen
        label="LOADING"
        statuses={[
          'INITIALIZING ENGINES',
          'LOADING PLAYER DATABASE',
          'CALIBRATING PROBABILITY MODELS',
          'READY',
        ]}
      />
    );
  }

  return null;
}
