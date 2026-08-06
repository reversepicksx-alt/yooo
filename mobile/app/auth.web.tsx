import { useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useRouter } from 'expo-router';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import { createCheckout, verifyAccess } from '@/lib/api';

type Step = 'email' | 'pin' | 'pricing';

export default function WebAuthScreen() {
  const router = useRouter();
  const { loginWithResponse } = useAuth();
  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');
  const [pin, setPin] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const checkout = async (planKey: string) => {
    const normalized = email.trim().toLowerCase();
    if (!normalized) {
      setStep('email');
      setError('Enter your email address before choosing a plan.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const result = await createCheckout(normalized, planKey);
      const url = result.checkoutUrl || result.checkout_url || result.redirect_url;
      if (!url) throw new Error(result.error || 'Could not start checkout. Please try again.');
      try { window.sessionStorage.setItem('rp_checkout_email', normalized); } catch {}
      window.location.href = url;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start checkout. Please try again.');
      setLoading(false);
    }
  };

  const verify = async (accessPin?: string) => {
    const normalized = email.trim().toLowerCase();
    if (!normalized) {
      setError('Enter your email address.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const result = await verifyAccess(normalized, accessPin);
      if (result.owner_pin_required) {
        setStep('pin');
        setPin('');
      } else if (result.verified && result.session_token && result.email) {
        await loginWithResponse({
          email: result.email,
          session_token: result.session_token,
          access_type: result.access_type,
        });
        router.replace('/(tabs)/scan');
      } else {
        setError(result.message || result.denial_reason || 'No active membership found.');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not verify access. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  if (step === 'pricing') {
    return (
      <View style={styles.page}>
        <View style={styles.card}>
          <Image source={require('../assets/logo.png')} style={styles.logo} resizeMode="contain" />
          <Text style={styles.title}>SUBSCRIBE ON THE WEBSITE</Text>
          <Text style={styles.copy}>
            Choose a website plan below. Secure checkout is powered by Stripe.
          </Text>
          {!!error && <Text style={styles.error}>{error}</Text>}
          <Pressable style={[styles.primaryButton, loading && styles.disabled]} disabled={loading} onPress={() => checkout('monthly')}>
            {loading ? <ActivityIndicator color="#000" /> : <Text style={styles.primaryText}>MONTHLY · $39.99/MONTH</Text>}
          </Pressable>
          <Pressable style={styles.linkButton} onPress={() => setStep('email')}>
            <Text style={styles.linkText}>Back to Login</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.page}>
      <View style={styles.card}>
        <Image source={require('../assets/logo.png')} style={styles.logo} resizeMode="contain" />
        <Text style={styles.title}>REVERSEPICKS</Text>
        <Text style={styles.subtitle}>ELITE PROP INTELLIGENCE</Text>

        <View style={styles.form}>
          <TextInput
            style={styles.input}
            placeholder={step === 'pin' ? 'Access code' : 'Enter your email'}
            placeholderTextColor={Colors.textTertiary}
            value={step === 'pin' ? pin : email}
            onChangeText={step === 'pin' ? setPin : value => { setEmail(value); setError(''); }}
            keyboardType={step === 'pin' ? 'number-pad' : 'email-address'}
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry={step === 'pin'}
            onSubmitEditing={() => step === 'pin' ? verify(pin.trim()) : verify()}
          />
          {!!error && <Text style={styles.error}>{error}</Text>}
          <Pressable
            style={[styles.primaryButton, loading && styles.disabled]}
            disabled={loading}
            onPress={() => step === 'pin' ? verify(pin.trim()) : verify()}
          >
            {loading ? <ActivityIndicator color="#000" /> : (
              <Text style={styles.primaryText}>{step === 'pin' ? 'CONFIRM' : 'VERIFY ACCESS'}</Text>
            )}
          </Pressable>
        </View>

        {step === 'pin' ? (
          <Pressable style={styles.linkButton} onPress={() => { setStep('email'); setPin(''); setError(''); }}>
            <Text style={styles.linkText}>Use a different email</Text>
          </Pressable>
        ) : (
          <>
            <Pressable style={styles.linkButton} onPress={() => setStep('pricing')}>
              <Text style={styles.linkText}>Subscribe on the website</Text>
            </Pressable>
            <Pressable style={styles.linkButton} onPress={() => verify()}>
              <Text style={styles.mutedLink}>Already paid? Verify your payment</Text>
            </Pressable>
          </>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  page: {
    flex: 1,
    minHeight: '100vh' as any,
    width: '100%',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Colors.background,
    padding: 24,
  },
  card: { width: '100%', maxWidth: 440, alignItems: 'stretch' },
  logo: { width: 116, height: 116, alignSelf: 'center', marginBottom: 12 },
  title: { color: Colors.text, fontSize: 25, fontWeight: '900', letterSpacing: 5, textAlign: 'center' },
  subtitle: { color: Colors.primary, fontSize: 11, fontWeight: '700', letterSpacing: 3, textAlign: 'center', marginTop: 6, marginBottom: 30 },
  form: { gap: 12 },
  input: {
    height: 54,
    width: '100%',
    color: Colors.text,
    backgroundColor: Colors.card,
    borderColor: Colors.borderSubtle,
    borderWidth: 1,
    borderRadius: Colors.radius,
    paddingHorizontal: 16,
    fontSize: 16,
    outlineWidth: 0,
  } as any,
  primaryButton: {
    height: 54,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Colors.primary,
    borderRadius: Colors.radius,
  },
  primaryText: { color: '#000', fontSize: 14, fontWeight: '900', letterSpacing: 0.6 },
  disabled: { opacity: 0.6 },
  secondaryPlan: { marginTop: 10 },
  error: { color: Colors.error, fontSize: 13, textAlign: 'center' },
  copy: { color: Colors.textSecondary, fontSize: 14, lineHeight: 21, textAlign: 'center', marginBottom: 22 },
  linkButton: { alignItems: 'center', paddingVertical: 10 },
  linkText: { color: Colors.primary, fontSize: 13, textDecorationLine: 'underline' },
  mutedLink: { color: Colors.textSecondary, fontSize: 13, textDecorationLine: 'underline' },
});