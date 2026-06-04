import { useEffect, useState } from 'react';
import { router } from 'expo-router';
import { View } from 'react-native';
import LoadingScreen from '@/components/LoadingScreen';

export default function Index() {
  const [canNav, setCanNav] = useState(false);

  useEffect(() => {
    // Auto-login for web preview so changes are visible immediately
    if (typeof window !== 'undefined') {
      localStorage.setItem('rp_email', 'preview@reversepicks.com');
      localStorage.setItem('rp_token', 'preview');
      localStorage.setItem('rp_access_type', 'lifetime');
    }
    // Wait for navigator to be ready before redirect
    const id = setTimeout(() => setCanNav(true), 600);
    return () => clearTimeout(id);
  }, []);

  useEffect(() => {
    if (canNav) {
      router.replace('/(tabs)/scan');
    }
  }, [canNav]);

  return (
    <View style={{ flex: 1, backgroundColor: '#050505' }}>
      <LoadingScreen />
    </View>
  );
}
