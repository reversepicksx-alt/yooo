import { useState, useEffect, useCallback, useRef } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  ActivityIndicator, Platform, Image, Alert, TextInput, KeyboardAvoidingView,
  Linking,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import { useSubscription, REVENUECAT_ENTITLEMENT_IDENTIFIER } from '@/lib/revenuecat';
import { iapSignup } from '@/lib/api';
import Purchases, { type PurchasesPackage } from 'react-native-purchases';

const FEATURES = [
  'Unlimited AI player prop predictions',
  'Tactical breakdowns & sharp summaries',
  'Real-time injury & lineup intel',
  'All major leagues + tournaments',
  'All major soccer leagues + international tournaments',
];

function getErrorMessage(e: unknown): string {
  if (typeof e === 'string') return e;

  const err = e as any;
  const code = err?.code ?? err?.errorCode ?? 0;
  const msg = err?.message ?? err?.underlyingErrorMessage ?? '';

  // RevenueCat / StoreKit decline codes
  if (code === 4 || /not allowed|declined|not authorized/i.test(msg)) {
    return 'Your payment method was declined.\n\nTry updating your Apple ID payment method in Settings, then return and try again.';
  }
  if (code === 5 || /invalid purchase|invalid identifier/i.test(msg)) {
    return 'This subscription option is temporarily unavailable. Please try a different plan or try again later.';
  }
  if (code === 6 || /product.*not available|not available for purchase/i.test(msg)) {
    return 'This subscription is not currently available. Please try again in a few minutes.';
  }
  if (code === 7 || /network|connection|offline/i.test(msg)) {
    return 'Network issue. Please check your connection and try again.';
  }
  if (code === 10 || /pending/i.test(msg)) {
    return 'This purchase is pending approval. You will be notified once it completes.';
  }
  if (code === 3 || /store.*problem|storekit/i.test(msg)) {
    return 'Apple Store is experiencing issues. Please try again in a few minutes.';
  }

  if (e instanceof Error) return e.message;
  return 'Something went wrong. Please try again.';
}

/** Convert RevenueCat ISO 8601 subscriptionPeriod (P1W, P1M, P3M) to human label */
function isoPeriodToLabel(raw: string): string {
  if (!raw) return '';
  const m = raw.match(/^P(\d+)([YMWD])$/);
  if (!m) return raw;
  const n = parseInt(m[1], 10);
  const unit = m[2];
  const map: Record<string, string> = {
    D: n === 1 ? 'day' : 'days',
    W: n === 1 ? 'week' : 'weeks',
    M: n === 1 ? 'month' : 'months',
    Y: n === 1 ? 'year' : 'years',
  };
  return `${n} ${map[unit] ?? unit}`;
}

