import { useState, useEffect, useRef } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  Platform, ActivityIndicator, Image, Alert,
  KeyboardAvoidingView, ScrollView,
} from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '@/contexts/AuthContext';
import Colors from '@/constants/colors';
import { apiCall } from '@/lib/api';
import { useSubscription } from '@/lib/revenuecat';
import type { PurchasesPackage } from 'react-native-purchases';

const INPUT_STYLE = Platform.OS === 'web' ? { outlineWidth: 0 } : {};

const FEATURES = [
  'AI-powered soccer player prop predictions',
  'Bayesian confidence scoring on every pick',
  'Live match intel & tactical breakdowns',
  'Scan bet slips to get instant analysis',
];

function getErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === 'string') return e;
  return 'Something went wrong. Please try again.';
}

export default function AuthScreen() {
  const insets = useSafeAreaInsets();
  const { loginWithResponse } = useAuth();
  const { packages, isLoading: pkgLoading, purchase, restore, isPurchasing, isRestoring } = useSubscription();

  type Step = 'paywall' | 'email' | 'code';
  const [step, setStep]       = useState<Step>(Platform.OS === 'ios' ? 'paywall' : 'email');
  const [email, setEmail]     = useState('');
  const [code, setCode]       = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');
  const [info, setInfo]       = useState('');
  const [resendTimer, setResendTimer] = useState(0);
  const resendRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [buyingId, setBuyingId] = useState<string | null>(null);

  // Owner mode
  const [showOwner, setShowOwner]   = useState(false);
  const [ownerCode, setOwnerCode]   = useState('');
  const [ownerLoading, setOwnerLoading] = useState(false);

  useEffect(() => {
    return () => { if (resendRef.current) clearInterval(resendRef.current); };
  }, []);

  const startResendTimer = () => {
    setResendTimer(60);
    resendRef.current = setInterval(() => {
      setResendTimer(t => {
        if (t <= 1) { clearInterval(resendRef.current!); return 0; }
        return t - 1;
      });
    }, 1000);
  };

  // ── Apple IAP purchase ─────────────────────────────────────────────────────
  const handleSubscribe = async (pkg: PurchasesPackage) => {
    setBuyingId(pkg.identifier);
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      await purchase(pkg);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      setInfo('Subscribed! Now enter your email to create your account.');
      setStep('email');
    } catch (e: any) {
      if (e?.userCancelled) return;
      Alert.alert('Purchase Failed', getErrorMessage(e));
    } finally {
      setBuyingId(null);
    }
  };

  const handleRestore = async () => {
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      await restore();
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      setInfo('Purchases restored! Enter your email to sign in.');
      setStep('email');
    } catch (e: any) {
      Alert.alert('Restore Failed', getErrorMessage(e));
    }
  };

  // ── OTP send ───────────────────────────────────────────────────────────────
  const handleSendCode = async (emailOverride?: string) => {
    const trimmed = (emailOverride ?? email).trim().toLowerCase();
    if (!trimmed || !trimmed.includes('@')) {
      setError('Enter a valid email address.');
      return;
    }
    setLoading(true);
    setError('');
    setInfo('');
    try {
      const result = await apiCall('/api/auth/send-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: trimmed }),
      });
      if (result.sent) {
        setEmail(trimmed);
        setStep('code');
        setInfo('Code sent — check your email.');
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
    }
  };

  // ── OTP verify ─────────────────────────────────────────────────────────────
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
      const result = await apiCall('/api/auth/verify-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim().toLowerCase(), code: trimmedCode }),
      });
      if (result.verified) {
        await loginWithResponse({
          email:         result.email,
          session_token: result.session_token,
          access_type:   result.access_type,
        });
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        if (result.has_access === false || result.access_type === 'NoSubscription') {
          router.replace('/(tabs)/account');
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

  // ── Owner login ────────────────────────────────────────────────────────────
  const handleOwnerLogin = async () => {
    if (!ownerCode.trim()) return;
    setOwnerLoading(true);
    setError('');
    try {
      const result = await apiCall('/api/auth/owner-login', {
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

  // ── PAYWALL ────────────────────────────────────────────────────────────────
  if (step === 'paywall') {
    return (
      <ScrollView
        style={styles.root}
        contentContainerStyle={[styles.paywallScroll, { paddingTop: insets.top + 20, paddingBottom: insets.bottom + 32 }]}
        showsVerticalScrollIndicator={false}
      >
        {/* Logo + header */}
        <View style={styles.paywallTop}>
          <Image source={require('../assets/logo.png')} style={styles.paywallLogo} resizeMode="contain" />
          <Text style={styles.paywallBrand}>REVERSEPICKS</Text>
          <Text style={styles.paywallHeadline}>Pro Analytics</Text>
          <Text style={styles.paywallSub}>AI-powered soccer player prop predictions</Text>
        </View>

        {/* Features */}
        <View style={styles.featureList}>
          {FEATURES.map((f, i) => (
            <View key={i} style={styles.featureRow}>
              <Ionicons name="checkmark-circle" size={18} color={Colors.primary} />
              <Text style={styles.featureText}>{f}</Text>
            </View>
          ))}
        </View>

        {/* Plans */}
        <View style={styles.plansWrap}>
          {pkgLoading ? (
            <View style={styles.pkgLoading}>
              <ActivityIndicator size="small" color={Colors.primary} />
              <Text style={styles.pkgLoadingText}>Loading plans…</Text>
            </View>
          ) : packages.length === 0 ? (
            <View style={styles.pkgEmpty}>
              <Text style={styles.pkgEmptyText}>Plans unavailable right now. Try again later.</Text>
            </View>
          ) : (
            packages.map((pkg, idx) => {
              const isMonthly = pkg.identifier === '$rc_monthly' || pkg.packageType === 'MONTHLY';
              const isBuying  = buyingId === pkg.identifier;
              const label     = isMonthly ? 'Monthly' : 'Weekly';
              const price     = pkg.product?.priceString ?? '—';
              const period    = isMonthly ? '/ month' : '/ week';
              return (
                <TouchableOpacity
                  key={pkg.identifier}
                  style={[styles.planCard, isMonthly && styles.planCardFeatured]}
                  onPress={() => handleSubscribe(pkg)}
                  disabled={!!buyingId || isRestoring}
                  activeOpacity={0.8}
                >
                  {isMonthly && (
                    <View style={styles.planBadge}>
                      <Text style={styles.planBadgeText}>BEST VALUE</Text>
                    </View>
                  )}
                  <View style={styles.planLeft}>
                    <Text style={[styles.planLabel, isMonthly && styles.planLabelFeatured]}>{label}</Text>
                    <Text style={styles.planPeriod}>{period}</Text>
                  </View>
                  <View style={styles.planRight}>
                    {isBuying ? (
                      <ActivityIndicator size="small" color={isMonthly ? '#000' : Colors.primary} />
                    ) : (
                      <>
                        <Text style={[styles.planPrice, isMonthly && styles.planPriceFeatured]}>{price}</Text>
                        <Ionicons name="chevron-forward" size={16} color={isMonthly ? '#000' : Colors.textTertiary} />
                      </>
                    )}
                  </View>
                </TouchableOpacity>
              );
            })
          )}
        </View>

        <Text style={styles.paywallDisclosure}>
          Subscriptions auto-renew unless cancelled 24h before period ends. Manage anytime in Apple Settings.
        </Text>

        {/* Restore */}
        <TouchableOpacity
          style={styles.restoreBtn}
          onPress={handleRestore}
          disabled={isRestoring || !!buyingId}
          activeOpacity={0.7}
        >
          {isRestoring
            ? <ActivityIndicator size="small" color={Colors.textSecondary} />
            : <Text style={styles.restoreBtnText}>Restore Purchases</Text>
          }
        </TouchableOpacity>

        {/* Sign in link */}
        <TouchableOpacity
          style={styles.signInLink}
          onPress={() => { setError(''); setInfo(''); setStep('email'); }}
          activeOpacity={0.7}
        >
          <Text style={styles.signInLinkText}>Already a member? <Text style={styles.signInLinkBold}>Sign In</Text></Text>
        </TouchableOpacity>

        {/* Admin access */}
        <TouchableOpacity
          style={styles.adminLink}
          onPress={() => { setShowOwner(v => !v); setStep('email'); }}
          activeOpacity={0.6}
        >
          <Ionicons name="shield-outline" size={13} color={Colors.textTertiary} />
          <Text style={styles.adminLinkText}>Admin Access</Text>
        </TouchableOpacity>
      </ScrollView>
    );
  }

  // ── OTP Code Entry ────────────────────────────────────────────────────────
  if (step === 'code') {
    return (
      <KeyboardAvoidingView
        style={styles.root}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <ScrollView
          contentContainerStyle={{ flexGrow: 1 }}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
        <View style={[styles.inner, { paddingTop: insets.top + 8, paddingBottom: insets.bottom + 20 }]}>
          <TouchableOpacity onPress={() => { setStep('email'); setCode(''); setError(''); setInfo(''); }} style={styles.backRow}>
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
                style={[styles.input, styles.codeInput, INPUT_STYLE]}
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
        </View>
        </ScrollView>
      </KeyboardAvoidingView>
    );
  }

  // ── Email Entry ───────────────────────────────────────────────────────────
  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <ScrollView
        contentContainerStyle={{ flexGrow: 1 }}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
      <View style={[styles.inner, { paddingTop: insets.top + 8, paddingBottom: insets.bottom + 20 }]}>
        {Platform.OS === 'ios' && (
          <TouchableOpacity onPress={() => { setError(''); setInfo(''); setStep('paywall'); }} style={styles.backRow}>
            <Ionicons name="arrow-back" size={18} color={Colors.textSecondary} />
            <Text style={styles.backRowText}>Back</Text>
          </TouchableOpacity>
        )}

        <View style={styles.card}>
          <View style={styles.logoWrap}>
            <Image source={require('../assets/logo.png')} style={styles.logoImg} resizeMode="contain" />
          </View>

          <Text style={styles.welcomeTitle}>Sign In</Text>
          <Text style={styles.welcomeSub}>Enter your email to receive a login code</Text>

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
              onSubmitEditing={() => handleSendCode()}
              returnKeyType="done"
            />
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

          {!showOwner && (
            <TouchableOpacity onPress={() => setShowOwner(true)} style={styles.adminLink} activeOpacity={0.6}>
              <Ionicons name="shield-outline" size={13} color={Colors.textTertiary} />
              <Text style={styles.adminLinkText}>Admin Access</Text>
            </TouchableOpacity>
          )}

          {showOwner && (
            <View style={styles.ownerBlock}>
              <View style={styles.inputRow}>
                <Ionicons name="shield-outline" size={17} color={Colors.primary} style={styles.icon} />
                <TextInput
                  style={[styles.input, INPUT_STYLE]}
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
                      <Ionicons name="shield-checkmark" size={16} color="#000" />
                      <Text style={styles.btnText}>OWNER LOGIN</Text>
                    </View>
                }
              </TouchableOpacity>
            </View>
          )}
        </View>
      </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
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

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: Colors.background,
  },

  // ── Paywall ─────────────────────────────────────────────────────────────
  paywallScroll: {
    paddingHorizontal: 24,
    alignItems: 'stretch',
  },
  paywallTop: {
    alignItems: 'center',
    marginBottom: 28,
  },
  paywallLogo: {
    width: 72,
    height: 72,
    marginBottom: 10,
  },
  paywallBrand: {
    color: Colors.primary,
    fontSize: 13,
    fontWeight: '800',
    letterSpacing: 4,
    marginBottom: 6,
  },
  paywallHeadline: {
    color: Colors.text,
    fontSize: 30,
    fontWeight: '900',
    letterSpacing: 0.5,
    textAlign: 'center',
  },
  paywallSub: {
    color: Colors.textSecondary,
    fontSize: 14,
    textAlign: 'center',
    marginTop: 6,
    lineHeight: 20,
  },
  featureList: {
    gap: 12,
    marginBottom: 28,
  },
  featureRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  featureText: {
    color: Colors.text,
    fontSize: 15,
    flex: 1,
    lineHeight: 20,
  },
  plansWrap: {
    gap: 12,
    marginBottom: 20,
  },
  pkgLoading: {
    alignItems: 'center',
    paddingVertical: 24,
    gap: 8,
  },
  pkgLoadingText: {
    color: Colors.textSecondary,
    fontSize: 14,
  },
  pkgEmpty: {
    alignItems: 'center',
    paddingVertical: 20,
  },
  pkgEmptyText: {
    color: Colors.textSecondary,
    fontSize: 14,
    textAlign: 'center',
  },
  planCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.card,
    borderRadius: 14,
    borderWidth: 1.5,
    borderColor: Colors.border,
    paddingHorizontal: 18,
    paddingVertical: 16,
    position: 'relative',
    overflow: 'hidden',
  },
  planCardFeatured: {
    backgroundColor: Colors.primary,
    borderColor: Colors.primary,
  },
  planBadge: {
    position: 'absolute',
    top: 0,
    right: 0,
    backgroundColor: 'rgba(0,0,0,0.25)',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderBottomLeftRadius: 10,
  },
  planBadgeText: {
    color: '#000',
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1,
  },
  planLeft: {
    flex: 1,
  },
  planLabel: {
    color: Colors.text,
    fontSize: 17,
    fontWeight: '700',
  },
  planLabelFeatured: {
    color: '#000',
  },
  planPeriod: {
    color: Colors.textSecondary,
    fontSize: 13,
    marginTop: 2,
  },
  planRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  planPrice: {
    color: Colors.primary,
    fontSize: 18,
    fontWeight: '800',
  },
  planPriceFeatured: {
    color: '#000',
  },
  paywallDisclosure: {
    color: Colors.textTertiary,
    fontSize: 11,
    textAlign: 'center',
    lineHeight: 16,
    marginBottom: 16,
  },
  restoreBtn: {
    alignItems: 'center',
    paddingVertical: 8,
    marginBottom: 4,
  },
  restoreBtnText: {
    color: Colors.textSecondary,
    fontSize: 13,
    fontWeight: '500',
  },
  signInLink: {
    alignItems: 'center',
    paddingVertical: 10,
  },
  signInLinkText: {
    color: Colors.textSecondary,
    fontSize: 14,
  },
  signInLinkBold: {
    color: Colors.primary,
    fontWeight: '700',
  },

  // ── Email / OTP ──────────────────────────────────────────────────────────
  inner: {
    flex: 1,
    justifyContent: 'center',
    paddingHorizontal: 20,
  },
  backRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingBottom: 16,
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
  logoWrap:        { alignItems: 'center', paddingVertical: 4 },
  logoImg:         { width: 64, height: 64 },
  welcomeTitle: {
    color: Colors.text,
    fontSize: 22,
    fontWeight: '800',
    textAlign: 'center',
    letterSpacing: 0.3,
  },
  welcomeSub: {
    color: Colors.textSecondary,
    fontSize: 13,
    textAlign: 'center',
    lineHeight: 19,
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
  btnOwner: { backgroundColor: '#1a1a1a', borderWidth: 1.5, borderColor: Colors.primary },
  btnInner: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  btnText:  { color: '#000', fontWeight: '800', fontSize: 15, letterSpacing: 0.5 },
  infoBox: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: 'rgba(57,255,20,0.07)',
    borderRadius: Colors.radius, padding: 10,
  },
  infoText:  { color: Colors.primary, fontSize: 13, flex: 1, lineHeight: 18 },
  errorBox: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: Colors.errorDim,
    borderRadius: Colors.radius, padding: 10,
  },
  errorText: { color: Colors.error, fontSize: 13, flex: 1, lineHeight: 18 },
  ownerBlock: { gap: 10, marginTop: 4 },
  resendBtn: { alignItems: 'center', paddingVertical: 4 },
  resendBtnDisabled: { opacity: 0.4 },
  resendText: { color: Colors.textSecondary, fontSize: 13, fontWeight: '500' },
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
  adminLink: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
    paddingVertical: 4,
    marginTop: -4,
  },
  adminLinkText: {
    color: Colors.textTertiary,
    fontSize: 12,
    letterSpacing: 0.4,
  },
});
