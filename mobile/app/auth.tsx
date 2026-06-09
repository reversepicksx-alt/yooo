import { useState, useEffect, useRef } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  Platform, ActivityIndicator, Image, Linking, Modal, ScrollView, KeyboardAvoidingView,
  Animated,
} from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '@/contexts/AuthContext';
import Colors from '@/constants/colors';
import {
  verifyAccess, setPassword as apiSetPassword, authLogin, createCheckout, linkPayment, contactSupport,
} from '@/lib/api';

type Step = 'email' | 'pricing';

const INPUT_STYLE = Platform.OS === 'web' ? { outlineWidth: 0, outlineStyle: 'none' } : {};

const PLANS = [
  { key: 'weekly',    label: 'Weekly',   sub: 'Billed weekly',  price: '$15',    unit: '/week',  popular: false },
  { key: 'monthly',   label: 'Monthly',  sub: 'Save 8%',        price: '$49.99', unit: '/month', popular: true  },
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
  const noMembershipTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showSplash, setShowSplash] = useState(true);
  const splashOpacity = useRef(new Animated.Value(1)).current;
  const splashScale   = useRef(new Animated.Value(1)).current;
  // Enhanced intro animation values
  const splBurstScale  = useRef(new Animated.Value(0.3)).current;
  const splBurstOpac   = useRef(new Animated.Value(1)).current;
  const splLogoScale   = useRef(new Animated.Value(0.5)).current;
  const splLogoOpac    = useRef(new Animated.Value(0)).current;
  const splR1Scale     = useRef(new Animated.Value(1)).current;
  const splR1Opac      = useRef(new Animated.Value(0)).current;
  const splR2Scale     = useRef(new Animated.Value(1)).current;
  const splR2Opac      = useRef(new Animated.Value(0)).current;
  const splScanY       = useRef(new Animated.Value(0)).current;
  const splScanOpac    = useRef(new Animated.Value(0)).current;
  const splTxtOpac     = useRef(new Animated.Value(0)).current;
  const splTxtY        = useRef(new Animated.Value(20)).current;
  const splProgress    = useRef(new Animated.Value(0)).current;
  const splChipsOpac   = useRef(new Animated.Value(0)).current;
  const [showPaymentEmail, setShowPaymentEmail] = useState(false);
  const [paymentEmail, setPaymentEmail] = useState('');
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

  // Cleanup the no-membership auto-redirect timer on unmount
  useEffect(() => {
    return () => {
      if (noMembershipTimerRef.current) clearTimeout(noMembershipTimerRef.current);
    };
  }, []);

  // Intro splash animation sequence
  useEffect(() => {
    // Phase 1 (0ms): Power burst ring expands + fades
    Animated.parallel([
      Animated.timing(splBurstScale, { toValue: 4.5, duration: 550, useNativeDriver: true }),
      Animated.timing(splBurstOpac,  { toValue: 0,   duration: 550, useNativeDriver: true }),
    ]).start();

    // Phase 2 (120ms): Logo springs in
    const t1 = setTimeout(() => {
      Animated.parallel([
        Animated.spring(splLogoScale, { toValue: 1, friction: 5, tension: 65, useNativeDriver: true }),
        Animated.timing(splLogoOpac,  { toValue: 1, duration: 380, useNativeDriver: true }),
      ]).start();
    }, 120);

    // Phase 3 (380ms): Radar rings pulse outward (recursive loop)
    const pulseRing = (scl: Animated.Value, opc: Animated.Value) => {
      scl.setValue(1);
      opc.setValue(0.65);
      Animated.parallel([
        Animated.timing(scl, { toValue: 2.3, duration: 1600, useNativeDriver: true }),
        Animated.timing(opc, { toValue: 0,   duration: 1600, useNativeDriver: true }),
      ]).start(({ finished }) => { if (finished) pulseRing(scl, opc); });
    };
    const t2 = setTimeout(() => pulseRing(splR1Scale, splR1Opac), 380);
    const t3 = setTimeout(() => pulseRing(splR2Scale, splR2Opac), 1000);

    // Phase 4 (480ms): Scan line sweeps top → bottom of logo
    const t4 = setTimeout(() => {
      splScanY.setValue(0);
      Animated.sequence([
        Animated.timing(splScanOpac, { toValue: 1,   duration: 60,  useNativeDriver: true }),
        Animated.timing(splScanY,    { toValue: 220,  duration: 650, useNativeDriver: true }),
        Animated.timing(splScanOpac, { toValue: 0,    duration: 120, useNativeDriver: true }),
      ]).start();
    }, 480);

    // Phase 5 (680ms): REVERSEPICKS text slides up
    const t5 = setTimeout(() => {
      Animated.parallel([
        Animated.timing(splTxtOpac, { toValue: 1, duration: 450, useNativeDriver: true }),
        Animated.timing(splTxtY,    { toValue: 0, duration: 450, useNativeDriver: true }),
      ]).start();
    }, 680);

    // Phase 6 (880ms): Progress bar fills (non-native because width)
    const t6 = setTimeout(() => {
      Animated.timing(splProgress, { toValue: 1, duration: 1600, useNativeDriver: false }).start();
    }, 880);

    // Phase 7 (1080ms): Data chips fade in
    const t7 = setTimeout(() => {
      Animated.timing(splChipsOpac, { toValue: 1, duration: 500, useNativeDriver: true }).start();
    }, 1080);

    // Phase 8 (3300ms): Exit — fade + slight scale up
    const tExit = setTimeout(() => {
      Animated.parallel([
        Animated.timing(splashOpacity, { toValue: 0, duration: 650, useNativeDriver: true }),
        Animated.timing(splashScale,   { toValue: 1.05, duration: 650, useNativeDriver: true }),
      ]).start(() => setShowSplash(false));
    }, 3300);

    return () => {
      [t1, t2, t3, t4, t5, t6, t7, tExit].forEach(clearTimeout);
    };
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
        setError('No membership found — taking you to plans now...');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
        if (noMembershipTimerRef.current) clearTimeout(noMembershipTimerRef.current);
        noMembershipTimerRef.current = setTimeout(() => {
          setError('');
          setStep('pricing');
        }, 1000);
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

      {/* ── Intro splash animation ── */}
      {showSplash && (
        <Animated.View
          style={[styles.splashOverlay, {
            opacity: splashOpacity,
            transform: [{ scale: splashScale }],
            pointerEvents: 'none',
          }]}
        >
          {/* Power burst ring — expands outward and fades on entry */}
          <Animated.View style={[styles.splashBurstRing, {
            opacity: splBurstOpac,
            transform: [{ scale: splBurstScale }],
          }]} />

          {/* Radar ring 1 — continuous pulse */}
          <Animated.View style={[styles.splashRadarRing, {
            opacity: splR1Opac,
            transform: [{ scale: splR1Scale }],
          }]} />

          {/* Radar ring 2 — offset pulse */}
          <Animated.View style={[styles.splashRadarRing, styles.splashRadarRing2, {
            opacity: splR2Opac,
            transform: [{ scale: splR2Scale }],
          }]} />

          {/* Logo with scan line sweeping across it */}
          <Animated.View style={[styles.splashLogoWrap, {
            opacity: splLogoOpac,
            transform: [{ scale: splLogoScale }],
          }]}>
            <Image
              source={require('../assets/rp-splash.png')}
              style={styles.splashLogo}
              resizeMode="contain"
            />
            <Animated.View style={[styles.splashScanLine, {
              opacity: splScanOpac,
              transform: [{ translateY: splScanY }],
            }]} />
          </Animated.View>

          {/* REVERSEPICKS title — slides up */}
          <Animated.View style={{
            alignItems: 'center',
            opacity: splTxtOpac,
            transform: [{ translateY: splTxtY }],
          }}>
            <Text style={styles.splashName}>REVERSEPICKS</Text>
          </Animated.View>

          {/* Neon progress bar */}
          <View style={styles.splashProgressTrack}>
            <Animated.View style={[styles.splashProgressFill, {
              width: splProgress.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] }),
            }]} />
          </View>

          {/* Data chips */}
          <Animated.View style={[styles.splashChipsRow, { opacity: splChipsOpac }]}>
            {(['AI', 'BAYESIAN', 'LIVE'] as const).map((label, i) => (
              <View key={label} style={[styles.splashChip, i > 0 && { marginLeft: 10 }]}>
                <Text style={styles.splashChipText}>{label}</Text>
              </View>
            ))}
          </Animated.View>
        </Animated.View>
      )}
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
  splashOverlay: {
    position: 'absolute',
    top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: '#000000',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 9999,
  },
  splashBurstRing: {
    position: 'absolute',
    width: 230,
    height: 230,
    borderRadius: 115,
    borderWidth: 2.5,
    borderColor: '#39FF14',
  },
  splashRadarRing: {
    position: 'absolute',
    width: 250,
    height: 250,
    borderRadius: 125,
    borderWidth: 1.5,
    borderColor: '#39FF14',
  },
  splashRadarRing2: {
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.6)',
  },
  splashLogoWrap: {
    width: 220,
    height: 220,
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  splashLogo: {
    width: 220,
    height: 220,
  },
  splashScanLine: {
    position: 'absolute',
    top: 0,
    left: -20,
    right: -20,
    height: 2,
    backgroundColor: '#39FF14',
    ...(Platform.OS === 'web'
      ? { boxShadow: '0 0 14px 5px rgba(57,255,20,0.8)' }
      : {
          shadowColor: '#39FF14',
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 1,
          shadowRadius: 10,
        }),
  },
  splashName: {
    color: '#FFFFFF',
    fontSize: 26,
    fontWeight: '800',
    letterSpacing: 6,
    marginTop: 18,
  },
  splashProgressTrack: {
    width: 150,
    height: 2,
    backgroundColor: '#1c1c1c',
    borderRadius: 1,
    overflow: 'hidden',
    marginTop: 12,
  },
  splashProgressFill: {
    height: 2,
    backgroundColor: '#39FF14',
    borderRadius: 1,
    ...(Platform.OS === 'web'
      ? { boxShadow: '0 0 8px rgba(57,255,20,0.7)' }
      : {
          shadowColor: '#39FF14',
          shadowOffset: { width: 0, height: 0 },
          shadowOpacity: 0.8,
          shadowRadius: 6,
        }),
  },
  splashChipsRow: {
    flexDirection: 'row',
    marginTop: 18,
  },
  splashChip: {
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.3)',
    borderRadius: 4,
    paddingHorizontal: 9,
    paddingVertical: 4,
    backgroundColor: 'rgba(57,255,20,0.05)',
  },
  splashChipText: {
    color: 'rgba(57,255,20,0.65)',
    fontSize: 8,
    fontWeight: '700',
    letterSpacing: 2.5,
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
