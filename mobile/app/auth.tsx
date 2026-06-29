import { useState, useEffect, useRef } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  Platform, ActivityIndicator, Image, Linking, Modal, ScrollView, KeyboardAvoidingView,
} from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '@/contexts/AuthContext';
import Colors from '@/constants/colors';
import {
  verifyAccess, requestCode, verifyCode, createCheckout, linkPayment, contactSupport,
} from '@/lib/api';

type Step = 'landing' | 'email' | 'otp' | 'pricing';
type Mode = 'signin' | 'signup';

const INPUT_STYLE = Platform.OS === 'web' ? { outlineWidth: 0 } as object : {};

const PLANS = [
  { key: 'weekly',    label: 'Weekly',   sub: 'Billed weekly',  price: '$15',    unit: '/week',  popular: false },
  { key: 'monthly',   label: 'Monthly',  sub: 'Save 8%',        price: '$49.99', unit: '/month', popular: true  },
  { key: 'quarterly', label: '3 Months', sub: 'Save 24%',       price: '$99.99', unit: '/3mo',   popular: false },
];

export default function AuthScreen() {
  const insets = useSafeAreaInsets();
  const { loginWithResponse } = useAuth();
  const params = useLocalSearchParams<{ stripe_success?: string }>();

  const [step, setStep] = useState<Step>('landing');
  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [otp, setOtp] = useState('');
  const [loading, setLoading] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [resendTimer, setResendTimer] = useState(0);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [checkoutLoading, setCheckoutLoading] = useState<string | null>(null);

  const [showPaymentEmail, setShowPaymentEmail] = useState(false);
  const [paymentEmail, setPaymentEmail] = useState('');

  // Support modal state
  const [showSupport, setShowSupport] = useState(false);
  const [supportName, setSupportName] = useState('');
  const [supportMessage, setSupportMessage] = useState('');
  const [supportSent, setSupportSent] = useState(false);
  const [supportLoading, setSupportLoading] = useState(false);
  const [supportError, setSupportError] = useState('');

  // ── Stripe redirect auto-login ──────────────────────────────────────────
  useEffect(() => {
    const isSuccess =
      params.stripe_success === '1' ||
      (Platform.OS === 'web' &&
        typeof window !== 'undefined' &&
        window.location.search.includes('stripe_success=1'));
    if (!isSuccess) return;

    let savedEmail = '';
    try {
      if (typeof window !== 'undefined' && window.sessionStorage) {
        savedEmail = window.sessionStorage.getItem('rp_checkout_email') || '';
      }
    } catch {}

    if (savedEmail) {
      setEmail(savedEmail);
      setInfo('Payment confirmed! Verifying your access...');
      setTimeout(async () => {
        setLoading(true);
        try {
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
      setInfo('Payment complete! Enter the email you used at checkout, then tap "Already paid?" below.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Resend timer ────────────────────────────────────────────────────────
  useEffect(() => {
    if (resendTimer <= 0) return;
    const t = setTimeout(() => setResendTimer(r => r - 1), 1000);
    return () => clearTimeout(t);
  }, [resendTimer]);

  // ── Handlers ────────────────────────────────────────────────────────────
  const handleSendCode = async () => {
    const trimmed = email.trim().toLowerCase();
    if (!trimmed || !trimmed.includes('@')) {
      setError('Enter a valid email address.');
      return;
    }
    setLoading(true);
    setError('');
    setInfo('');
    try {
      const result = await requestCode(trimmed);
      if (result.sent) {
        setCodeSent(true);
        setResendTimer(60);
        setOtp('');
        setStep('otp');
        setInfo('Code sent \u2014 check your inbox and spam folder.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      } else {
        setError(result.message || 'Could not send code. Try again.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to send code.');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async () => {
    const trimmed = email.trim().toLowerCase();
    const code = otp.trim();
    if (!code || code.length < 4) {
      setError('Enter the code from your email.');
      return;
    }
    setLoading(true);
    setError('');
    setInfo('');
    try {
      const result = await verifyCode(trimmed, code);
      if (result.access_type === 'NoSubscription' || !result.has_access) {
        // No active subscription
        await loginWithResponse({
          email: result.email,
          session_token: result.session_token,
          access_type: result.access_type,
        });
        if (mode === 'signup') {
          // New user: take them to plans
          setStep('pricing');
        } else {
          // Existing member thought they had access
          setError('No active subscription found. Choose a plan to get access.');
          setTimeout(() => setStep('pricing'), 800);
        }
      } else {
        // Has active subscription
        await loginWithResponse({
          email: result.email,
          session_token: result.session_token,
          access_type: result.access_type,
        });
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        router.replace('/(tabs)/scan');
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Invalid or expired code. Try again.');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async () => {
    if (resendTimer > 0) return;
    await handleSendCode();
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
          try { window.sessionStorage.setItem('rp_checkout_email', trimmed); } catch {}
          window.location.href = url;
        } else {
          await Linking.openURL(url);
        }
        setInfo('Complete payment in the browser, then tap "Already paid?" below.');
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
    const payTrimmed = paymentEmail.trim().toLowerCase();
    if (showPaymentEmail && payTrimmed && payTrimmed !== trimmed) {
      setLoading(true);
      setError('');
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
        setError(e instanceof Error ? e.message : 'Could not verify. Try again.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      } finally {
        setLoading(false);
      }
      return;
    }
    setLoading(true);
    setError('');
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

  const handleSendSupport = async () => {
    const message = supportMessage.trim();
    if (!message) return;
    setSupportLoading(true);
    setSupportError('');
    try {
      const result = await contactSupport(supportName.trim(), email.trim(), message);
      if (result.success) {
        setSupportSent(true);
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      } else {
        setSupportError(result.error || 'Failed to send. Please try again.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      }
    } catch {
      setSupportError('Could not send message. Check your connection and try again.');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setSupportLoading(false);
    }
  };

  // ── Pricing page ────────────────────────────────────────────────────────
  if (step === 'pricing') {
    const FEATURES = [
      { icon: 'flash' as any,              text: 'AI-powered player prop predictions' },
      { icon: 'analytics' as any,           text: 'Bayesian probability engine' },
      { icon: 'scan' as any,               text: 'Scan any sportsbook slip instantly' },
      { icon: 'chatbubble-ellipses' as any, text: 'Tactical AI chat + live intel' },
    ];
    return (
      <ScrollView
        style={[styles.root, { paddingTop: insets.top }]}
        contentContainerStyle={{ paddingBottom: insets.bottom + 20 }}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.pricingContainer}>
          <View style={styles.pricingHero}>
            <Image source={require('../assets/logo.png')} style={styles.pricingLogo} resizeMode="contain" />
            <Text style={styles.pricingTitle}>UNLOCK THE EDGE</Text>
            <Text style={styles.pricingTagline}>
              Data-driven soccer prop analysis trusted by sharp bettors.
            </Text>
          </View>

          <View style={styles.featuresList}>
            {FEATURES.map((f, i) => (
              <View key={i} style={styles.featureRow}>
                <View style={styles.featureIcon}>
                  <Ionicons name={f.icon} size={14} color={Colors.primary} />
                </View>
                <Text style={styles.featureText}>{f.text}</Text>
              </View>
            ))}
          </View>

          {!!error && <ErrorBox message={error} />}

          <Text style={styles.planSectionLabel}>CHOOSE YOUR PLAN</Text>

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

          <View style={styles.socialProofRow}>
            <Ionicons name="shield-checkmark" size={13} color={Colors.primary} />
            <Text style={styles.socialProofText}>Cancel anytime \u00b7 Instant access \u00b7 Secure checkout</Text>
          </View>

          <TouchableOpacity style={styles.backBtn} onPress={() => setStep('landing')} activeOpacity={0.8}>
            <Ionicons name="arrow-back" size={15} color={Colors.text} />
            <Text style={styles.backBtnText}>Back</Text>
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
      </ScrollView>
    );
  }

  // ── Main auth screen (landing / email / otp) ───────────────────────────
  return (
    <View style={[styles.root, { paddingTop: insets.top, paddingBottom: insets.bottom + 20 }]}>
      <View style={styles.inner}>

        {/* Logo (always visible) */}
        <View style={styles.hero}>
          <Image source={require('../assets/logo.png')} style={styles.logo} resizeMode="contain" />
          <Text style={styles.appName}>REVERSE PICKS</Text>
          <Text style={styles.tagline}>AI Player Props Analytics</Text>
        </View>

        {/* ─── LANDING ─── */}
        {step === 'landing' && (
          <View style={styles.landingCard}>
            <Text style={styles.landingHead}>Welcome</Text>
            <Text style={styles.landingSub}>Trusted by 2,000+ sports bettors</Text>

            <TouchableOpacity
              style={styles.landingBtnPrimary}
              onPress={() => { setMode('signin'); setStep('email'); setError(''); setInfo(''); }}
              activeOpacity={0.85}
            >
              <Ionicons name="log-in-outline" size={18} color="#000" />
              <Text style={styles.landingBtnText}>Already a member? Sign In</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.landingBtnSecondary}
              onPress={() => { setMode('signup'); setStep('email'); setError(''); setInfo(''); }}
              activeOpacity={0.85}
            >
              <Ionicons name="person-add-outline" size={18} color={Colors.primary} />
              <Text style={styles.landingBtnTextSecondary}>New here? Sign Up</Text>
            </TouchableOpacity>

            <View style={styles.landingFooter}>
              <Text style={styles.landingFooterText}>
                By continuing you agree to our{' '}
                <Text style={styles.landingFooterLink} onPress={() => Linking.openURL('https://reversepicks.com/terms')}>Terms</Text>
                {' '}&{' '}
                <Text style={styles.landingFooterLink} onPress={() => Linking.openURL('https://reversepicks.com/privacy')}>Privacy Policy</Text>
              </Text>
            </View>
          </View>
        )}

        {/* ─── EMAIL ─── */}
        {step === 'email' && (
          <View style={styles.authCard}>
            <Text style={styles.authHeading}>
              {mode === 'signin' ? 'Sign In' : 'Sign Up'}
            </Text>
            <Text style={styles.authSub}>
              Enter your email to receive a secure login code
            </Text>

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
                onSubmitEditing={handleSendCode}
                returnKeyType="done"
              />
            </View>

            {!!info && <InfoBox message={info} />}
            {!!error && <ErrorBox message={error} />}

            <TouchableOpacity
              style={[styles.btn, loading && styles.btnDisabled]}
              onPress={handleSendCode}
              disabled={loading}
              activeOpacity={0.85}
            >
              {loading
                ? <ActivityIndicator color="#000" size="small" />
                : (
                  <View style={styles.btnInner}>
                    <Ionicons name="flash" size={16} color="#000" />
                    <Text style={styles.btnText}>SEND CODE</Text>
                  </View>
                )
              }
            </TouchableOpacity>

            <View style={styles.authToggleRow}>
              <Text style={styles.authToggleText}>
                {mode === 'signin' ? "New here? " : "Already a member? "}
              </Text>
              <TouchableOpacity
                onPress={() => {
                  setMode(mode === 'signin' ? 'signup' : 'signin');
                  setError(''); setInfo('');
                }}
              >
                <Text style={styles.authToggleLink}>
                  {mode === 'signin' ? 'Sign Up' : 'Sign In'}
                </Text>
              </TouchableOpacity>
            </View>

            <TouchableOpacity onPress={() => setStep('landing')} style={styles.authBackRow}>
              <Ionicons name="arrow-back" size={14} color={Colors.textSecondary} />
              <Text style={styles.authBackText}>Back</Text>
            </TouchableOpacity>
          </View>
        )}

        {/* ─── OTP ─── */}
        {step === 'otp' && (
          <View style={styles.authCard}>
            <Text style={styles.authHeading}>Verify Code</Text>
            <Text style={styles.authSub}>
              Enter the code sent to{' '}
              <Text style={styles.authSubEmail}>{email}</Text>
            </Text>

            <View style={styles.inputRow}>
              <Ionicons name="key-outline" size={17} color={Colors.textSecondary} style={styles.icon} />
              <TextInput
                style={[styles.input, INPUT_STYLE]}
                placeholder="6-digit code"
                placeholderTextColor={Colors.textTertiary}
                value={otp}
                onChangeText={v => { setOtp(v.replace(/\D/g, '').slice(0, 8)); setError(''); setInfo(''); }}
                keyboardType="number-pad"
                autoCapitalize="none"
                autoCorrect={false}
                maxLength={8}
                onSubmitEditing={handleVerifyOtp}
                returnKeyType="done"
              />
            </View>

            {!!info && <InfoBox message={info} />}
            {!!error && <ErrorBox message={error} />}

            <TouchableOpacity
              style={[styles.btn, loading && styles.btnDisabled]}
              onPress={handleVerifyOtp}
              disabled={loading}
              activeOpacity={0.85}
            >
              {loading
                ? <ActivityIndicator color="#000" size="small" />
                : (
                  <View style={styles.btnInner}>
                    <Ionicons name="shield-checkmark-outline" size={16} color="#000" />
                    <Text style={styles.btnText}>VERIFY</Text>
                  </View>
                )
              }
            </TouchableOpacity>

            <TouchableOpacity
              onPress={handleResend}
              disabled={resendTimer > 0 || loading}
              style={styles.resendRow}
              activeOpacity={0.7}
            >
              <Text style={[styles.resendText, resendTimer > 0 && styles.resendTextDisabled]}>
                {resendTimer > 0
                  ? `Resend code in ${resendTimer}s`
                  : 'Didn\'t get it? Resend code'}
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              onPress={() => { setStep('email'); setOtp(''); setError(''); setInfo(''); }}
              style={styles.authBackRow}
            >
              <Ionicons name="arrow-back" size={14} color={Colors.textSecondary} />
              <Text style={styles.authBackText}>Change email</Text>
            </TouchableOpacity>
          </View>
        )}

      </View>

      {/* ─── Support Modal ─── */}
      <Modal
        visible={showSupport}
        transparent
        animationType="slide"
        onRequestClose={() => setShowSupport(false)}
      >
        <KeyboardAvoidingView
          style={styles.modalOverlay}
          behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        >
          <View style={styles.supportModal}>
            <View style={styles.supportModalHeader}>
              <Ionicons name="headset-outline" size={20} color={Colors.primary} />
              <Text style={styles.supportModalTitle}>Contact Support</Text>
              <TouchableOpacity onPress={() => setShowSupport(false)} style={styles.supportCloseBtn} activeOpacity={0.7}>
                <Ionicons name="close" size={20} color={Colors.textSecondary} />
              </TouchableOpacity>
            </View>

            {supportSent ? (
              <View style={styles.supportSentWrap}>
                <Ionicons name="checkmark-circle" size={48} color={Colors.primary} />
                <Text style={styles.supportSentTitle}>Message Sent!</Text>
                <Text style={styles.supportSentSub}>
                  We received your message and will get back to you at reversepicksx@gmail.com as soon as possible.
                </Text>
                <TouchableOpacity
                  style={[styles.supportSendBtn, { marginTop: 20 }]}
                  onPress={() => setShowSupport(false)}
                  activeOpacity={0.85}
                >
                  <Text style={styles.supportSendBtnText}>Done</Text>
                </TouchableOpacity>
              </View>
            ) : (
              <ScrollView keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
                <Text style={styles.supportSubtitle}>
                  We'll get back to you at reversepicksx@gmail.com as soon as possible.
                </Text>

                <Text style={styles.supportLabel}>Your Name (optional)</Text>
                <View style={styles.supportInputRow}>
                  <Ionicons name="person-outline" size={16} color={Colors.textSecondary} style={styles.supportInputIcon} />
                  <TextInput
                    style={[styles.supportInput, INPUT_STYLE]}
                    placeholder="e.g. John Doe"
                    placeholderTextColor={Colors.textTertiary}
                    value={supportName}
                    onChangeText={setSupportName}
                    autoCapitalize="words"
                    autoCorrect={false}
                  />
                </View>

                <Text style={styles.supportLabel}>Your Email</Text>
                <View style={styles.supportInputRow}>
                  <Ionicons name="mail-outline" size={16} color={Colors.textSecondary} style={styles.supportInputIcon} />
                  <TextInput
                    style={[styles.supportInput, INPUT_STYLE]}
                    placeholder="your@email.com"
                    placeholderTextColor={Colors.textTertiary}
                    value={email}
                    onChangeText={v => { setEmail(v); setError(''); }}
                    keyboardType="email-address"
                    autoCapitalize="none"
                    autoCorrect={false}
                  />
                </View>

                <Text style={styles.supportLabel}>Message</Text>
                <TextInput
                  style={[styles.supportTextArea, INPUT_STYLE]}
                  placeholder="Describe your issue or question..."
                  placeholderTextColor={Colors.textTertiary}
                  value={supportMessage}
                  onChangeText={setSupportMessage}
                  multiline
                  numberOfLines={5}
                  textAlignVertical="top"
                  autoCorrect
                />

                {!!supportError && (
                  <View style={styles.supportErrorBox}>
                    <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                    <Text style={styles.supportErrorText}>{supportError}</Text>
                  </View>
                )}

                <TouchableOpacity
                  style={[styles.supportSendBtn, (!supportMessage.trim() || supportLoading) && styles.supportSendBtnDisabled]}
                  onPress={handleSendSupport}
                  disabled={!supportMessage.trim() || supportLoading}
                  activeOpacity={0.85}
                >
                  {supportLoading ? (
                    <ActivityIndicator color="#000" size="small" />
                  ) : (
                    <>
                      <Ionicons name="send-outline" size={16} color="#000" />
                      <Text style={styles.supportSendBtnText}>Send Message</Text>
                    </>
                  )}
                </TouchableOpacity>
              </ScrollView>
            )}
          </View>
        </KeyboardAvoidingView>
      </Modal>

      {/* Support link at bottom */}
      <View style={styles.supportRow}>
        <View style={styles.dividerLine} />
        <TouchableOpacity
          onPress={() => { setSupportSent(false); setSupportName(''); setSupportMessage(''); setSupportError(''); setShowSupport(true); }}
          activeOpacity={0.7}
        >
          <Text style={styles.supportLink}>Contact Support</Text>
        </TouchableOpacity>
        <View style={styles.dividerLine} />
      </View>
    </View>
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
  root: { flex: 1, backgroundColor: Colors.background },
  inner: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 24 },
  hero: { alignItems: 'center', marginBottom: 40 },
  logo: { width: 110, height: 110, marginBottom: 16 },
  appName: { fontSize: 22, fontWeight: '900', color: Colors.text, letterSpacing: 4 },
  tagline: { fontSize: 11, color: Colors.primary, letterSpacing: 2.5, textTransform: 'uppercase', fontWeight: '700', marginTop: 6 },

  // Landing
  landingCard: { width: '100%', gap: 14, alignItems: 'center' },
  landingHead: { fontSize: 20, fontWeight: '800', color: Colors.text },
  landingSub: { fontSize: 13, color: Colors.textSecondary, marginTop: -6 },
  landingBtnPrimary: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
    backgroundColor: Colors.primary, borderRadius: Colors.radius, height: 54, width: '100%',
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.35, shadowRadius: 14, elevation: 8,
  },
  landingBtnSecondary: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
    backgroundColor: Colors.card, borderRadius: Colors.radius, height: 54, width: '100%',
    borderWidth: 1, borderColor: Colors.border,
  },
  landingBtnText: { color: '#000', fontWeight: '800', fontSize: 15, letterSpacing: 0.5 },
  landingBtnTextSecondary: { color: Colors.primary, fontWeight: '700', fontSize: 15, letterSpacing: 0.5 },
  landingFooter: { marginTop: 8 },
  landingFooterText: { color: Colors.textSecondary, fontSize: 11, textAlign: 'center', lineHeight: 17 },
  landingFooterLink: { color: Colors.primary, fontWeight: '600' },

  // Auth card (email / otp)
  authCard: { width: '100%', gap: 14 },
  authHeading: { fontSize: 24, fontWeight: '800', color: Colors.text, textAlign: 'center' },
  authSub: { fontSize: 13, color: Colors.textSecondary, textAlign: 'center', lineHeight: 19, marginTop: -6 },
  authSubEmail: { color: Colors.primary, fontWeight: '600' },
  authToggleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 4 },
  authToggleText: { color: Colors.textSecondary, fontSize: 13 },
  authToggleLink: { color: Colors.primary, fontSize: 13, fontWeight: '700' },
  authBackRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingVertical: 4 },
  authBackText: { color: Colors.textSecondary, fontSize: 13, fontWeight: '500' },
  resendRow: { alignItems: 'center', paddingVertical: 4 },
  resendText: { color: Colors.primary, fontSize: 13, fontWeight: '600' },
  resendTextDisabled: { color: Colors.textTertiary },

  // Inputs
  inputRow: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: Colors.card, borderRadius: Colors.radius,
    borderWidth: 1, borderColor: Colors.borderSubtle,
    paddingHorizontal: 14, height: 54,
  },
  icon: { marginRight: 10 },
  input: { flex: 1, color: Colors.text, fontSize: 16 },

  // Buttons
  btn: {
    backgroundColor: Colors.primary, borderRadius: Colors.radius, height: 54,
    alignItems: 'center', justifyContent: 'center',
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.35, shadowRadius: 14, elevation: 8,
  },
  btnDisabled: { opacity: 0.6 },
  btnInner: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  btnText: { color: '#000', fontWeight: '800', fontSize: 15, letterSpacing: 0.5 },

  // Messages
  errorBox: { flexDirection: 'row', alignItems: 'center', backgroundColor: Colors.errorDim, padding: 12, borderRadius: Colors.radius, gap: 8 },
  errorText: { color: Colors.error, fontSize: 13, flex: 1 },
  infoBox: { flexDirection: 'row', alignItems: 'flex-start', backgroundColor: Colors.primaryDim, padding: 12, borderRadius: Colors.radius, gap: 8 },
  infoText: { color: Colors.primary, fontSize: 13, flex: 1 },

  // Pricing page
  pricingContainer: { flex: 1, paddingHorizontal: 24, paddingTop: 20, gap: 14 },
  pricingHero: { alignItems: 'center', marginBottom: 8 },
  pricingLogo: { width: 60, height: 60, marginBottom: 12 },
  pricingTitle: { fontSize: 18, fontWeight: '900', color: Colors.primary, letterSpacing: 3, textTransform: 'uppercase', marginBottom: 6 },
  pricingTagline: { fontSize: 13, color: Colors.textSecondary, textAlign: 'center', lineHeight: 19, paddingHorizontal: 12 },
  featuresList: { backgroundColor: Colors.card, borderRadius: Colors.radius, borderWidth: 1, borderColor: Colors.borderSubtle, padding: 14, gap: 10 },
  featureRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  featureIcon: { width: 26, height: 26, borderRadius: 8, backgroundColor: Colors.primaryDim, alignItems: 'center', justifyContent: 'center' },
  featureText: { fontSize: 13, color: Colors.text, fontWeight: '500', flex: 1 },
  planSectionLabel: { fontSize: 10, fontWeight: '800', color: Colors.textSecondary, letterSpacing: 2, textTransform: 'uppercase', textAlign: 'center' },
  socialProofRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: -4 },
  socialProofText: { fontSize: 11, color: Colors.textSecondary, textAlign: 'center' },
  planCard: { backgroundColor: Colors.card, borderRadius: Colors.radiusLg, borderWidth: 1, borderColor: Colors.borderSubtle, padding: 20, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  planCardPopular: { borderColor: Colors.primary, borderWidth: 1.5, shadowColor: Colors.primary, shadowOffset: { width: 0, height: 0 }, shadowOpacity: 0.2, shadowRadius: 10, elevation: 6 },
  popularBadge: { position: 'absolute', top: -11, right: 16, backgroundColor: Colors.primary, borderRadius: 20, paddingHorizontal: 10, paddingVertical: 3 },
  popularText: { color: '#000', fontSize: 10, fontWeight: '800', letterSpacing: 0.5 },
  planLeft: { flex: 1 },
  planName: { fontSize: 18, fontWeight: '700', color: Colors.text, marginBottom: 3 },
  planSub: { fontSize: 12, color: Colors.textSecondary },
  planRight: { alignItems: 'flex-end' },
  priceRow: { flexDirection: 'row', alignItems: 'baseline', gap: 2 },
  planPrice: { fontSize: 24, fontWeight: '800', color: Colors.primary },
  planUnit: { fontSize: 13, color: Colors.textSecondary, fontWeight: '500' },
  backBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: Colors.card, borderRadius: Colors.radius, borderWidth: 1, borderColor: Colors.borderSubtle, height: 50, marginTop: 4 },
  backBtnText: { color: Colors.text, fontSize: 14, fontWeight: '600' },

  // Already paid / payment email
  alreadyPaidRow: { alignItems: 'center', paddingVertical: 6 },
  alreadyPaid: { color: Colors.primary, fontSize: 13, fontWeight: '600', textDecorationLine: 'underline' },
  paymentEmailBlock: { gap: 8 },
  paymentEmailLabel: { color: Colors.textSecondary, fontSize: 12, fontWeight: '500', textAlign: 'center' },

  // Support
  supportRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 4, paddingHorizontal: 24 },
  supportLink: { color: Colors.textSecondary, fontSize: 12, fontWeight: '500', textDecorationLine: 'underline' },
  dividerLine: { flex: 1, height: 1, backgroundColor: Colors.borderSubtle },
  modalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.75)', justifyContent: 'flex-end' },
  supportModal: { backgroundColor: '#111', borderTopLeftRadius: 20, borderTopRightRadius: 20, borderWidth: 1, borderColor: Colors.border, padding: 24, paddingBottom: 40, maxHeight: '90%' },
  supportModalHeader: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 16 },
  supportModalTitle: { flex: 1, color: Colors.text, fontSize: 17, fontWeight: '700', letterSpacing: 0.3 },
  supportCloseBtn: { padding: 4 },
  supportSubtitle: { color: Colors.textSecondary, fontSize: 13, lineHeight: 19, marginBottom: 20 },
  supportLabel: { color: Colors.textSecondary, fontSize: 12, fontWeight: '600', letterSpacing: 0.5, textTransform: 'uppercase', marginBottom: 6, marginTop: 14 },
  supportInputRow: { flexDirection: 'row', alignItems: 'center', backgroundColor: Colors.card, borderRadius: Colors.radius, borderWidth: 1, borderColor: Colors.borderSubtle, paddingHorizontal: 12, height: 46 },
  supportInputIcon: { marginRight: 8 },
  supportInput: { flex: 1, color: Colors.text, fontSize: 16 },
  supportTextArea: { backgroundColor: Colors.card, borderRadius: Colors.radius, borderWidth: 1, borderColor: Colors.borderSubtle, padding: 12, color: Colors.text, fontSize: 16, minHeight: 120, lineHeight: 22 },
  supportSendBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: Colors.primary, borderRadius: Colors.radius, height: 52, marginTop: 20, shadowColor: Colors.primary, shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.3, shadowRadius: 10, elevation: 6 },
  supportSendBtnDisabled: { opacity: 0.4 },
  supportSendBtnText: { color: '#000', fontWeight: '800', fontSize: 15, letterSpacing: 0.3 },
  supportSentWrap: { alignItems: 'center', paddingVertical: 24, gap: 12 },
  supportSentTitle: { color: Colors.text, fontSize: 20, fontWeight: '800' },
  supportSentSub: { color: Colors.textSecondary, fontSize: 14, textAlign: 'center', lineHeight: 21 },
  supportErrorBox: { flexDirection: 'row', alignItems: 'center', backgroundColor: Colors.errorDim, borderRadius: Colors.radius, padding: 10, gap: 8, marginTop: 12 },
  supportErrorText: { color: Colors.error, fontSize: 13, flex: 1 },
});
