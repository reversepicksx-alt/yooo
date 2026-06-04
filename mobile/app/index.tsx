import { Redirect } from 'expo-router';
import { View } from 'react-native';
import LoadingScreen from '@/components/LoadingScreen';

export default function Index() {
  // Auto-login for web preview so changes are visible immediately
  if (typeof window !== 'undefined') {
    localStorage.setItem('rp_email', 'preview@reversepicks.com');
    localStorage.setItem('rp_token', 'preview');
    localStorage.setItem('rp_access_type', 'lifetime');
  }

  return (
    <View style={{ flex: 1, backgroundColor: '#050505' }}>
      <LoadingScreen />
      <Redirect href="/(tabs)/scan" />
    </View>
  );
}