export default function PaywallScreen() {
  const insets = useSafeAreaInsets();
  const { session, loginWithResponse, logout } = useAuth();
  const {
    packages, isLoading, purchase, restore,
    isPurchasing, isRestoring, refetchOfferings,
  } = useSubscription();

  const [selectedPkg, setSelectedPkg] = useState<PurchasesPackage | null>(null);
  const [buyingId, setBuyingId] = useState<string | null>(null);

  // Guest-mode: purchase completed but no account yet — collect email to create one
  const [showEmailCapture, setShowEmailCapture] = useState(false);
  const [pendingRevenueCatCustomerId, setPendingRevenueCatCustomerId] = useState('');
  const [guestEmail, setGuestEmail] = useState('');
  const [guestEmailError, setGuestEmailError] = useState('');
  const [guestEmailLoading, setGuestEmailLoading] = useState(false);

  const syncBackendAndEnter = useCallback(async (revenueCatCustomerId: string) => {
    if (!session?.email || !session?.token) return;
    const response = await fetch('/api/auth/iap-grant', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: session.email,
        session_token: session.token,
        revenuecat_customer_id: revenueCatCustomerId,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(body?.detail || 'Apple subscription verification failed. Please try again.');
    }
    // Update local session so future gate checks pass
    await loginWithResponse({
      email: session.email,
      session_token: session.token,
      access_type: 'Premium (Apple)',
    });
  }, [session, loginWithResponse]);

  // If somehow a subscribed user lands here (e.g. device has an active
  // RevenueCat entitlement but the backend session is still 'NoSubscription'
  // because the IAP webhook hasn't synced yet), sync the backend/local
  // session BEFORE navigating away. Skipping the sync here previously caused
  // an infinite redirect loop: (tabs)/_layout bounces 'NoSubscription'
  // sessions to /paywall, this effect bounced straight back to /(tabs)/scan
  // without updating accessType, and (tabs)/_layout would immediately bounce
  // back to /paywall again — repeating forever and showing up to users as a
  // rapid, nonstop "swiping" screen-transition glitch right after login.
  const enteredFromEntitlementRef = useRef(false);
  useEffect(() => {
    if (Platform.OS === 'web') return;
    if (enteredFromEntitlementRef.current) return;
    Purchases.getCustomerInfo().then(async info => {
      const ent = info?.entitlements?.active?.[REVENUECAT_ENTITLEMENT_IDENTIFIER];
      if (!ent) return;
      enteredFromEntitlementRef.current = true;
      if (session?.email && session?.token && session.accessType === 'NoSubscription') {
        const customerId = await Purchases.getAppUserID();
        await syncBackendAndEnter(customerId);
      }
      router.replace('/(tabs)/scan');
    }).catch(() => {});
  }, [session, syncBackendAndEnter]);

  const handlePurchase = async (pkg: PurchasesPackage) => {
    if (Platform.OS === 'web') return;
    setBuyingId(pkg.identifier);
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      const customerInfo = await purchase(pkg);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      const ent = customerInfo?.entitlements?.active?.[REVENUECAT_ENTITLEMENT_IDENTIFIER];
       if (!ent) throw new Error('Apple completed the purchase, but the Pro entitlement is not active yet. Please tap Restore Purchases.');
       const customerId = await Purchases.getAppUserID();

      if (!session?.email) {
        // New user — collect email to create account
         setPendingRevenueCatCustomerId(customerId);
        setShowEmailCapture(true);
        return;
      }

       await syncBackendAndEnter(customerId);
      router.replace('/(tabs)/scan');
    } catch (e: any) {
      if (e?.userCancelled) return;
      Alert.alert('Purchase Failed', getErrorMessage(e));
    } finally {
      setBuyingId(null);
    }
  };

  const handleGuestSignup = async () => {
    const trimmed = guestEmail.trim().toLowerCase();
    if (!trimmed || !trimmed.includes('@')) {
      setGuestEmailError('Please enter a valid email address.');
      return;
    }
    setGuestEmailLoading(true);
    setGuestEmailError('');
    try {
      // Link the anonymous StoreKit purchase to the account identity before
      // asking the server to verify it. The server will then only accept the
      // named customer ID equal to this email.
      await Purchases.logIn(trimmed);
      const customerId = await Purchases.getAppUserID();
      const result = await iapSignup(trimmed, customerId);
      if (result.session_token && result.email) {
        await loginWithResponse({
          email: result.email,
          session_token: result.session_token,
          access_type: result.access_type,
        });
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        router.replace('/(tabs)/scan');
      } else {
        setGuestEmailError(result.message || 'Could not create account. Please try again.');
      }
    } catch (e: unknown) {
      setGuestEmailError(e instanceof Error ? e.message : 'Could not create account. Please try again.');
    } finally {
      setGuestEmailLoading(false);
    }
  };

  const handleRestore = async () => {
    if (Platform.OS === 'web') return;
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      const customerInfo = await restore();
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      const ent = customerInfo?.entitlements?.active?.[REVENUECAT_ENTITLEMENT_IDENTIFIER];
      if (ent) {
        const customerId = await Purchases.getAppUserID();
        if (!session?.email) {
          // New device / not logged in yet — collect email to link the restored purchase
          setPendingRevenueCatCustomerId(customerId);
          setShowEmailCapture(true);
          return;
        }
        await syncBackendAndEnter(customerId);
        router.replace('/(tabs)/scan');
      } else {
        Alert.alert('No Purchases Found', 'There are no active subscriptions to restore.');
      }
    } catch (e: any) {
      Alert.alert('Restore Failed', getErrorMessage(e));
    }
  };

  // ── POST-PURCHASE EMAIL CAPTURE (new users who tapped Sign Up / Plans first) ──
  if (showEmailCapture) {
    return (
      <KeyboardAvoidingView
        style={[styles.root, { paddingTop: insets.top + 20, paddingBottom: insets.bottom + 24 }]}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <ScrollView contentContainerStyle={[styles.inner, { justifyContent: 'center', flex: 1 }]} showsVerticalScrollIndicator={false}>
          <Image source={require('../assets/logo.png')} style={styles.logo} resizeMode="contain" />
          <Text style={styles.headline}>Almost There!</Text>
          <Text style={styles.subhead}>Enter your email to activate your subscription and create your account.</Text>

          <View style={emailStyles.inputRow}>
            <Ionicons name="mail-outline" size={17} color={Colors.textSecondary} style={{ marginRight: 8 }} />
            <TextInput
              style={[emailStyles.input, { outlineWidth: 0 } as any]}
              placeholder="your@email.com"
              placeholderTextColor={Colors.textTertiary}
              value={guestEmail}
              onChangeText={v => { setGuestEmail(v); setGuestEmailError(''); }}
              keyboardType="email-address"
              autoCapitalize="none"
              autoCorrect={false}
              autoComplete="email"
              textContentType="emailAddress"
              returnKeyType="done"
              onSubmitEditing={handleGuestSignup}
              autoFocus
            />
          </View>

          {!!guestEmailError && (
            <View style={emailStyles.errorBox}>
              <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
              <Text style={emailStyles.errorText}>{guestEmailError}</Text>
            </View>
          )}

          <TouchableOpacity
            style={[styles.ctaBtn, guestEmailLoading && styles.ctaBtnDisabled]}
            onPress={handleGuestSignup}
            disabled={guestEmailLoading}
            activeOpacity={0.85}
          >
            {guestEmailLoading ? (
              <ActivityIndicator color="#000" size="small" />
            ) : (
              <Text style={styles.ctaText}>Activate Subscription</Text>
            )}
          </TouchableOpacity>

          <Text style={emailStyles.legalNote}>
            Your email is used only to access your account and for support. We do not share it with third parties.
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    );
  }

  // ── WEB FALLBACK ──
  if (Platform.OS === 'web') {
    return (
      <View style={[styles.root, { paddingTop: insets.top + 40, paddingBottom: insets.bottom + 24 }]}>
        <ScrollView contentContainerStyle={styles.inner} showsVerticalScrollIndicator={false}>
          <Image source={require('../assets/logo.png')} style={styles.logo} resizeMode="contain" />
          <Text style={styles.headline}>Reverse Picks Pro</Text>
          <Text style={styles.subhead}>Get unlimited AI predictions on iOS.</Text>
          {FEATURES.map((f, i) => (
            <View key={i} style={styles.bulletRow}>
              <Ionicons name="checkmark-circle" size={18} color={Colors.primary} />
              <Text style={styles.bulletText}>{f}</Text>
            </View>
          ))}
          <View style={styles.webCard}>
            <Text style={styles.webCardText}>
              Subscriptions are available exclusively through the App Store. Download Reverse Picks on your iPhone to subscribe and unlock full access.
            </Text>
          </View>
        </ScrollView>
      </View>
    );
  }

  // ── NATIVE PAYWALL ──
  return (
    <View style={[styles.root, { paddingTop: insets.top + 20, paddingBottom: insets.bottom + 24 }]}>
      <ScrollView contentContainerStyle={styles.inner} showsVerticalScrollIndicator={false}>

        {/* Close / back to login */}
        <TouchableOpacity
          style={styles.closeBtn}
          onPress={async () => { await logout(); router.replace('/auth'); }}
          activeOpacity={0.7}
        >
          <Ionicons name="close" size={22} color={Colors.textSecondary} />
        </TouchableOpacity>

        {/* Logo */}
        <Image source={require('../assets/logo.png')} style={styles.logo} resizeMode="contain" />

        {/* Headline */}
        <Text style={styles.headline}>Unlock Full Access</Text>
        <Text style={styles.subhead}>AI-powered player prop predictions for every match</Text>

        {/* Feature bullets */}
        <View style={styles.bulletWrap}>
          {FEATURES.map((f, i) => (
            <View key={i} style={styles.bulletRow}>
              <Ionicons name="checkmark-circle" size={18} color={Colors.primary} />
              <Text style={styles.bulletText}>{f}</Text>
            </View>
          ))}
        </View>

        {/* Plans */}
        {isLoading ? (
          <View style={styles.loader}>
            <ActivityIndicator color={Colors.primary} />
            <Text style={styles.loaderText}>Loading plans…</Text>
          </View>
        ) : packages.length === 0 ? (
          <View style={styles.loader}>
            <Ionicons name="cloud-offline-outline" size={32} color={Colors.textTertiary} />
            <Text style={styles.loaderText}>Plans unavailable</Text>
            <Text style={[styles.loaderText, { fontSize: 11, marginTop: -4 }]}>
              Check App Store agreements & product metadata, then retry
            </Text>
            <TouchableOpacity
              style={[styles.retryBtn, { marginTop: 12 }]}
              onPress={() => refetchOfferings()}
              activeOpacity={0.8}
            >
              <Ionicons name="refresh" size={14} color={Colors.primary} />
              <Text style={styles.retryText}>Retry</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <View style={styles.plansWrap}>
            {packages.map((pkg) => {
              const isSelected = selectedPkg?.identifier === pkg.identifier;
              const isBuying = buyingId === pkg.identifier;
              const title = pkg.product?.title ?? pkg.packageType ?? pkg.identifier;
              const price = pkg.product?.priceString ?? '—';
              const periodRaw = pkg.product?.subscriptionPeriod ?? '';
              const period = isoPeriodToLabel(periodRaw);
              const desc = pkg.product?.description ?? '';
              return (
                <TouchableOpacity
                  key={pkg.identifier}
                  style={[styles.planCard, isSelected && styles.planCardActive]}
                  onPress={() => setSelectedPkg(pkg)}
                  activeOpacity={0.8}
                  disabled={!!buyingId || isRestoring}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={styles.planTitle}>{title}</Text>
                    {period ? <Text style={styles.planPeriod}>{period}</Text> : null}
                  </View>
                  <View style={{ alignItems: 'flex-end' }}>
                    <Text style={styles.planPrice}>{price}</Text>
                    {isSelected && (
                      <View style={styles.checkDot}>
                        <Ionicons name="checkmark" size={12} color="#000" />
                      </View>
                    )}
                  </View>
                </TouchableOpacity>
              );
            })}
          </View>
        )}

        {/* CTA */}
        <TouchableOpacity
          style={[
            styles.ctaBtn,
            (!selectedPkg || isPurchasing || isRestoring) && styles.ctaBtnDisabled,
          ]}
          onPress={() => selectedPkg && handlePurchase(selectedPkg)}
          disabled={!selectedPkg || isPurchasing || isRestoring}
          activeOpacity={0.85}
        >
          {isPurchasing || !!buyingId ? (
            <ActivityIndicator color="#000" size="small" />
          ) : (
            <Text style={styles.ctaText}>
              {selectedPkg ? 'Continue' : 'Select a plan'}
            </Text>
          )}
        </TouchableOpacity>

        {/* Restore */}
        <TouchableOpacity
          style={styles.restoreWrap}
          onPress={handleRestore}
          disabled={isRestoring || !!buyingId}
          activeOpacity={0.7}
        >
          {isRestoring ? (
            <ActivityIndicator size="small" color={Colors.textSecondary} />
          ) : (
            <Text style={styles.restoreText}>Restore Purchases</Text>
          )}
        </TouchableOpacity>

        {/* Log out & back to sign-in */}
        <TouchableOpacity
          style={[styles.restoreWrap, { paddingTop: 2 }]}
          onPress={async () => { await logout(); router.replace('/auth'); }}
          activeOpacity={0.7}
        >
          <Text style={[styles.restoreText, { color: Colors.textTertiary, fontSize: 12 }]}>Log out and go back</Text>
        </TouchableOpacity>

        {/* Legal links — required by App Store Guideline 3.1.2(c) */}
        <View style={styles.legalRow}>
          <TouchableOpacity onPress={() => router.push('/terms')} activeOpacity={0.7} hitSlop={10} style={{ paddingVertical: 8, paddingHorizontal: 6 }}>
            <Text style={styles.legalLink}>Terms of Use</Text>
          </TouchableOpacity>
          <Text style={styles.legalSep}>·</Text>
          <TouchableOpacity onPress={() => router.push('/privacy')} activeOpacity={0.7} hitSlop={10} style={{ paddingVertical: 8, paddingHorizontal: 6 }}>
            <Text style={styles.legalLink}>Privacy Policy</Text>
          </TouchableOpacity>
          <Text style={styles.legalSep}>·</Text>
          <TouchableOpacity onPress={() => Linking.openURL('https://www.apple.com/legal/internet-services/itunes/dev/stdeula/')} activeOpacity={0.7} hitSlop={10} style={{ paddingVertical: 8, paddingHorizontal: 6 }}>
            <Text style={styles.legalLink}>EULA</Text>
          </TouchableOpacity>
        </View>

        {/* Apple disclosure */}
        <Text style={styles.disclosure}>
          Subscription automatically renews at the displayed price unless cancelled at least 24 hours before the end of the current period. Payment is charged to your Apple ID account at confirmation of purchase. Manage or cancel anytime in Apple Settings.
        </Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  inner: {
    paddingHorizontal: 24,
    paddingBottom: 20,
    alignItems: 'center',
    gap: 20,
  },
  closeBtn: {
    position: 'absolute', top: 0, right: 0,
    width: 44, height: 44, alignItems: 'center', justifyContent: 'center', zIndex: 10,
  },
  logo: { width: 64, height: 64, marginTop: 8 },
  headline: {
    color: Colors.text,
    fontSize: 26,
    fontWeight: '800',
    textAlign: 'center',
    letterSpacing: 0.3,
  },
  subhead: {
    color: Colors.textSecondary,
    fontSize: 14,
    textAlign: 'center',
    lineHeight: 20,
    marginTop: -8,
  },
  bulletWrap: { width: '100%', gap: 10, marginTop: 4 },
  bulletRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  bulletText: { color: Colors.text, fontSize: 14, flex: 1, lineHeight: 20 },
  plansWrap: { width: '100%', gap: 10, marginTop: 4 },
  planCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.card,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: 16,
    gap: 12,
  },
  planCardActive: {
    borderColor: Colors.primary,
    backgroundColor: 'rgba(57,255,20,0.06)',
  },
  planTitle: { color: Colors.text, fontSize: 15, fontWeight: '700' },
  planDesc: { color: Colors.textSecondary, fontSize: 12, marginTop: 2 },
  planPeriod: { color: Colors.textTertiary, fontSize: 11, marginTop: 2 },
  planPrice: { color: Colors.text, fontSize: 16, fontWeight: '800' },
  checkDot: {
    width: 20, height: 20, borderRadius: 10,
    backgroundColor: Colors.primary,
    alignItems: 'center', justifyContent: 'center',
    marginTop: 4,
  },
  ctaBtn: {
    width: '100%',
    backgroundColor: Colors.primary,
    borderRadius: 14,
    height: 56,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 8,
    shadowColor: Colors.primary,
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.35,
    shadowRadius: 16,
    elevation: 8,
  },
  ctaBtnDisabled: { opacity: 0.5 },
  ctaText: { color: '#000', fontWeight: '800', fontSize: 16, letterSpacing: 0.5 },
  restoreWrap: { paddingVertical: 8, alignItems: 'center' },
  restoreText: { color: Colors.textSecondary, fontSize: 14, fontWeight: '600' },
  legalRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    marginTop: 4,
  },
  legalLink: {
    color: Colors.textSecondary,
    fontSize: 12,
    fontWeight: '600',
    textDecorationLine: 'underline',
  },
  legalSep: {
    color: Colors.textTertiary,
    fontSize: 12,
  },
  disclosure: {
    color: Colors.textTertiary,
    fontSize: 10,
    textAlign: 'center',
    lineHeight: 16,
    marginTop: 4,
    paddingHorizontal: 8,
  },
  retryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: 10,
    paddingVertical: 8,
    paddingHorizontal: 16,
  },
  retryText: {
    color: Colors.primary,
    fontSize: 13,
    fontWeight: '700',
  },
  loader: { alignItems: 'center', gap: 8, paddingVertical: 20 },
  loaderText: { color: Colors.textSecondary, fontSize: 13 },
  webCard: {
    backgroundColor: Colors.card,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: 20,
    marginTop: 8,
    width: '100%',
  },
  webCardText: { color: Colors.textSecondary, fontSize: 13, lineHeight: 20, textAlign: 'center' },
});

const emailStyles = StyleSheet.create({
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.card,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: 14,
    height: 52,
    width: '100%',
  },
  input: {
    flex: 1,
    color: Colors.text,
    fontSize: 15,
  },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(239,68,68,0.1)',
    borderRadius: 8,
    padding: 10,
    width: '100%',
  },
  errorText: { color: '#ef4444', fontSize: 13, flex: 1 },
  legalNote: {
    color: Colors.textTertiary,
    fontSize: 11,
    textAlign: 'center',
    lineHeight: 16,
    paddingHorizontal: 8,
  },
});
