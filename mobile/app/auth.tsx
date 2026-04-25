import { useState, useEffect } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  Platform, ActivityIndicator, Image, Linking,
} from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '@/contexts/AuthContext';
import Colors from '@/constants/colors';
import {
  verifyAccess, setPassword as apiSetPassword, authLogin, createCheckout, linkPayment,
} from '@/lib/api';

type Step = 'email' | 'pricing';

const INPUT_STYLE = Platform.OS === 'web' ? { outlineWidth: 0, outlineStyle: 'none' } : {};

const PLANS = [
  { key: 'weekly',    label: 'Weekly',   sub: 'Billed weekly',  price: '$11',    unit: '/week',  popular: false },
  { key: 'monthly',   label: 'Monthly',  sub: 'Save 9%',        price: '$39.99', unit: '/month', popular: true  },
  { key: 'quarterly', label: '3 Months', sub: 'Save 24%',       price: '$99.99', unit: '/3mo',   popular: false },
];

export default function AuthScreen() {
  const insets = useSafeAreaInsets();
  const { loginWithResponse } = useAuth();
  const params = useLocalSearchParams<{ stripe_success?: string }>();
  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [checkoutLoading, setCheckoutLoading] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [showPaymentEmail, setShowPaymentEmail] = useState(false);
  const [paymentEmail, setPaymentEmail] = useState('');

  // When Stripe redirects back with ?stripe_success=1, pre-fill the email
  // (saved before redirect) and auto-trigger verification so the user lands
  // in a logged-in state without any manual steps.
  useEffect(() => {
    const isSuccess =
      params.stripe_success === '1' ||
      (Platform.OS === 'web' &&
        typeof window !== 'undefined' &&
        window.location.search.includes('stripe_success=1'));

    if (!isSuccess) return;

    // Retrieve the email we saved right before the Stripe redirect
    let savedEmail = '';
    try {
      if (typeof window !== 'undefined' && window.sessionStorage) {
        savedEmail = window.sessionStorage.getItem('rp_checkout_email') || '';
      }
    } catch {}

    if (savedEmail) {
      setEmail(savedEmail);
      setInfo('✅ Payment confirmed! Verifying your access...');
      // Auto-trigger verification with a brief delay so state settles
      setTimeout(async () => {
        setLoading(true);
        try {
          const { verifyAccess } = await import('@/lib/api');
          const result = await verifyAccess(savedEmail);
          if (result.verified && result.session_token && result.email) {
            await loginWithResponse({
              email: result.email,
              session_token: result.session_token,
              access_type: result.access_type,
            });
            try { window.sessionStorage.removeItem('rp_checkout_email'); } catch {}
            await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
            router.replace('/(tabs)/scan');
          } else {
            setInfo('');
            setError('Payment received! Enter your email below and tap "Already paid?" to finish signing in.');
          }
        } catch {
          setInfo('');
          setError('Payment received! Enter your email below and tap "Already paid?" to finish signing in.');
        } finally {
          setLoading(false);
        }
      }, 400);
    } else {
      setInfo('✅ Payment complete! Enter the email you used at checkout, then tap "Already paid?" below.');
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleCheckEmail = async () => {
    const trimmed = email.trim().toLowerCase();
    if (!trimmed) { setError('Enter your email address.'); return; }
    setLoading(true);
    setError('');
    setInfo('');
    try {
      const result = await verifyAccess(trimmed);
      if (result.denied && result.denial_reason) {
        setError(result.denial_reason);
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      } else if (result.verified && result.session_token && result.email) {
        await loginWithResponse({
          email: result.email,
          session_token: result.session_token,
          access_type: result.access_type,
        });
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        router.replace('/(tabs)/scan');
      } else {
        setError(result.message || 'No active membership found. Subscribe below to get access.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to verify access.');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setLoading(false);
    }
  };

  const handleShowPricing = () => {
    const trimmed = email.trim().toLowerCase();
    if (!trimmed) { setError('Enter your email address first.'); return; }
    setError('');
    setInfo('');
    setStep('pricing');
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  };

  const handleSubscribePlan = async (planKey: string) => {
    const trimmed = email.trim().toLowerCase();
    setCheckoutLoading(planKey);
    setError('');
    try {
      const result = await createCheckout(trimmed, planKey);
      const url = result.checkoutUrl || result.checkout_url || result.redirect_url;
      if (url) {
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        if (Platform.OS === 'web' && typeof window !== 'undefined') {
          // Save email so we can pre-fill and auto-verify on Stripe redirect return
          try { window.sessionStorage.setItem('rp_checkout_email', trimmed); } catch {}
          window.location.href = url;
        } else {
          await Linking.openURL(url);
        }
        setInfo('Complete payment in the browser. If your card is declined, try a different card or use Cash App Pay / Link in the checkout. Then tap "Already paid?" below.');
        setStep('email');
      } else {
        setError(result.error || 'Could not create checkout. Try again.');
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Checkout failed. Try again.');
    } finally {
      setCheckoutLoading(null);
    }
  };

  const handleAlreadyPaid = async () => {
    const trimmed = email.trim().toLowerCase();
    if (!trimmed) { setError('Enter your email address.'); return; }

    // If user has entered a different payment email, use link-payment flow
    const payTrimmed = paymentEmail.trim().toLowerCase();
    if (showPaymentEmail && payTrimmed && payTrimmed !== trimmed) {
      setLoading(true);
      setError('');
      setInfo('');
      try {
        const result = await linkPayment(trimmed, payTrimmed);
        if (result.verified && result.session_token && result.email) {
          await loginWithResponse({
            email: result.email,
            session_token: result.session_token,
            access_type: result.access_type,
          });
          await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
          router.replace('/(tabs)/scan');
        } else {
          setError(result.message || 'No active subscription found for that payment email.');
          await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Could not verify. Check your connection and try again.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      } finally {
        setLoading(false);
      }
      return;
    }

    setLoading(true);
    setError('');
    setInfo('');
    try {
      const result = await verifyAccess(trimmed);
      if (result.verified && result.session_token && result.email) {
        await loginWithResponse({
          email: result.email,
          session_token: result.session_token,
          access_type: result.access_type,
        });
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        router.replace('/(tabs)/scan');
      } else {
        // Access not found — prompt for the email used at checkout
        setShowPaymentEmail(true);
        setError('No membership found. Enter the email you used at checkout below.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      }
    } catch {
      setShowPaymentEmail(true);
      setError('Could not verify. Enter the email you used at checkout below.');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setLoading(false);
    }
  };

  const goBack = () => {
    setStep('email');
    setError('');
    setInfo('');
  };

  if (step === 'pricing') {
    return (
      <View style={[styles.root, { paddingTop: insets.top, paddingBottom: insets.bottom }]}>
        <View style={styles.pricingContainer}>
          <View style={styles.pricingHero}>
            <Image source={require('../assets/logo.png')} style={styles.pricingLogo} resizeMode="contain" />
            <Text style={styles.pricingTitle}>CHOOSE YOUR PLAN</Text>
          </View>

          {!!error && <ErrorBox message={error} />}

          {PLANS.map(plan => (
            <TouchableOpacity
              key={plan.key}
              style={[styles.planCard, plan.popular && styles.planCardPopular]}
              onPress={() => handleSubscribePlan(plan.key)}
              disabled={checkoutLoading !== null}
              activeOpacity={0.8}
            >
              {plan.popular && (
                <View style={styles.popularBadge}>
                  <Text style={styles.popularText}>MOST POPULAR</Text>
                </View>
              )}
              <View style={styles.planLeft}>
                <Text style={styles.planName}>{plan.label}</Text>
                <Text style={styles.planSub}>{plan.sub}</Text>
              </View>
              <View style={styles.planRight}>
                {checkoutLoading === plan.key
                  ? <ActivityIndicator color={Colors.primary} size="small" />
                  : (
                    <View style={styles.priceRow}>
                      <Text style={styles.planPrice}>{plan.price}</Text>
                      <Text style={styles.planUnit}>{plan.unit}</Text>
                    </View>
                  )
                }
              </View>
            </TouchableOpacity>
          ))}

          <TouchableOpacity style={styles.backBtn} onPress={goBack} activeOpacity={0.8}>
            <Ionicons name="arrow-back" size={15} color={Colors.text} />
            <Text style={styles.backBtnText}>Back to Login</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.root, { paddingTop: insets.top, paddingBottom: insets.bottom + 20 }]}>
      <View style={styles.inner}>
        <View style={styles.hero}>
          <Image
            source={require('../assets/logo.png')}
            style={styles.logo}
            resizeMode="contain"
          />
          <Text style={styles.appName}>REVERSEPICKS</Text>
          <Text style={styles.tagline}>ELITE PROP INTELLIGENCE</Text>
        </View>

        <View style={styles.formArea}>
          {step === 'email' && (
            <View style={styles.card}>
              <View style={styles.inputRow}>
                <Ionicons name="mail-outline" size={17} color={Colors.textSecondary} style={styles.icon} />
                <TextInput
                  style={[styles.input, INPUT_STYLE]}
                  placeholder="Enter your email"
                  placeholderTextColor={Colors.textTertiary}
                  value={email}
                  onChangeText={v => { setEmail(v); setError(''); setInfo(''); }}
                  keyboardType="email-address"
                  autoCapitalize="none"
                  autoCorrect={false}
                  autoComplete="email"
                  textContentType="emailAddress"
                  onSubmitEditing={handleCheckEmail}
                  returnKeyType="done"
                />
              </View>

              {!!info && <InfoBox message={info} />}
              {!!error && <ErrorBox message={error} />}

              <TouchableOpacity
                style={[styles.btn, loading && styles.btnDisabled]}
                onPress={handleCheckEmail}
                disabled={loading}
                activeOpacity={0.85}
              >
                {loading
                  ? <ActivityIndicator color="#000" size="small" />
                  : (
                    <View style={styles.btnInner}>
                      <Ionicons name="flash" size={16} color="#000" />
                      <Text style={styles.btnText}>VERIFY ACCESS</Text>
                    </View>
                  )
                }
              </TouchableOpacity>

              <View style={styles.dividerRow}>
                <View style={styles.dividerLine} />
                <Text style={styles.dividerText}>Not a member yet?</Text>
                <View style={styles.dividerLine} />
              </View>

              <TouchableOpacity
                style={[styles.btn, styles.btnSubscribe]}
                onPress={handleShowPricing}
                activeOpacity={0.85}
              >
                <View style={styles.btnInner}>
                  <Ionicons name="card-outline" size={16} color="#000" />
                  <Text style={styles.btnText}>Subscribe Now</Text>
                </View>
              </TouchableOpacity>

              {showPaymentEmail && (
                <View style={styles.paymentEmailBlock}>
                  <Text style={styles.paymentEmailLabel}>What email did you use at checkout?</Text>
                  <View style={styles.inputRow}>
                    <Ionicons name="receipt-outline" size={17} color={Colors.textSecondary} style={styles.icon} />
                    <TextInput
                      style={[styles.input, INPUT_STYLE]}
                      placeholder="Payment email"
                      placeholderTextColor={Colors.textTertiary}
                      value={paymentEmail}
                      onChangeText={v => { setPaymentEmail(v); setError(''); }}
                      keyboardType="email-address"
                      autoCapitalize="none"
                      autoCorrect={false}
                      autoComplete="email"
                      textContentType="emailAddress"
                      onSubmitEditing={handleAlreadyPaid}
                      returnKeyType="go"
                    />
                  </View>
                </View>
              )}

              <TouchableOpacity onPress={handleAlreadyPaid} disabled={loading} style={styles.alreadyPaidRow}>
                {loading
                  ? <ActivityIndicator color={Colors.primary} size="small" />
                  : <Text style={styles.alreadyPaid}>
                      {showPaymentEmail ? 'Verify with payment email' : 'Already paid? Verify your payment'}
                    </Text>
                }
              </TouchableOpacity>
            </View>
          )}

        </View>
      </View>
    </View>
  );
}

function EmailBadge({ email, onBack }: { email: string; onBack: () => void }) {
  return (
    <TouchableOpacity style={styles.emailBadge} onPress={onBack} activeOpacity={0.7}>
      <Ionicons name="arrow-back" size={14} color={Colors.primary} />
      <Text style={styles.emailText} numberOfLines={1}>{email}</Text>
      <Ionicons name="pencil-outline" size={13} color={Colors.textSecondary} />
    </TouchableOpacity>
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <View style={styles.errorBox}>
      <Ionicons name="alert-circle-outline" size={15} color={Colors.error} />
      <Text style={styles.errorText}>{message}</Text>
    </View>
  );
}

function InfoBox({ message }: { message: string }) {
  return (
    <View style={styles.infoBox}>
      <Ionicons name="checkmark-circle-outline" size={15} color={Colors.primary} />
      <Text style={styles.infoText}>{message}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  inner: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  hero: {
    alignItems: 'center',
    marginBottom: 40,
  },
  logo: {
    width: 130,
    height: 130,
    marginBottom: 20,
  },
  appName: {
    fontSize: 24,
    fontWeight: '900',
    color: Colors.text,
    letterSpacing: 5,
  },
  tagline: {
    fontSize: 11,
    color: Colors.primary,
    letterSpacing: 3,
    textTransform: 'uppercase',
    fontWeight: '700',
    marginTop: 6,
  },
  formArea: {
    width: '100%',
  },
  card: { width: '100%', gap: 12 },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    paddingHorizontal: 14,
    height: 54,
  },
  icon: { marginRight: 10 },
  input: { flex: 1, color: Colors.text, fontSize: 15 },
  eye: { padding: 4 },
  btn: {
    backgroundColor: Colors.primary,
    borderRadius: Colors.radius,
    height: 54,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: Colors.primary,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.35,
    shadowRadius: 14,
    elevation: 8,
  },
  btnSubscribe: {},
  btnDisabled: { opacity: 0.6 },
  btnInner: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  btnText: { color: '#000', fontWeight: '800', fontSize: 15, letterSpacing: 0.5 },
  dividerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginVertical: 4,
  },
  dividerLine: { flex: 1, height: 1, backgroundColor: Colors.borderSubtle },
  dividerText: { color: Colors.textSecondary, fontSize: 12, fontWeight: '500' },
  alreadyPaidRow: { alignItems: 'center', paddingVertical: 6 },
  alreadyPaid: {
    color: Colors.primary,
    fontSize: 13,
    fontWeight: '600',
    textDecorationLine: 'underline',
  },
  paymentEmailBlock: { gap: 8 },
  paymentEmailLabel: {
    color: Colors.textSecondary,
    fontSize: 12,
    fontWeight: '500',
    textAlign: 'center',
  },
  forgotRow: { alignItems: 'center', paddingVertical: 6 },
  forgotText: {
    color: Colors.textSecondary,
    fontSize: 13,
    fontWeight: '500',
    textDecorationLine: 'underline',
  },
  emailBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  emailText: { flex: 1, color: Colors.text, fontSize: 14, fontWeight: '500' },
  setupNote: { color: Colors.textSecondary, fontSize: 13, textAlign: 'center' },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.errorDim,
    padding: 12,
    borderRadius: Colors.radius,
    gap: 8,
  },
  errorText: { color: Colors.error, fontSize: 13, flex: 1 },
  infoBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    backgroundColor: Colors.primaryDim,
    padding: 12,
    borderRadius: Colors.radius,
    gap: 8,
  },
  infoText: { color: Colors.primary, fontSize: 13, flex: 1 },
  pricingContainer: {
    flex: 1,
    paddingHorizontal: 24,
    paddingTop: 20,
    gap: 14,
  },
  pricingHero: { alignItems: 'center', marginBottom: 8 },
  pricingLogo: { width: 60, height: 60, marginBottom: 12 },
  pricingTitle: {
    fontSize: 13,
    fontWeight: '800',
    color: Colors.primary,
    letterSpacing: 3,
    textTransform: 'uppercase',
  },
  planCard: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    padding: 20,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  planCardPopular: {
    borderColor: Colors.primary,
    borderWidth: 1.5,
    shadowColor: Colors.primary,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.2,
    shadowRadius: 10,
    elevation: 6,
  },
  popularBadge: {
    position: 'absolute',
    top: -11,
    right: 16,
    backgroundColor: Colors.primary,
    borderRadius: 20,
    paddingHorizontal: 10,
    paddingVertical: 3,
  },
  popularText: { color: '#000', fontSize: 10, fontWeight: '800', letterSpacing: 0.5 },
  planLeft: { flex: 1 },
  planName: { fontSize: 18, fontWeight: '700', color: Colors.text, marginBottom: 3 },
  planSub: { fontSize: 12, color: Colors.textSecondary },
  planRight: { alignItems: 'flex-end' },
  priceRow: { flexDirection: 'row', alignItems: 'baseline', gap: 2 },
  planPrice: { fontSize: 24, fontWeight: '800', color: Colors.primary },
  planUnit: { fontSize: 13, color: Colors.textSecondary, fontWeight: '500' },
  backBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    height: 50,
    marginTop: 4,
  },
  backBtnText: { color: Colors.text, fontSize: 14, fontWeight: '600' },
});
