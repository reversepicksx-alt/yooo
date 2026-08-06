import { useState, useEffect, useMemo } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  Platform, ActivityIndicator, Image, Linking, Modal, ScrollView, KeyboardAvoidingView,
} from 'react-native';
import { Redirect, useRouter, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '@/contexts/AuthContext';
import Colors from '@/constants/colors';
import {
  verifyAccess, setPassword as apiSetPassword, authLogin, linkPayment, contactSupport, createCheckout,
} from '@/lib/api';

type Step = 'email' | 'pin' | 'pricing';

/** Emails that bypass all gates — direct login, no PIN / OTP / password. */
const NO_CODE_EMAILS = new Set([
  'reversepicksx@gmail.com',
]);

const INPUT_STYLE = Platform.OS === 'web' ? { outlineWidth: 0 } as object : {};

const PLANS = [
  { key: 'monthly',   label: 'Monthly',  sub: 'Billed monthly', price: '$46.99', unit: '/month', popular: true },
];

export default function AuthScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { loginWithResponse } = useAuth();
  const params = useLocalSearchParams<{ stripe_success?: string }>();

  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [showPaymentEmail, setShowPaymentEmail] = useState(false);
  const [paymentEmail, setPaymentEmail] = useState('');
  const [ownerPin, setOwnerPin] = useState('');
  const [showSupport, setShowSupport] = useState(false);
  const [supportName, setSupportName] = useState('');
  const [supportMessage, setSupportMessage] = useState('');
  const [supportSent, setSupportSent] = useState(false);
  const [supportLoading, setSupportLoading] = useState(false);
  const [supportError, setSupportError] = useState('');

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

    // No-code bypass — instant login for designated emails
    if (NO_CODE_EMAILS.has(trimmed)) {
      try {
        const result = await verifyAccess(trimmed);
        if (result.verified && result.session_token) {
          await loginWithResponse({
            email: result.email || trimmed,
            session_token: result.session_token,
            access_type: result.access_type,
          });
          await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
          router.replace('/(tabs)/scan');
          return;
        }
        // If the backend requires the owner PIN, drop into the PIN screen so the
        // user can actually type the code instead of getting stuck on the email step.
        if (result.owner_pin_required) {
          setStep('pin');
          setOwnerPin('');
          setError('');
          setLoading(false);
          return;
        }
        setError(result.message || 'Access not granted.');
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Login failed. Please try again.');
      } finally {
        setLoading(false);
      }
      return;
    }

    try {
      const result = await verifyAccess(trimmed);
      if (result.owner_pin_required) {
        setStep('pin');
        setOwnerPin('');
      } else if (result.denied && result.denial_reason) {
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

  const handleSendSupport = async () => {
    const message = supportMessage.trim();
    if (!message) return;
    setSupportLoading(true);
    setSupportError('');
    try {
      const result = await contactSupport(
        supportName.trim(),
        email.trim(),
        message,
      );
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

  const handleShowPricing = () => setStep('pricing');

  const handleStripeCheckout = async (planKey: string) => {
    const checkoutEmail = email.trim().toLowerCase();
    if (!checkoutEmail) {
      setStep('email');
      setError('Enter your email address before choosing a plan.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const result = await createCheckout(checkoutEmail, planKey);
      const url = result.checkoutUrl || result.checkout_url || result.redirect_url;
      if (!url) throw new Error(result.error || 'Could not start checkout. Please try again.');
      if (Platform.OS === 'web' && typeof window !== 'undefined') {
        try { window.sessionStorage.setItem('rp_checkout_email', checkoutEmail); } catch {}
        window.location.href = url;
      } else {
        await Linking.openURL(url);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not start checkout. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleConfirmPin = async () => {
    const trimmedEmail = email.trim().toLowerCase();
    const trimmedPin = ownerPin.trim();
    if (!trimmedPin) { setError('Enter your access code.'); return; }
    setLoading(true);
    setError('');
    try {
      const result = await verifyAccess(trimmedEmail, trimmedPin);
      if (result.verified && result.session_token && result.email) {
        await loginWithResponse({
          email: result.email,
          session_token: result.session_token,
          access_type: result.access_type,
        });
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        router.replace('/(tabs)/scan');
      } else if (result.owner_pin_required) {
        setError('Incorrect code. Try again.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      } else {
        setError(result.message || 'Verification failed.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to verify. Check your connection.');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setLoading(false);
    }
  };

  const goBack = () => {
    setStep('email');
    setOwnerPin('');
    setError('');
    setInfo('');
  };

  // Keep the web entry screen deliberately simple and independent from the
  // native-oriented centered layout below. Safari can mount the old nested
  // flex/ScrollView tree without painting its children after a tab logout.
  if (Platform.OS === 'web' && step === 'email') {
    return (
      <View style={styles.webLoginRoot}>
        <View style={styles.webLoginCard}>
          <Image source={require('../assets/logo.png')} style={styles.webLoginLogo as any} resizeMode="contain" />
          <Text style={styles.webLoginTitle}>REVERSEPICKS</Text>
          <Text style={styles.webLoginSubtitle}>ELITE PROP INTELLIGENCE</Text>
          <View style={styles.webLoginInputRow}>
            <Ionicons name="mail-outline" size={18} color={Colors.textSecondary} />
            <TextInput
              style={styles.webLoginInput}
              placeholder="Enter your email"
              placeholderTextColor={Colors.textTertiary}
              value={email}
              onChangeText={v => { setEmail(v); setError(''); setInfo(''); }}
              keyboardType="email-address"
              autoCapitalize="none"
              autoCorrect={false}
              autoComplete="email"
              onSubmitEditing={handleCheckEmail}
            />
          </View>
          {!!info && <InfoBox message={info} />}
          {!!error && <ErrorBox message={error} />}
          <TouchableOpacity
            style={[styles.btn, loading && styles.btnDisabled]}
            onPress={handleCheckEmail}
            disabled={loading}
          >
            {loading ? <ActivityIndicator color="#000" /> : (
              <View style={styles.btnInner}>
                <Ionicons name="flash" size={16} color="#000" />
                <Text style={styles.btnText}>VERIFY ACCESS</Text>
              </View>
            )}
          </TouchableOpacity>
          <TouchableOpacity onPress={handleShowPricing} style={styles.webLoginSecondary}>
            <Text style={styles.alreadyPaid}>Subscribe on the website</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={handleAlreadyPaid} disabled={loading} style={styles.webLoginSecondary}>
            <Text style={styles.webLoginMuted}>Already paid? Verify your payment</Text>
          </TouchableOpacity>
          <TouchableOpacity
            onPress={() => { setSupportSent(false); setSupportName(''); setSupportMessage(''); setSupportError(''); setShowSupport(true); }}
            style={styles.webLoginSecondary}
          >
            <Text style={styles.webLoginMuted}>Contact Support</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  if (step === 'pin') {
    return (
      <View style={[styles.root, { paddingTop: insets.top, paddingBottom: insets.bottom + 20 }]}>
        <View style={styles.inner}>
          <View style={styles.hero}>
            <Image source={require('../assets/logo.png')} style={styles.logo as any} resizeMode="contain" />
            <Text style={styles.appName}>REVERSEPICKS</Text>
            <Text style={styles.tagline}>ELITE PROP INTELLIGENCE</Text>
          </View>
          <View style={styles.formArea}>
            <View style={styles.card}>
              <View style={{ alignItems: 'center', marginBottom: 14 }}>
                <Ionicons name="lock-closed" size={26} color={Colors.primary} />
                <Text style={{ color: Colors.text, fontSize: 15, fontWeight: '600', marginTop: 8, letterSpacing: 0.4 }}>
                  Enter your access code
                </Text>
              </View>
              <View style={styles.inputRow}>
                <Ionicons name="keypad-outline" size={17} color={Colors.textSecondary} style={styles.icon} />
                <TextInput
                  style={[styles.input, INPUT_STYLE]}
                  placeholder="Access code"
                  placeholderTextColor={Colors.textTertiary}
                  value={ownerPin}
                  onChangeText={v => { setOwnerPin(v); setError(''); }}
                  keyboardType="number-pad"
                  autoCapitalize="none"
                  autoCorrect={false}
                  secureTextEntry
                  onSubmitEditing={handleConfirmPin}
                  returnKeyType="done"
                />
              </View>
              {!!error && <ErrorBox message={error} />}
              <TouchableOpacity
                style={[styles.btn, loading && styles.btnDisabled]}
                onPress={handleConfirmPin}
                disabled={loading}
                activeOpacity={0.85}
              >
                {loading
                  ? <ActivityIndicator color="#000" size="small" />
                  : (
                    <View style={styles.btnInner}>
                      <Ionicons name="checkmark-circle" size={16} color="#000" />
                      <Text style={styles.btnText}>CONFIRM</Text>
                    </View>
                  )
                }
              </TouchableOpacity>
              <TouchableOpacity onPress={goBack} style={styles.alreadyPaidRow} activeOpacity={0.7}>
                <Text style={styles.alreadyPaid}>← Back</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </View>
    );
  }

  if (step === 'pricing') {
    return (
      <View style={[styles.root, { paddingTop: insets.top, paddingBottom: insets.bottom }]}>
        <View style={styles.pricingContainer}>
          <View style={styles.pricingHero}>
            <Image source={require('../assets/logo.png')} style={styles.pricingLogo as any} resizeMode="contain" />
            <Text style={styles.pricingTitle}>
              {Platform.OS === 'web' ? 'SUBSCRIBE ON THE WEBSITE' : 'SUBSCRIBE IN THE APP'}
            </Text>
          </View>
          {Platform.OS === 'web' ? (
            <View style={styles.appOnlyNotice}>
              <Ionicons name="card-outline" size={32} color={Colors.primary} />
              <Text style={styles.appOnlyTitle}>Choose your website plan</Text>
              <Text style={styles.appOnlyText}>
                Secure checkout powered by Stripe. Your subscription will work on the Reverse Picks website.
              </Text>
              {PLANS.map(plan => (
                <TouchableOpacity
                  key={plan.key}
                  style={[styles.btn, plan.popular && styles.btnSubscribe, loading && styles.btnDisabled]}
                  onPress={() => handleStripeCheckout(plan.key)}
                  disabled={loading}
                  activeOpacity={0.85}
                >
                  <View style={styles.btnInner}>
                    <Ionicons name="card-outline" size={16} color="#000" />
                    <Text style={styles.btnText}>{plan.label.toUpperCase()} · {plan.price}{plan.unit}</Text>
                  </View>
                </TouchableOpacity>
              ))}
              {!!error && <ErrorBox message={error} />}
            </View>
          ) : (
            <View style={styles.appOnlyNotice}>
              <Ionicons name="logo-apple" size={32} color={Colors.primary} />
              <Text style={styles.appOnlyTitle}>All new memberships are through Apple</Text>
              <Text style={styles.appOnlyText}>
                Download Reverse Picks from the App Store, sign in with this email, and choose your Apple subscription plan there.
              </Text>
              <TouchableOpacity
                style={styles.btn}
                onPress={() => Linking.openURL('https://apps.apple.com/app/id6781092173')}
                activeOpacity={0.85}
              >
                <View style={styles.btnInner}>
                  <Ionicons name="download-outline" size={16} color="#000" />
                  <Text style={styles.btnText}>DOWNLOAD THE APP</Text>
                </View>
              </TouchableOpacity>
            </View>
          )}

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
            style={styles.logo as any}
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
          )}

        </View>
      </View>

      {/* ─── Contact Support Modal ─── */}
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
  webLoginRoot: {
    flex: 1,
    minHeight: '100vh' as any,
    width: '100%',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Colors.background,
    padding: 24,
  },
  webLoginCard: {
    width: '100%',
    maxWidth: 440,
    alignItems: 'stretch',
  },
  webLoginLogo: {
    width: 112,
    height: 112,
    alignSelf: 'center',
    marginBottom: 14,
  },
  webLoginTitle: {
    color: Colors.text,
    fontSize: 25,
    fontWeight: '900',
    letterSpacing: 5,
    textAlign: 'center',
  },
  webLoginSubtitle: {
    color: Colors.primary,
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 3,
    textAlign: 'center',
    marginTop: 6,
    marginBottom: 30,
  },
  webLoginInputRow: {
    height: 54,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    backgroundColor: Colors.card,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    borderRadius: Colors.radius,
    marginBottom: 12,
  },
  webLoginInput: {
    flex: 1,
    minWidth: 0,
    color: Colors.text,
    fontSize: 16,
    outlineWidth: 0,
  } as any,
  webLoginSecondary: {
    alignItems: 'center',
    paddingVertical: 9,
  },
  webLoginMuted: {
    color: Colors.textSecondary,
    fontSize: 13,
    textDecorationLine: 'underline',
  },
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
  input: { flex: 1, color: Colors.text, fontSize: 16 },
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
  appOnlyNotice: {
    alignItems: 'center',
    padding: 22,
    marginTop: 10,
    marginBottom: 16,
    borderRadius: 14,
    backgroundColor: '#111b18',
    borderWidth: 1,
    borderColor: '#245f4d',
  },
  appOnlyTitle: { color: Colors.text, fontSize: 17, fontWeight: '800', textAlign: 'center', marginTop: 12 },
  appOnlyText: { color: Colors.textSecondary, fontSize: 13, lineHeight: 20, textAlign: 'center', marginTop: 9, marginBottom: 18 },
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

  supportRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginTop: 4,
  },
  supportLink: {
    color: Colors.textSecondary,
    fontSize: 12,
    fontWeight: '500',
    textDecorationLine: 'underline',
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.75)',
    justifyContent: 'flex-end',
  },
  supportModal: {
    backgroundColor: '#111',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: 24,
    paddingBottom: 40,
    maxHeight: '90%',
  },
  supportModalHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 16,
  },
  supportModalTitle: {
    flex: 1,
    color: Colors.text,
    fontSize: 17,
    fontWeight: '700',
    letterSpacing: 0.3,
  },
  supportCloseBtn: {
    padding: 4,
  },
  supportSubtitle: {
    color: Colors.textSecondary,
    fontSize: 13,
    lineHeight: 19,
    marginBottom: 20,
  },
  supportLabel: {
    color: Colors.textSecondary,
    fontSize: 12,
    fontWeight: '600',
    letterSpacing: 0.5,
    textTransform: 'uppercase',
    marginBottom: 6,
    marginTop: 14,
  },
  supportInputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    paddingHorizontal: 12,
    height: 46,
  },
  supportInputIcon: { marginRight: 8 },
  supportInput: {
    flex: 1,
    color: Colors.text,
    fontSize: 16,
  },
  supportTextArea: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    padding: 12,
    color: Colors.text,
    fontSize: 16,
    minHeight: 120,
    lineHeight: 22,
  },
  supportSendBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: Colors.primary,
    borderRadius: Colors.radius,
    height: 52,
    marginTop: 20,
    shadowColor: Colors.primary,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 10,
    elevation: 6,
  },
  supportSendBtnDisabled: { opacity: 0.4 },
  supportSendBtnText: { color: '#000', fontWeight: '800', fontSize: 15, letterSpacing: 0.3 },
  supportSentWrap: {
    alignItems: 'center',
    paddingVertical: 24,
    gap: 12,
  },
  supportSentTitle: {
    color: Colors.text,
    fontSize: 20,
    fontWeight: '800',
  },
  supportSentSub: {
    color: Colors.textSecondary,
    fontSize: 14,
    textAlign: 'center',
    lineHeight: 21,
  },
  supportErrorBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.errorDim,
    borderRadius: Colors.radius,
    padding: 10,
    gap: 8,
    marginTop: 12,
  },
  supportErrorText: {
    color: Colors.error,
    fontSize: 13,
    flex: 1,
  },
});
