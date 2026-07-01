import { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  Platform, ActivityIndicator, Image, Alert,
  KeyboardAvoidingView, ScrollView, Animated, Dimensions, Linking,
} from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import * as SecureStore from 'expo-secure-store';
let LocalAuthentication: typeof import('expo-local-authentication') | null = null;
try { LocalAuthentication = require('expo-local-authentication'); } catch { LocalAuthentication = null; }
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '@/contexts/AuthContext';
import Colors from '@/constants/colors';
import { apiCall } from '@/lib/api';
import Purchases from 'react-native-purchases';

const { width: SCREEN_W } = Dimensions.get('window');

type Step = 'landing' | 'email' | 'code';

function getErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === 'string') return e;
  return 'Something went wrong. Please try again.';
}

function InfoBox({ message }: { message: string }) {
  return (
    <View style={styles.infoBox}>
      <Ionicons name="information-circle-outline" size={15} color={Colors.primary} />
      <Text style={styles.infoText}>{message}</Text>
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

// ─────────────────────────────────────────────────────────────────────────────
export default function AuthScreen() {
  const insets = useSafeAreaInsets();
  const { loginWithResponse } = useAuth();

  const [step, setStep]       = useState<Step>('landing');
  const [flow, setFlow]       = useState<'signin' | 'signup'>('signin');
  const [email, setEmail]     = useState('');
  const [code, setCode]       = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');
  const [info, setInfo]       = useState('');
  const [resendTimer, setResendTimer] = useState(0);
  const resendRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Owner mode — hidden behind 5 taps on logo
  const [logoTaps, setLogoTaps]       = useState(0);
  const logoTapTimer                  = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showOwner, setShowOwner]     = useState(false);
  const [ownerCode, setOwnerCode]     = useState('');
  const [ownerLoading, setOwnerLoading] = useState(false);

  // Returning user & biometric
  const [savedEmail, setSavedEmail]   = useState<string | null>(null);
  const [bioAvailable, setBioAvailable] = useState(false);
  const [bioLoading, setBioLoading]   = useState(false);

  // Slide animation between email and code steps
  const slideAnim = useRef(new Animated.Value(0)).current;

  // ── On mount: check for returning user & biometric availability ──────────────
  useEffect(() => {
    (async () => {
      try {
        const stored = Platform.OS !== 'web'
          ? await SecureStore.getItemAsync('rp_email')
          : localStorage.getItem('rp_email');
        if (stored) setSavedEmail(stored);
        if (Platform.OS !== 'web' && LocalAuthentication) {
          const hasHW   = await LocalAuthentication.hasHardwareAsync();
          const enrolled = await LocalAuthentication.isEnrolledAsync();
          setBioAvailable(hasHW && enrolled);
        }
      } catch {}
    })();
  }, []);

  useEffect(() => {
    return () => { if (resendRef.current) clearInterval(resendRef.current); };
  }, []);

  // ── Slide to code step ───────────────────────────────────────────────────────
  const goToCode = useCallback(() => {
    setStep('code');
    slideAnim.setValue(SCREEN_W);
    Animated.spring(slideAnim, {
      toValue: 0,
      friction: 8,
      tension: 70,
      useNativeDriver: true,
    }).start();
  }, [slideAnim]);

  // ── Slide back to email ──────────────────────────────────────────────────────
  const goToEmail = useCallback(() => {
    Animated.timing(slideAnim, {
      toValue: SCREEN_W,
      duration: 220,
      useNativeDriver: true,
    }).start(() => {
      setStep('email');
      setCode('');
      setError('');
      setInfo('');
      slideAnim.setValue(0);
    });
  }, [slideAnim]);

  const startResendTimer = () => {
    setResendTimer(60);
    resendRef.current = setInterval(() => {
      setResendTimer(t => {
        if (t <= 1) { clearInterval(resendRef.current!); return 0; }
        return t - 1;
      });
    }, 1000);
  };

  // ── Secret logo tap to reveal owner panel ───────────────────────────────────
  const handleLogoTap = () => {
    const next = logoTaps + 1;
    setLogoTaps(next);
    if (logoTapTimer.current) clearTimeout(logoTapTimer.current);
    if (next >= 5) {
      setLogoTaps(0);
      setShowOwner(prev => !prev);
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
    } else {
      logoTapTimer.current = setTimeout(() => setLogoTaps(0), 1500);
    }
  };

  // ── Biometric login ──────────────────────────────────────────────────────────
  const handleBiometricLogin = async () => {
    if (!savedEmail || !LocalAuthentication) return;
    setBioLoading(true);
    try {
      const result = await LocalAuthentication.authenticateAsync({
        promptMessage: 'Sign in to Reverse Picks',
        fallbackLabel: 'Use passcode',
        disableDeviceFallback: false,
      });
      if (result.success) {
        setEmail(savedEmail);
        await handleSendCode(savedEmail);
      } else {
        setBioLoading(false);
      }
    } catch {
      setBioLoading(false);
    }
  };

  // ── OTP send ─────────────────────────────────────────────────────────────────
  const handleSendCode = async (emailOverride?: string) => {
    const trimmed = (emailOverride ?? email).trim().toLowerCase();
    if (!trimmed || !trimmed.includes('@')) {
      setError('Enter a valid email address.');
      return;
    }
    setLoading(true);
    setError('');
    setInfo('');

    // Owner email — instant login, no code
    if (trimmed === 'reversepicksx@gmail.com') {
      try {
        const result = await apiCall<any>('/api/auth/verify-access', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: trimmed }),
        });
        if (result.verified) {
          await loginWithResponse({
            email:         result.email,
            session_token: result.session_token,
            access_type:   result.access_type,
          });
          await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
          setLoading(false);
          setBioLoading(false);
          router.replace('/(tabs)/scan');
          return;
        }
      } catch {
        // fall through to normal OTP
      }
    }

    // Link RevenueCat identity BEFORE sending code
    try {
      if (Platform.OS !== 'web') {
        await Purchases.logIn(trimmed);
      }
    } catch (rcErr) {
      console.warn('[RevenueCat] logIn error (non-fatal):', rcErr);
    }

    try {
      const result = await apiCall<any>('/api/auth/send-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: trimmed }),
      });
      if (result.sent) {
        setEmail(trimmed);
        goToCode();
        setInfo(result.message || 'Code sent — check your email.');
        startResendTimer();
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      } else {
        setError(result.message || 'Failed to send code. Try again.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to send code. Check your connection.');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setLoading(false);
      setBioLoading(false);
    }
  };

  // ── OTP verify ───────────────────────────────────────────────────────────────
  const handleVerifyCode = async () => {
    const trimmedCode = code.trim();
    if (trimmedCode.length !== 6) {
      setError('Enter the 6-digit code from your email.');
      return;
    }
    setLoading(true);
    setError('');
    setInfo('');
    try {
      const result = await apiCall<any>('/api/auth/verify-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim().toLowerCase(), code: trimmedCode }),
      });
      if (result.verified) {
        // Fast-path IAP grant if backend says NoSubscription but RC has active entitlement
        if (result.access_type === 'NoSubscription' && Platform.OS === 'ios') {
          try {
            const rcInfo = await Purchases.getCustomerInfo();
            const hasEnt = rcInfo?.entitlements?.active?.['pro'] !== undefined;
            if (hasEnt) {
              const exp    = rcInfo.entitlements.active['pro'].expirationDate;
              const expMs  = exp ? new Date(exp).getTime() : undefined;
              await fetch('/api/auth/iap-grant', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  email:          result.email,
                  session_token:  result.session_token,
                  product_id:     rcInfo.entitlements.active['pro'].productIdentifier || 'unknown',
                  expires_at_ms:  expMs ?? null,
                }),
              });
              result.access_type = 'Premium (Apple)';
              result.has_access  = true;
            }
          } catch (rcErr) {
            console.warn('[IAP] RC fast-path grant failed:', rcErr);
          }
        }

        await loginWithResponse({
          email:         result.email,
          session_token: result.session_token,
          access_type:   result.access_type,
        });
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);

        if (result.has_access === false || result.access_type === 'NoSubscription') {
          router.replace('/paywall');
        } else {
          router.replace('/(tabs)/scan');
        }
      } else {
        setError(result.detail || 'Verification failed. Try again.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Verification failed. Check your connection.');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setLoading(false);
    }
  };

  // ── Owner login ───────────────────────────────────────────────────────────────
  const handleOwnerLogin = async () => {
    if (!ownerCode.trim()) return;
    setOwnerLoading(true);
    setError('');
    try {
      const result = await apiCall<any>('/api/auth/owner-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: ownerCode.trim() }),
      });
      if (result.verified) {
        await loginWithResponse({
          email:         result.email,
          session_token: result.session_token,
          access_type:   result.access_type,
        });
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        router.replace('/(tabs)/scan');
      } else {
        setError('Invalid code.');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Login failed.');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setOwnerLoading(false);
    }
  };

  // ════════════════════════════════════════════════════════════════════════════
  // ── CODE STEP ───────────────────────────────────────────────────────────────
  if (step === 'code') {
    return (
      <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
        <ScrollView contentContainerStyle={{ flexGrow: 1 }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <Animated.View style={[styles.inner, { paddingTop: insets.top + 8, paddingBottom: insets.bottom + 20, transform: [{ translateX: slideAnim }] }]}>
            <TouchableOpacity onPress={goToEmail} style={styles.backRow}>
              <Ionicons name="arrow-back" size={18} color={Colors.textSecondary} />
              <Text style={styles.backRowText}>Back</Text>
            </TouchableOpacity>

            <View style={styles.card}>
              <View style={styles.codeHeader}>
                <View style={styles.codeIconWrap}>
                  <Ionicons name="mail" size={28} color={Colors.primary} />
                </View>
                <Text style={styles.codeTitle}>Check your email</Text>
                <Text style={styles.codeSub}>
                  We sent a 6-digit code to{'\n'}
                  <Text style={styles.codeEmail}>{email}</Text>
                </Text>
              </View>

              <View style={styles.inputRow}>
                <Ionicons name="keypad-outline" size={17} color={Colors.textSecondary} style={styles.icon} />
                <TextInput
                  style={[styles.input, styles.codeInput]}
                  placeholder="000000"
                  placeholderTextColor={Colors.textTertiary}
                  value={code}
                  onChangeText={v => { setCode(v.replace(/\D/g, '').slice(0, 6)); setError(''); }}
                  keyboardType="number-pad"
                  maxLength={6}
                  autoFocus
                  onSubmitEditing={handleVerifyCode}
                  returnKeyType="done"
                  textContentType="oneTimeCode"
                />
              </View>

              {!!info  && <InfoBox  message={info}  />}
              {!!error && <ErrorBox message={error} />}

              <TouchableOpacity
                style={[styles.btn, (loading || code.length !== 6) && styles.btnDisabled]}
                onPress={handleVerifyCode}
                disabled={loading || code.length !== 6}
                activeOpacity={0.85}
              >
                {loading
                  ? <ActivityIndicator color="#000" size="small" />
                  : <View style={styles.btnInner}>
                      <Ionicons name="checkmark-circle" size={16} color="#000" />
                      <Text style={styles.btnText}>CONFIRM CODE</Text>
                    </View>
                }
              </TouchableOpacity>

              <TouchableOpacity
                style={[styles.resendBtn, resendTimer > 0 && styles.resendBtnDisabled]}
                onPress={() => resendTimer === 0 && handleSendCode(email)}
                disabled={resendTimer > 0}
                activeOpacity={0.7}
              >
                <Text style={styles.resendText}>
                  {resendTimer > 0 ? `Resend code in ${resendTimer}s` : 'Resend code'}
                </Text>
              </TouchableOpacity>
            </View>

            <View style={styles.inlineTerms}>
              <Text style={{ color: Colors.textTertiary, fontSize: 11 }}>
                By continuing you agree to our{' '}
              </Text>
              <TouchableOpacity onPress={() => router.push('/terms')} hitSlop={8} style={{ paddingVertical: 4, paddingHorizontal: 2 }}>
                <Text style={styles.inlineTermsLink}>Terms of Use</Text>
              </TouchableOpacity>
              <Text style={{ color: Colors.textTertiary, fontSize: 11 }}>{' & '}</Text>
              <TouchableOpacity onPress={() => router.push('/privacy')} hitSlop={8} style={{ paddingVertical: 4, paddingHorizontal: 2 }}>
                <Text style={styles.inlineTermsLink}>Privacy Policy</Text>
              </TouchableOpacity>
            </View>
          </Animated.View>
        </ScrollView>
      </KeyboardAvoidingView>
    );
  }

  // ════════════════════════════════════════════════════════════════════════════
  // ── LANDING STEP ────────────────────────────────────────────────────────────
  if (step === 'landing') {
    return (
      <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
        <ScrollView contentContainerStyle={{ flexGrow: 1 }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <View style={[styles.inner, { paddingTop: insets.top + 40, paddingBottom: insets.bottom + 24 }]}>

            {/* Logo — tap 5× to reveal owner panel */}
            <TouchableOpacity onPress={handleLogoTap} activeOpacity={1} style={{ alignSelf: 'center', marginBottom: 28 }}>
              <Image source={require('../assets/logo.png')} style={{ width: 72, height: 72 }} resizeMode="contain" />
            </TouchableOpacity>

            <Text style={[styles.welcomeTitle, { marginBottom: 32 }]}>Reverse Picks</Text>

            <TouchableOpacity
              style={styles.landingBtnPrimary}
              onPress={() => { setFlow('signin'); setStep('email'); setError(''); setInfo(''); }}
              activeOpacity={0.85}
            >
              <Ionicons name="log-in-outline" size={18} color="#000" />
              <Text style={styles.landingBtnText}>Sign In</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.landingBtnSecondary}
              onPress={() => {
                Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                router.push('/paywall');
              }}
              activeOpacity={0.85}
            >
              <Ionicons name="card-outline" size={18} color={Colors.primary} />
              <Text style={styles.landingBtnTextSecondary}>Sign Up / Plans</Text>
            </TouchableOpacity>

            {/* Owner panel — revealed by 5 logo taps */}
            {showOwner && (
              <View style={styles.ownerBlock}>
                <View style={styles.inputRow}>
                  <Ionicons name="shield-outline" size={17} color={Colors.primary} style={styles.icon} />
                  <TextInput
                    style={[styles.input]}
                    placeholder="Owner access code"
                    placeholderTextColor={Colors.textTertiary}
                    value={ownerCode}
                    onChangeText={v => { setOwnerCode(v); setError(''); }}
                    autoCapitalize="none"
                    autoCorrect={false}
                    secureTextEntry
                    onSubmitEditing={handleOwnerLogin}
                    returnKeyType="go"
                  />
                </View>
                <TouchableOpacity
                  style={[styles.btn, styles.btnOwner, ownerLoading && styles.btnDisabled]}
                  onPress={handleOwnerLogin}
                  disabled={ownerLoading}
                  activeOpacity={0.85}
                >
                  {ownerLoading
                    ? <ActivityIndicator color="#000" size="small" />
                    : <View style={styles.btnInner}>
                        <Ionicons name="shield-checkmark" size={16} color={Colors.primary} />
                        <Text style={[styles.btnText, { color: Colors.primary }]}>OWNER LOGIN</Text>
                      </View>
                  }
                </TouchableOpacity>
              </View>
            )}

            <View style={styles.inlineTerms}>
              <Text style={{ color: Colors.textTertiary, fontSize: 11 }}>
                By continuing you agree to our{' '}
              </Text>
              <TouchableOpacity onPress={() => router.push('/terms')} hitSlop={8} style={{ paddingVertical: 4, paddingHorizontal: 2 }}>
                <Text style={styles.inlineTermsLink}>Terms of Use</Text>
              </TouchableOpacity>
              <Text style={{ color: Colors.textTertiary, fontSize: 11 }}>{' & '}</Text>
              <TouchableOpacity onPress={() => router.push('/privacy')} hitSlop={8} style={{ paddingVertical: 4, paddingHorizontal: 2 }}>
                <Text style={styles.inlineTermsLink}>Privacy Policy</Text>
              </TouchableOpacity>
            </View>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    );
  }

  // ════════════════════════════════════════════════════════════════════════════
  // ── EMAIL STEP ──────────────────────────────────────────────────────────────
  return (
    <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
      <ScrollView contentContainerStyle={{ flexGrow: 1 }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
        <View style={[styles.inner, { paddingTop: insets.top + 8, paddingBottom: insets.bottom + 20 }]}>

          <TouchableOpacity onPress={() => setStep('landing')} style={styles.backRow}>
            <Ionicons name="arrow-back" size={18} color={Colors.textSecondary} />
            <Text style={styles.backRowText}>Back</Text>
          </TouchableOpacity>

          <View style={styles.card}>
            <Text style={styles.welcomeTitle}>{flow === 'signup' ? 'Get Started' : 'Welcome'}</Text>
            <Text style={styles.welcomeSub}>{flow === 'signup' ? 'Enter your email to choose a plan' : 'Enter your email to sign in'}</Text>

            <View style={styles.inputRow}>
              <Ionicons name="mail-outline" size={17} color={Colors.textSecondary} style={styles.icon} />
              <TextInput
                style={[styles.input]}
                placeholder={savedEmail ?? 'Enter your email'}
                placeholderTextColor={savedEmail ? Colors.textSecondary : Colors.textTertiary}
                value={email}
                onChangeText={v => { setEmail(v); setError(''); setInfo(''); }}
                keyboardType="email-address"
                autoCapitalize="none"
                autoCorrect={false}
                autoComplete="email"
                textContentType="emailAddress"
                onSubmitEditing={() => handleSendCode()}
                returnKeyType="done"
              />
              {savedEmail && email === '' && (
                <TouchableOpacity onPress={() => setEmail(savedEmail)} activeOpacity={0.7}>
                  <Text style={styles.useEmailBtn}>Use</Text>
                </TouchableOpacity>
              )}
            </View>

            {!!info  && <InfoBox  message={info}  />}
            {!!error && <ErrorBox message={error} />}

            <TouchableOpacity
              style={[styles.btn, loading && styles.btnDisabled]}
              onPress={() => handleSendCode()}
              disabled={loading}
              activeOpacity={0.85}
            >
              {loading
                ? <ActivityIndicator color="#000" size="small" />
                : <View style={styles.btnInner}>
                    <Ionicons name="flash" size={16} color="#000" />
                    <Text style={styles.btnText}>SEND CODE</Text>
                  </View>
              }
            </TouchableOpacity>

            {/* Face ID / Touch ID button for returning users */}
            {bioAvailable && savedEmail && (
              <TouchableOpacity
                style={[styles.biometricBtn, bioLoading && styles.btnDisabled]}
                onPress={handleBiometricLogin}
                disabled={bioLoading}
                activeOpacity={0.85}
              >
                {bioLoading
                  ? <ActivityIndicator color={Colors.primary} size="small" />
                  : <View style={styles.btnInner}>
                      <Ionicons name="finger-print-outline" size={18} color={Colors.primary} />
                      <Text style={styles.biometricBtnText}>Sign in with Face ID</Text>
                    </View>
                }
              </TouchableOpacity>
            )}

          </View>

          <View style={styles.inlineTerms}>
            <Text style={{ color: Colors.textTertiary, fontSize: 11 }}>
              By continuing you agree to our{' '}
            </Text>
            <TouchableOpacity onPress={() => router.push('/terms')} hitSlop={8} style={{ paddingVertical: 4, paddingHorizontal: 2 }}>
              <Text style={styles.inlineTermsLink}>Terms of Use</Text>
            </TouchableOpacity>
            <Text style={{ color: Colors.textTertiary, fontSize: 11 }}>{' & '}</Text>
            <TouchableOpacity onPress={() => router.push('/privacy')} hitSlop={8} style={{ paddingVertical: 4, paddingHorizontal: 2 }}>
              <Text style={styles.inlineTermsLink}>Privacy Policy</Text>
            </TouchableOpacity>
          </View>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

// ═════════════════════════════════════════════════════════════════════════════
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  inner: {
    flex: 1,
    justifyContent: 'center',
    paddingHorizontal: 20,
    gap: 16,
  },
  backRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingBottom: 8,
  },
  backRowText: { color: Colors.textSecondary, fontSize: 14 },
  card: {
    backgroundColor: Colors.card,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: 24,
    gap: 14,
  },
  welcomeSub: {
    color: Colors.textSecondary,
    fontSize: 13,
    textAlign: 'center',
    lineHeight: 19,
  },
  welcomeTitle: {
    color: Colors.text,
    fontSize: 22,
    fontWeight: '800',
    textAlign: 'center',
    letterSpacing: 0.3,
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.background,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    paddingHorizontal: 12,
    height: 50,
  },
  icon:  { marginRight: 8 },
  input: { flex: 1, color: Colors.text, fontSize: 16 },
  codeInput: { letterSpacing: 6, fontSize: 22, fontWeight: '700', textAlign: 'center' },
  useEmailBtn: {
    color: Colors.primary,
    fontSize: 13,
    fontWeight: '700',
    paddingHorizontal: 4,
  },
  btn: {
    backgroundColor: Colors.primary,
    borderRadius: Colors.radius,
    height: 52,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: Colors.primary,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 10,
    elevation: 6,
  },
  btnDisabled: { opacity: 0.45 },
  btnOwner: {
    backgroundColor: 'transparent',
    borderWidth: 1.5,
    borderColor: Colors.primary,
    shadowOpacity: 0,
    elevation: 0,
  },
  btnInner: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  btnText:  { color: '#000', fontWeight: '800', fontSize: 15, letterSpacing: 0.5 },
  biometricBtn: {
    backgroundColor: 'transparent',
    borderRadius: Colors.radius,
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: Colors.border,
  },
  biometricBtnText: { color: Colors.primary, fontWeight: '700', fontSize: 14 },
  infoBox: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: 'rgba(57,255,20,0.07)',
    borderRadius: Colors.radius, padding: 10,
  },
  infoText: { color: Colors.primary, fontSize: 13, flex: 1, lineHeight: 18 },
  errorBox: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: Colors.errorDim,
    borderRadius: Colors.radius, padding: 10,
  },
  errorText: { color: Colors.error, fontSize: 13, flex: 1, lineHeight: 18 },
  ownerBlock: { gap: 10, marginTop: 4 },
  resendBtn:         { alignItems: 'center', paddingVertical: 4 },
  resendBtnDisabled: { opacity: 0.4 },
  resendText:        { color: Colors.textSecondary, fontSize: 13, fontWeight: '500' },
  codeHeader: { alignItems: 'center', gap: 8, paddingBottom: 4 },
  codeIconWrap: {
    width: 60, height: 60, borderRadius: 30,
    backgroundColor: 'rgba(57,255,20,0.1)',
    borderWidth: 1.5, borderColor: Colors.primary,
    alignItems: 'center', justifyContent: 'center',
    marginBottom: 4,
  },
  codeTitle: { color: Colors.text, fontSize: 20, fontWeight: '800', letterSpacing: 0.3 },
  codeSub:   { color: Colors.textSecondary, fontSize: 13, textAlign: 'center', lineHeight: 20 },
  codeEmail: { color: Colors.text, fontWeight: '700' },
  inlineTerms: { color: Colors.textTertiary, fontSize: 11, textAlign: 'center', paddingTop: 8 },
  inlineTermsLink: { color: Colors.primary, textDecorationLine: 'underline' },

  // Landing buttons
  landingBtnPrimary: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
    backgroundColor: Colors.primary, borderRadius: Colors.radius, height: 52,
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.35, shadowRadius: 14, elevation: 8,
  },
  landingBtnSecondary: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
    backgroundColor: 'transparent', borderRadius: Colors.radius, height: 52,
    borderWidth: 1.5, borderColor: Colors.primary,
  },
  landingBtnText: { color: '#000', fontWeight: '800', fontSize: 15, letterSpacing: 0.5 },
  landingBtnTextSecondary: { color: Colors.primary, fontWeight: '700', fontSize: 15, letterSpacing: 0.5 },

  backBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: Colors.card, borderRadius: Colors.radius, borderWidth: 1, borderColor: Colors.borderSubtle, height: 50, marginTop: 4 },
  backBtnText: { color: Colors.text, fontSize: 14, fontWeight: '600' },
});
