import { useState, useEffect, useRef } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  Platform, ActivityIndicator, Image, Animated,
} from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '@/contexts/AuthContext';
import Colors from '@/constants/colors';
import { apiCall } from '@/lib/api';

const INPUT_STYLE = Platform.OS === 'web' ? { outlineWidth: 0 } : {};

// Tap logo 7 times to reveal owner code input
const OWNER_TAP_COUNT = 7;

export default function AuthScreen() {
  const insets = useSafeAreaInsets();
  const { loginWithResponse } = useAuth();

  // Steps: 'email' → 'code' (OTP entry)
  type Step = 'email' | 'code';
  const [step, setStep]       = useState<Step>('email');
  const [email, setEmail]     = useState('');
  const [code, setCode]       = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');
  const [info, setInfo]       = useState('');
  const [resendTimer, setResendTimer] = useState(0);
  const resendRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Owner mode
  const [ownerTaps, setOwnerTaps]   = useState(0);
  const [showOwner, setShowOwner]   = useState(false);
  const [ownerCode, setOwnerCode]   = useState('');
  const [ownerLoading, setOwnerLoading] = useState(false);

  // Splash animation
  const [showSplash, setShowSplash] = useState(true);
  const splashOpacity = useRef(new Animated.Value(1)).current;
  const splashScale   = useRef(new Animated.Value(1)).current;
  const splBurstScale = useRef(new Animated.Value(0.3)).current;
  const splBurstOpac  = useRef(new Animated.Value(1)).current;
  const splLogoScale  = useRef(new Animated.Value(0.5)).current;
  const splLogoOpac   = useRef(new Animated.Value(0)).current;
  const splR1Scale    = useRef(new Animated.Value(1)).current;
  const splR1Opac     = useRef(new Animated.Value(0)).current;
  const splR2Scale    = useRef(new Animated.Value(1)).current;
  const splR2Opac     = useRef(new Animated.Value(0)).current;
  const splScanY      = useRef(new Animated.Value(0)).current;
  const splScanOpac   = useRef(new Animated.Value(0)).current;
  const splTxtOpac    = useRef(new Animated.Value(0)).current;
  const splTxtY       = useRef(new Animated.Value(20)).current;
  const splProgress   = useRef(new Animated.Value(0)).current;
  const splChip0Opac  = useRef(new Animated.Value(0)).current;
  const splChip1Opac  = useRef(new Animated.Value(0)).current;
  const splChip2Opac  = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(splBurstScale, { toValue: 4.5, duration: 550, useNativeDriver: true }),
      Animated.timing(splBurstOpac,  { toValue: 0,   duration: 550, useNativeDriver: true }),
    ]).start();
    const t1 = setTimeout(() => {
      Animated.parallel([
        Animated.spring(splLogoScale, { toValue: 1, friction: 5, tension: 65, useNativeDriver: true }),
        Animated.timing(splLogoOpac,  { toValue: 1, duration: 380, useNativeDriver: true }),
      ]).start();
    }, 120);
    const pulseRing = (scl: Animated.Value, opc: Animated.Value) => {
      scl.setValue(1); opc.setValue(0.65);
      Animated.parallel([
        Animated.timing(scl, { toValue: 2.3, duration: 1600, useNativeDriver: true }),
        Animated.timing(opc, { toValue: 0,   duration: 1600, useNativeDriver: true }),
      ]).start(({ finished }) => { if (finished) pulseRing(scl, opc); });
    };
    const t2 = setTimeout(() => pulseRing(splR1Scale, splR1Opac), 380);
    const t3 = setTimeout(() => pulseRing(splR2Scale, splR2Opac), 1000);
    const t4 = setTimeout(() => {
      splScanY.setValue(0);
      Animated.sequence([
        Animated.timing(splScanOpac, { toValue: 1,   duration: 60,  useNativeDriver: true }),
        Animated.timing(splScanY,    { toValue: 220, duration: 650, useNativeDriver: true }),
        Animated.timing(splScanOpac, { toValue: 0,   duration: 120, useNativeDriver: true }),
      ]).start();
    }, 480);
    const t5 = setTimeout(() => {
      Animated.parallel([
        Animated.timing(splTxtOpac, { toValue: 1, duration: 450, useNativeDriver: true }),
        Animated.timing(splTxtY,    { toValue: 0, duration: 450, useNativeDriver: true }),
      ]).start();
    }, 680);
    const t6 = setTimeout(() => {
      Animated.timing(splProgress, { toValue: 1, duration: 1600, useNativeDriver: false }).start();
    }, 880);
    const t7  = setTimeout(() => Animated.timing(splChip0Opac, { toValue: 1, duration: 380, useNativeDriver: true }).start(), 1080);
    const t7b = setTimeout(() => Animated.timing(splChip1Opac, { toValue: 1, duration: 380, useNativeDriver: true }).start(), 1340);
    const t7c = setTimeout(() => Animated.timing(splChip2Opac, { toValue: 1, duration: 380, useNativeDriver: true }).start(), 1600);
    const tExit = setTimeout(() => {
      Animated.parallel([
        Animated.timing(splashOpacity, { toValue: 0,    duration: 650, useNativeDriver: true }),
        Animated.timing(splashScale,   { toValue: 1.05, duration: 650, useNativeDriver: true }),
      ]).start(() => setShowSplash(false));
    }, 3600);
    return () => { [t1,t2,t3,t4,t5,t6,t7,t7b,t7c,tExit].forEach(clearTimeout); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        router.replace('/(tabs)/scan');
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

  const handleLogoTap = () => {
    const next = ownerTaps + 1;
    setOwnerTaps(next);
    if (next >= OWNER_TAP_COUNT) {
      setShowOwner(true);
      setOwnerTaps(0);
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
    }
  };

  // ── Splash ────────────────────────────────────────────────────────────────
  if (showSplash) {
    return (
      <Animated.View style={[styles.splash, { opacity: splashOpacity, transform: [{ scale: splashScale }] }]}>
        <Animated.View style={[styles.splBurst, { transform: [{ scale: splBurstScale }], opacity: splBurstOpac }]} />
        <View style={styles.splCenter}>
          <Animated.View style={{ transform: [{ scale: splR1Scale }], opacity: splR1Opac, ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center' }}>
            <View style={styles.splRing} />
          </Animated.View>
          <Animated.View style={{ transform: [{ scale: splR2Scale }], opacity: splR2Opac, ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center' }}>
            <View style={[styles.splRing, { width: 160, height: 160, borderRadius: 80 }]} />
          </Animated.View>
          <Animated.View style={[styles.splScan, { transform: [{ translateY: splScanY }], opacity: splScanOpac }]} />
        </View>
        <Animated.Text style={[styles.splTitle, { opacity: splTxtOpac, transform: [{ translateY: splTxtY }] }]}>
          REVERSEPICKS
        </Animated.Text>
        <Animated.View style={[styles.splProgressBar, { width: splProgress.interpolate({ inputRange: [0,1], outputRange: ['0%','100%'] }) }]} />
        <View style={styles.splChips}>
          {[['⚽','Soccer Props'],['🤖','AI Powered'],['📊','Edge Analytics']].map(([icon, label], i) => (
            <Animated.View key={i} style={[styles.splChip, { opacity: [splChip0Opac,splChip1Opac,splChip2Opac][i] }]}>
              <Text style={styles.splChipText}>{icon} {label}</Text>
            </Animated.View>
          ))}
        </View>
      </Animated.View>
    );
  }

  // ── OTP Code Entry ────────────────────────────────────────────────────────
  if (step === 'code') {
    return (
      <View style={[styles.root, { paddingTop: insets.top, paddingBottom: insets.bottom + 20 }]}>
        <View style={styles.inner}>
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
      </View>
    );
  }

  // ── Email Entry ───────────────────────────────────────────────────────────
  return (
    <View style={[styles.root, { paddingTop: insets.top, paddingBottom: insets.bottom + 20 }]}>
      <View style={styles.inner}>
        <View style={styles.card}>
          <TouchableOpacity onPress={handleLogoTap} activeOpacity={0.9} style={styles.logoWrap}>
            <Image source={require('../assets/logo.png')} style={styles.logo} resizeMode="contain" />
          </TouchableOpacity>

          <Text style={styles.welcomeTitle}>Welcome back</Text>
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

          {/* Owner code — hidden, unlocked by tapping logo 7x */}
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
    </View>
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
  inner: {
    flex: 1,
    justifyContent: 'center',
    paddingHorizontal: 20,
  },
  backRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 20,
    paddingTop: 8,
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
  logoWrap: { alignItems: 'center', paddingVertical: 4 },
  logo:     { width: 64, height: 64 },
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

  // Splash
  splash: {
    flex: 1,
    backgroundColor: Colors.background,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 20,
  },
  splBurst: {
    position: 'absolute',
    width: 120, height: 120, borderRadius: 60,
    backgroundColor: Colors.primary,
    opacity: 0.15,
  },
  splCenter: {
    width: 140, height: 140,
    alignItems: 'center', justifyContent: 'center',
  },
  splRing: {
    width: 120, height: 120, borderRadius: 60,
    borderWidth: 1.5, borderColor: Colors.primary,
  },
  splLogoWrap: { width: 80, height: 80, borderRadius: 40, overflow: 'hidden', zIndex: 2 },
  splLogo:     { width: 80, height: 80 },
  splScan: {
    position: 'absolute',
    top: 0, left: -10, right: -10,
    height: 2,
    backgroundColor: Colors.primary,
    opacity: 0.8,
    zIndex: 3,
  },
  splTitle: {
    color: Colors.primary,
    fontSize: 22,
    fontWeight: '900',
    letterSpacing: 6,
  },
  splProgressBar: {
    height: 2,
    backgroundColor: Colors.primary,
    borderRadius: 1,
    alignSelf: 'flex-start',
    marginHorizontal: 40,
  },
  splChips: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', justifyContent: 'center', paddingHorizontal: 20 },
  splChip:  {
    backgroundColor: 'rgba(57,255,20,0.08)',
    borderRadius: 20, borderWidth: 1, borderColor: 'rgba(57,255,20,0.25)',
    paddingHorizontal: 12, paddingVertical: 6,
  },
  splChipText: { color: Colors.primary, fontSize: 12, fontWeight: '600' },
});
