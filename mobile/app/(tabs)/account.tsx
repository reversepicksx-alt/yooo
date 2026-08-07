import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, TextInput, StyleSheet, ScrollView, TouchableOpacity,
  Alert, Platform, Image, Modal, ActivityIndicator, Linking, Animated,
  KeyboardAvoidingView,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import * as ImagePicker from 'expo-image-picker';
import NotificationBell from '@/components/NotificationBell';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import { getUserProfile, setUsername, setProfileImage, getDmInbox, getQuotaStatus, resetQuotaBreaker, type QuotaStatus } from '@/lib/api';
import {
  getSubscriptionStatus, cancelSubscription, changePlan,
  resubscribeCheckout, PLAN_OPTIONS, deleteAccount, type SubscriptionStatus,
} from '@/lib/api';
import { useSubscription } from '@/lib/revenuecat';
import Purchases, { type PurchasesPackage } from 'react-native-purchases';

// ── Skeleton loader ────────────────────────────────────────────────────────────
function SkeletonLine({ w, h = 14, mt = 0 }: { w: string | number; h?: number; mt?: number }) {
  const anim = useRef(new Animated.Value(0.4)).current;
  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(anim, { toValue: 1,   duration: 800, useNativeDriver: true }),
        Animated.timing(anim, { toValue: 0.4, duration: 800, useNativeDriver: true }),
      ])
    ).start();
  }, []);
  return (
    <Animated.View style={{
      width: w as any, height: h, borderRadius: h / 2,
      backgroundColor: '#2a2a2a', marginTop: mt, opacity: anim,
    }} />
  );
}

function AccountSkeleton() {
  return (
    <View style={{ paddingHorizontal: 20, paddingTop: 20, gap: 24 }}>
      {/* profile card skeleton */}
      <View style={{ flexDirection: 'row', gap: 16, alignItems: 'center', marginBottom: 4 }}>
        <View style={{ width: 56, height: 56, borderRadius: 28, backgroundColor: '#2a2a2a' }} />
        <View style={{ flex: 1, gap: 8 }}>
          <SkeletonLine w="60%" h={14} />
          <SkeletonLine w="35%" h={10} />
        </View>
      </View>
      {/* section label */}
      <SkeletonLine w="25%" h={10} />
      {/* menu group */}
      <View style={{ backgroundColor: '#111', borderRadius: 16, borderWidth: 1, borderColor: '#1e1e1e', overflow: 'hidden', gap: 0 }}>
        {[1, 2].map(i => (
          <View key={i} style={{ flexDirection: 'row', alignItems: 'center', padding: 16, gap: 14, borderBottomWidth: 1, borderBottomColor: '#1e1e1e' }}>
            <View style={{ width: 34, height: 34, borderRadius: 8, backgroundColor: '#1c1c1c' }} />
            <View style={{ flex: 1, gap: 6 }}>
              <SkeletonLine w="40%" h={13} />
              <SkeletonLine w="55%" h={10} />
            </View>
          </View>
        ))}
      </View>
      {/* subscription section */}
      <SkeletonLine w="30%" h={10} />
      <View style={{ backgroundColor: '#111', borderRadius: 16, borderWidth: 1, borderColor: '#1e1e1e', overflow: 'hidden' }}>
        {[1, 2, 3].map(i => (
          <View key={i} style={{ flexDirection: 'row', alignItems: 'center', padding: 16, gap: 14, borderBottomWidth: 1, borderBottomColor: '#1e1e1e' }}>
            <View style={{ width: 34, height: 34, borderRadius: 8, backgroundColor: '#1c1c1c' }} />
            <View style={{ flex: 1, gap: 6 }}>
              <SkeletonLine w="45%" h={13} />
              <SkeletonLine w="60%" h={10} />
            </View>
          </View>
        ))}
      </View>
    </View>
  );
}

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === 'string') return err;
  return 'Something went wrong. Please try again.';
}

interface MenuRowProps {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  value?: string;
  valueColor?: string;
  onPress?: () => void;
  danger?: boolean;
  loading?: boolean;
}

function MenuRow({ icon, label, value, valueColor, onPress, danger, loading }: MenuRowProps) {
  return (
    <TouchableOpacity
      style={styles.menuRow}
      onPress={onPress}
      activeOpacity={onPress ? 0.7 : 1}
      disabled={!onPress || loading}
    >
      <View style={[styles.menuIcon, danger && styles.menuIconDanger]}>
        <Ionicons name={icon} size={18} color={danger ? Colors.error : Colors.primary} />
      </View>
      <View style={styles.menuContent}>
        <Text style={[styles.menuLabel, danger && styles.menuLabelDanger]}>{label}</Text>
        {value && <Text style={[styles.menuValue, valueColor ? { color: valueColor } : undefined]}>{value}</Text>}
      </View>
      {loading ? (
        <ActivityIndicator size="small" color={Colors.primary} />
      ) : onPress ? (
        <Ionicons name="chevron-forward" size={16} color={Colors.textTertiary} />
      ) : null}
    </TouchableOpacity>
  );
}

function InstructionStep({
  number,
  title,
  text,
  last = false,
}: {
  number: string;
  title: string;
  text: string;
  last?: boolean;
}) {
  return (
    <View style={[styles.instructionStep, !last && styles.instructionStepBorder]}>
      <View style={styles.instructionNumber}>
        <Text style={styles.instructionNumberText}>{number}</Text>
      </View>
      <View style={styles.instructionCopy}>
        <Text style={styles.instructionStepTitle}>{title}</Text>
        <Text style={styles.instructionStepText}>{text}</Text>
      </View>
    </View>
  );
}

function formatDate(iso?: string): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return '—';
  }
}

function formatExpiryDate(ms?: number): string {
  if (!ms) return '—';
  try {
    const d = new Date(ms);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return '—';
  }
}

// ── Apple IAP Paywall (iOS native only) ────────────────────────────────────

function IAPPaywall() {
  const { packages, isLoading, purchase, restore, isPurchasing, isRestoring } = useSubscription();
  const { email, session, loginWithResponse } = useAuth();
  const [buyingId, setBuyingId] = useState<string | null>(null);
  const [confirmPkg, setConfirmPkg] = useState<PurchasesPackage | null>(null);

  const grantBackend = async () => {
    if (!email || !session?.token) return;
    const customerId = await Purchases.getAppUserID();
    const response = await fetch('/api/auth/iap-grant', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, session_token: session.token, revenuecat_customer_id: customerId }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(body?.detail || 'Apple subscription verification failed. Please try again.');
    }
    const result = await response.json().catch(() => null);
    if (result?.access_type) {
      await loginWithResponse({
        email,
        session_token: session.token,
        access_type: result.access_type,
      });
    }
  };

  const handlePurchase = async (pkg: PurchasesPackage) => {
    setBuyingId(pkg.identifier);
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      const customerInfo = await purchase(pkg);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      if (!customerInfo?.entitlements?.active?.['pro']) {
        throw new Error('Apple completed the purchase, but the Pro entitlement is not active yet. Please tap Restore Purchases.');
      }
      await grantBackend();
      Alert.alert('Subscribed!', 'Welcome to Reverse Picks Pro. Your subscription is now active.');
    } catch (e: any) {
      if (e?.userCancelled) return;
      Alert.alert('Purchase Failed', getErrorMessage(e));
    } finally {
      setBuyingId(null);
      setConfirmPkg(null);
    }
  };

  const handleRestore = async () => {
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      const customerInfo = await restore();
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      if (customerInfo?.entitlements?.active?.['pro']) await grantBackend();
      Alert.alert('Restored', 'Your purchases have been restored.');
    } catch (e: any) {
      Alert.alert('Restore Failed', getErrorMessage(e));
    }
  };

  if (isLoading) {
    return (
      <View style={styles.paywallLoading}>
        <ActivityIndicator size="small" color={Colors.primary} />
        <Text style={styles.paywallLoadingText}>Loading plans…</Text>
      </View>
    );
  }

  return (
    <>
      {/* Confirm purchase modal */}
      <Modal visible={!!confirmPkg} transparent animationType="fade" onRequestClose={() => setConfirmPkg(null)}>
        <TouchableOpacity style={styles.modalOverlay} activeOpacity={1} onPress={() => setConfirmPkg(null)}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHandle} />
            <Text style={styles.modalTitle}>Confirm Purchase</Text>
            <Text style={styles.modalSubtitle}>
              {confirmPkg?.product?.title} — {confirmPkg?.product?.priceString}
            </Text>
            <TouchableOpacity
              style={[styles.buyBtn, { opacity: isPurchasing ? 0.6 : 1 }]}
              onPress={() => confirmPkg && handlePurchase(confirmPkg)}
              disabled={isPurchasing}
              activeOpacity={0.8}
            >
              {isPurchasing ? (
                <ActivityIndicator size="small" color={Colors.background} />
              ) : (
                <Text style={styles.buyBtnText}>Subscribe via Apple</Text>
              )}
            </TouchableOpacity>
            <TouchableOpacity style={styles.modalCancel} onPress={() => setConfirmPkg(null)}>
              <Text style={styles.modalCancelText}>Cancel</Text>
            </TouchableOpacity>
            <Text style={styles.paywallDisclosure}>
              Subscription auto-renews at the same price unless cancelled at least 24 hours before the end of the current period. Manage or cancel anytime in Apple Settings.
            </Text>
          </View>
        </TouchableOpacity>
      </Modal>

      <View style={styles.paywallHeader}>
        <Ionicons name="flash" size={28} color={Colors.primary} />
        <Text style={styles.paywallTitle}>Reverse Picks Pro</Text>
        <Text style={styles.paywallSub}>Model-powered soccer player props analytics</Text>
      </View>

      <View style={styles.menuGroup}>
        {packages.length === 0 ? (
          <View style={styles.paywallEmpty}>
            <Text style={styles.paywallEmptyText}>No plans available. Try again later.</Text>
          </View>
        ) : (
          packages.map((pkg) => {
            const isBuying = buyingId === pkg.identifier;
            const priceStr = pkg.product?.priceString ?? '—';
            const title = pkg.product?.title ?? pkg.packageType ?? pkg.identifier;
            const desc = pkg.product?.description ?? '';
            return (
              <TouchableOpacity
                key={pkg.identifier}
                style={styles.planOption}
                onPress={() => setConfirmPkg(pkg)}
                activeOpacity={0.7}
                disabled={!!buyingId || isRestoring}
              >
                <View style={styles.planInfo}>
                  <Text style={styles.planName}>{title}</Text>
                  {desc ? <Text style={styles.planDesc}>{desc}</Text> : null}
                </View>
                <View style={styles.planRight}>
                  <Text style={styles.planPrice}>{priceStr}</Text>
                  {isBuying ? (
                    <ActivityIndicator size="small" color={Colors.primary} style={{ marginTop: 4 }} />
                  ) : (
                    <Ionicons name="chevron-forward" size={16} color={Colors.textTertiary} />
                  )}
                </View>
              </TouchableOpacity>
            );
          })
        )}
      </View>

      <TouchableOpacity
        style={styles.restoreBtn}
        onPress={handleRestore}
        disabled={isRestoring || !!buyingId}
        activeOpacity={0.7}
      >
        {isRestoring ? (
          <ActivityIndicator size="small" color={Colors.textSecondary} />
        ) : (
          <Text style={styles.restoreBtnText}>Restore Purchases</Text>
        )}
      </TouchableOpacity>

      <Text style={styles.paywallDisclosure}>
        Subscriptions automatically renew unless cancelled at least 24 hours before the end of the current period. Payment is charged to your Apple ID account at confirmation of purchase. Manage or cancel at any time in
        <Text style={{ color: Colors.primary }} onPress={() => Linking.openURL('https://apps.apple.com/account/subscriptions')}>
          Apple Settings
        </Text>
        .
      </Text>
    </>
  );
}

// ── Active IAP Subscription info ────────────────────────────────────────────

function IAPSubscriptionInfo() {
  const { customerInfo, restore, isRestoring } = useSubscription();
  const entitlement = customerInfo?.entitlements.active?.['pro'];

  const handleManage = () => {
    Linking.openURL('https://apps.apple.com/account/subscriptions');
  };

  const handleRestore = async () => {
    try {
      await restore();
      Alert.alert('Restored', 'Your subscription status has been refreshed.');
    } catch (e: any) {
      Alert.alert('Error', getErrorMessage(e));
    }
  };

  return (
    <View style={styles.menuGroup}>
      <MenuRow
        icon="checkmark-circle-outline"
        label="Status"
        value="Active"
        valueColor={Colors.primary}
      />
      {entitlement?.expirationDate && (
        <MenuRow
          icon="calendar-outline"
          label="Renews"
          value={formatExpiryDate(
            typeof entitlement.expirationDate === 'string'
              ? new Date(entitlement.expirationDate).getTime()
              : entitlement.expirationDate
          )}
        />
      )}
      <MenuRow
        icon="settings-outline"
        label="Manage Subscription"
        value="Manage in Apple Settings"
        onPress={handleManage}
      />
      <MenuRow
        icon="refresh-outline"
        label="Restore Purchases"
        onPress={handleRestore}
        loading={isRestoring}
      />
    </View>
  );
}

// ── Plan picker (web / Stripe) ───────────────────────────────────────────────

function PlanPickerModal({
  visible, currentPlanKey, loading, onSelect, onClose, isResubscribe,
}: {
  visible: boolean;
  currentPlanKey?: string;
  loading: boolean;
  onSelect: (key: string) => void;
  onClose: () => void;
  isResubscribe?: boolean;
}) {
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <TouchableOpacity style={styles.modalOverlay} activeOpacity={1} onPress={onClose}>
        <View style={styles.modalSheet}>
          <View style={styles.modalHandle} />
          <Text style={styles.modalTitle}>{isResubscribe ? 'Resubscribe' : 'Change Plan'}</Text>
          <Text style={styles.modalSubtitle}>{isResubscribe ? 'Choose a plan to resubscribe' : 'Select a new billing cycle'}</Text>

          {PLAN_OPTIONS.map((plan) => {
            const isCurrent = plan.key === currentPlanKey;
            return (
              <TouchableOpacity
                key={plan.key}
                style={[styles.planOption, isCurrent && styles.planOptionCurrent]}
                onPress={() => !isCurrent && !loading && onSelect(plan.key)}
                activeOpacity={isCurrent ? 1 : 0.7}
                disabled={isCurrent || loading}
              >
                <View style={styles.planInfo}>
                  <Text style={[styles.planName, isCurrent && styles.planNameCurrent]}>{plan.name}</Text>
                  <Text style={styles.planPrice}>{plan.price}</Text>
                </View>
                {isCurrent ? (
                  <View style={styles.currentBadge}>
                    <Text style={styles.currentBadgeText}>CURRENT</Text>
                  </View>
                ) : loading ? (
                  <ActivityIndicator size="small" color={Colors.primary} />
                ) : (
                  <Ionicons name="chevron-forward" size={18} color={Colors.textTertiary} />
                )}
              </TouchableOpacity>
            );
          })}

          <TouchableOpacity style={styles.modalCancel} onPress={onClose}>
            <Text style={styles.modalCancelText}>Cancel</Text>
          </TouchableOpacity>
        </View>
      </TouchableOpacity>
    </Modal>
  );
}

// ── Main screen ──────────────────────────────────────────────────────────────

export default function AccountScreen() {
  const insets = useSafeAreaInsets();
  const { session, logout } = useAuth();
  const topPad = Platform.OS === 'web' ? 67 : insets.top;
  const bottomPad = Platform.OS === 'web' ? 34 : insets.bottom;

  const [subStatus, setSubStatus] = useState<SubscriptionStatus | null>(null);
  const [subLoading, setSubLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [planPickerVisible, setPlanPickerVisible] = useState(false);

  // Profile state
  const [profile, setProfile] = useState<{ username: string | null; profileImage?: string | null }>({ username: null });
  const [usernameModal, setUsernameModal] = useState(false);
  const [usernameInput, setUsernameInput] = useState('');
  const [usernameLoading, setUsernameLoading] = useState(false);
  const [usernameError, setUsernameError] = useState('');
  const [instructionsOpen, setInstructionsOpen] = useState(false);

  // Owner: API-Football quota breaker state
  const [quotaStatus, setQuotaStatus] = useState<QuotaStatus | null>(null);
  const [quotaResetting, setQuotaResetting] = useState(false);
  const [quotaMsg, setQuotaMsg] = useState('');

  // RevenueCat state (iOS native only)
  const { isSubscribed: hasIAP, isLoading: iapLoading } = useSubscription();

  const isStripeSub = session?.accessType?.toLowerCase().includes('stripe');
  const isLifetime = session?.accessType?.toLowerCase().includes('lifetime');
  const isOwner = session?.accessType?.toLowerCase() === 'owner';

  // On iOS native: subscription section is always IAP-driven
  const isIOSNative = Platform.OS === 'ios';

  // Profile image state
  const [imageUploading, setImageUploading] = useState(false);
  const [dmUnreadCount, setDmUnreadCount] = useState(0);

  // On web/android: show Stripe management for active Stripe subscribers
  const showStripeManagement = !isIOSNative && isStripeSub && !isLifetime && !isOwner;

  const fetchSubStatus = useCallback(async () => {
    if (!session?.email || !showStripeManagement) return;
    setSubLoading(true);
    try {
      const status = await getSubscriptionStatus(session.email, session.accessType);
      setSubStatus(status);
    } catch {
      setSubStatus(null);
    } finally {
      setSubLoading(false);
    }
  }, [session?.email, showStripeManagement]);

  useEffect(() => {
    fetchSubStatus();
  }, [fetchSubStatus]);

  // Owner: fetch quota breaker status on mount
  const fetchQuotaStatus = useCallback(async () => {
    if (!isOwner || !session?.email || !session?.token) return;
    try {
      const s = await getQuotaStatus(session.email, session.token);
      setQuotaStatus(s);
    } catch {
      // non-critical — leave state null
    }
  }, [isOwner, session?.email, session?.token]);

  useEffect(() => {
    fetchQuotaStatus();
  }, [fetchQuotaStatus]);

  const handleQuotaReset = async () => {
    if (!session?.email || !session?.token) return;
    setQuotaResetting(true);
    setQuotaMsg('');
    try {
      const res = await resetQuotaBreaker(session.email, session.token);
      setQuotaMsg(res.message);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      // Refresh status after reset
      await fetchQuotaStatus();
    } catch (e: unknown) {
      setQuotaMsg(e instanceof Error ? e.message : 'Reset failed. Try again.');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setQuotaResetting(false);
    }
  };

  // Poll DM inbox for unread count
  useEffect(() => {
    if (!session?.email) return;
    const refresh = async () => {
      try {
        const convs = await getDmInbox(session.email);
        const total = convs.reduce((s, c) => s + (c.unreadCount || 0), 0);
        setDmUnreadCount(total);
      } catch {}
    };
    refresh();
    const id = setInterval(refresh, 30_000);
    return () => clearInterval(id);
  }, [session?.email]);

  // Load user profile (username)
  useEffect(() => {
    if (!session?.email) return;
    getUserProfile(session.email)
      .then((p) => setProfile(p))
      .catch(() => setProfile({ username: null }));
  }, [session?.email]);

  const handlePickProfileImage = async () => {
    if (!session?.email) return;
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Permission needed', 'Allow photo library access to change your profile picture.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.6,
      base64: true,
      allowsEditing: true,
      aspect: [1, 1],
    });
    if (result.canceled || !result.assets?.[0]?.base64) return;
    setImageUploading(true);
    try {
      const base64 = `data:image/jpeg;base64,${result.assets[0].base64}`;
      const res = await setProfileImage(session.email, base64);
      if (res.ok) {
        setProfile((prev) => ({ ...prev, profileImage: res.profileImage }));
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      }
    } catch {
      Alert.alert('Upload failed', 'Could not upload profile image. Try again.');
    } finally {
      setImageUploading(false);
    }
  };

  const handleSetUsername = async () => {
    if (!session?.email || !usernameInput.trim()) return;
    setUsernameLoading(true);
    setUsernameError('');
    try {
      const res = await setUsername(session.email, usernameInput.trim());
      if (res.ok) {
        setProfile((prev) => ({ ...prev, username: res.username }));
        setUsernameModal(false);
        setUsernameInput('');
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      } else {
        setUsernameError(res.message || 'Could not save username');
      }
    } catch (e: any) {
      setUsernameError(e?.message || 'Something went wrong');
    } finally {
      setUsernameLoading(false);
    }
  };

  const handleCancel = async () => {
    const doCancel = async () => {
      if (!session?.email) return;
      setActionLoading(true);
      try {
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
        await cancelSubscription(session.email, session.accessType);
        await fetchSubStatus();
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      } catch (e: unknown) {
        const msg = getErrorMessage(e);
        if (Platform.OS === 'web') {
          window.alert(msg);
        } else {
          Alert.alert('Error', msg);
        }
      } finally {
        setActionLoading(false);
      }
    };

    if (Platform.OS === 'web') {
      if (typeof window !== 'undefined' &&
        window.confirm('Cancel your subscription? You\'ll keep access until your current billing period ends.')) {
        await doCancel();
      }
    } else {
      Alert.alert(
        'Cancel Subscription',
        'You\'ll keep access until your current billing period ends. Are you sure?',
        [
          { text: 'Keep Plan', style: 'cancel' },
          { text: 'Cancel Subscription', style: 'destructive', onPress: doCancel },
        ]
      );
    }
  };

  const handleChangePlan = async (newKey: string) => {
    if (!session?.email) return;
    setActionLoading(true);
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      await changePlan(session.email, newKey, session.accessType);
      await fetchSubStatus();
      setPlanPickerVisible(false);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      const msg = getErrorMessage(e);
      if (Platform.OS === 'web') {
        window.alert(msg);
      } else {
        Alert.alert('Error', msg);
      }
    } finally {
      setActionLoading(false);
    }
  };

  const handleResubscribePlan = async (planKey: string) => {
    if (!session?.email) return;
    setActionLoading(true);
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      const result = await resubscribeCheckout(session.email, planKey, 'stripe');
      const url = result.checkoutUrl || result.checkout_url || result.redirect_url;
      if (url) {
        setPlanPickerVisible(false);
        if (Platform.OS === 'web' && typeof window !== 'undefined') {
          window.location.href = url;
        } else {
          await Linking.openURL(url);
        }
      } else {
        throw new Error('Could not create checkout. Please try again.');
      }
    } catch (e: unknown) {
      const msg = getErrorMessage(e);
      if (Platform.OS === 'web') {
        window.alert(msg);
      } else {
        Alert.alert('Error', msg);
      }
    } finally {
      setActionLoading(false);
    }
  };

  const handleLogout = async () => {
    const goToSignIn = () => {
      // Do not rely on the tab layout's async auth guard after clearing the
      // session. On web that can leave the protected tab shell mounted on a
      // dark screen while the redirect is still settling.
      router.replace('/auth');
    };
    if (Platform.OS === 'web') {
      if (typeof window !== 'undefined' && !window.confirm('Sign out of Reverse Picks?')) return;
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
      await logout();
      goToSignIn();
    } else {
      Alert.alert('Sign Out', 'Are you sure you want to sign out?', [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Sign Out',
          style: 'destructive',
          onPress: async () => {
            await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
            await logout();
            goToSignIn();
          },
        },
      ]);
    }
  };

  const handleDeleteAccount = () => {
    const doDelete = async () => {
      if (!session?.email || !session?.token) return;
      try {
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
        await deleteAccount(session.email, session.token);
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        await logout();
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : 'Failed to delete account. Please try again.';
        if (Platform.OS === 'web') {
          window.alert(msg);
        } else {
          Alert.alert('Error', msg);
        }
      }
    };

    if (Platform.OS === 'web') {
      if (
        typeof window !== 'undefined' &&
        window.confirm(
          'Permanently delete your account?\n\nThis will erase all your picks and subscription data. This cannot be undone.',
        )
      ) {
        doDelete();
      }
    } else {
      Alert.alert(
        'Delete Account',
        'This permanently deletes your account, all saved picks, and subscription history. This cannot be undone.',
        [
          { text: 'Cancel', style: 'cancel' },
          {
            text: 'Delete Account',
            style: 'destructive',
            onPress: () => {
              Alert.alert(
                'Are you sure?',
                'Your account and all data will be permanently deleted.',
                [
                  { text: 'Cancel', style: 'cancel' },
                  { text: 'Yes, Delete', style: 'destructive', onPress: doDelete },
                ],
              );
            },
          },
        ],
      );
    }
  };

  const initials = session?.email
    ? session.email.slice(0, 2).toUpperCase()
    : 'RP';

  const accessLabel = session?.accessType
    ? session.accessType.charAt(0).toUpperCase() + session.accessType.slice(1)
    : 'Active';

  const isCanceled = subStatus?.status === 'CANCELED';
  const statusLabel = isCanceled
    ? `Cancels ${formatDate(subStatus?.expiresAt)}`
    : subStatus?.status === 'ACTIVE'
      ? 'Active'
      : subStatus?.status || '—';
  const statusColor = isCanceled ? '#f59e0b' : Colors.primary;

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      <View style={[styles.header, { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingRight: 16 }]}>
        <Text style={styles.headerTitle}>Account</Text>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <TouchableOpacity onPress={() => router.push('/dm')} style={styles.headerIcon}>
            <Ionicons
              name={dmUnreadCount > 0 ? 'mail' : 'mail-outline'}
              size={22}
              color={dmUnreadCount > 0 ? Colors.primary : Colors.text}
            />
            {dmUnreadCount > 0 && (
              <View style={styles.dmBadge}>
                <Text style={styles.dmBadgeText}>{dmUnreadCount > 99 ? '99+' : String(dmUnreadCount)}</Text>
              </View>
            )}
          </TouchableOpacity>
          <NotificationBell />
        </View>
      </View>
      <ScrollView contentContainerStyle={[styles.body, { paddingBottom: bottomPad + 20 }]}>
        <View style={styles.profileCard}>
          <TouchableOpacity
            style={[styles.avatar, { position: 'relative' }]}
            onPress={handlePickProfileImage}
            activeOpacity={0.8}
          >
            {profile.profileImage ? (
              <Image source={{ uri: profile.profileImage }} style={{ width: 56, height: 56, borderRadius: 28 }} />
            ) : (
              <Text style={styles.avatarText}>{initials}</Text>
            )}
            <View style={styles.avatarEditBadge}>
              <Ionicons name="camera" size={12} color={Colors.background} />
            </View>
          </TouchableOpacity>
          <View style={styles.profileInfo}>
            <Text style={styles.profileEmail} numberOfLines={1}>
              {profile.username ? `@${profile.username}` : (session?.email?.split('@')[0] || 'User')}
            </Text>
            <View style={styles.accessBadge}>
              <Ionicons name="shield-checkmark" size={11} color={Colors.primary} />
              <Text style={styles.accessText}>{accessLabel}</Text>
            </View>
          </View>
        </View>

        <Text style={styles.sectionLabel}>Account</Text>
        <View style={styles.menuGroup}>
          <MenuRow icon="mail-outline" label="Email" value={session?.email} />
          <MenuRow
            icon="at-outline"
            label="Username"
            value={profile.username ? `@${profile.username}` : 'Set username'}
            valueColor={profile.username ? Colors.primary : Colors.textTertiary}
            onPress={() => setUsernameModal(true)}
          />
          <MenuRow icon="shield-outline" label="Access Level" value={accessLabel} />
          {!isOwner && (
            <MenuRow
              icon="chatbubble-outline"
              label="Message Owner"
              value="Get help & support"
              onPress={() => {
                const ownerEmail = 'reversepicksx@gmail.com';
                router.push(`/dm-thread?email=${encodeURIComponent(ownerEmail)}&name=Reverse%20Picks%20Support&image=`);
              }}
            />
          )}
        </View>

        <Text style={styles.sectionLabel}>Getting Started</Text>
        <View style={styles.instructionsCard}>
          <TouchableOpacity
            style={styles.instructionsHeader}
            onPress={() => {
              setInstructionsOpen((open) => !open);
              Haptics.selectionAsync();
            }}
            activeOpacity={0.75}
          >
            <View style={styles.instructionsIcon}>
              <Ionicons name="help-circle-outline" size={20} color={Colors.primary} />
            </View>
            <View style={styles.instructionsTitleWrap}>
              <Text style={styles.instructionsTitle}>How to make a prediction</Text>
              <Text style={styles.instructionsSubtitle}>Find a player, choose a prop, and run the analysis</Text>
            </View>
            <Ionicons
              name={instructionsOpen ? 'chevron-up' : 'chevron-down'}
              size={18}
              color={Colors.textTertiary}
            />
          </TouchableOpacity>

          {instructionsOpen && (
            <View style={styles.instructionsBody}>
              <InstructionStep
                number="1"
                title="Open Predict"
                text="Tap Predict in the bottom navigation."
              />
              <InstructionStep
                number="2"
                title="Choose the sport and league"
                text="Select Soccer or another supported sport, then choose the league for the upcoming match."
              />
              <InstructionStep
                number="3"
                title="Find your player"
                text="Use the Player search field. Type the player's name and select the matching player and team from the results."
              />
              <InstructionStep
                number="4"
                title="Choose the prop"
                text="Select what you want to analyze, such as shots, passes, tackles, or saves."
              />
              <InstructionStep
                number="5"
                title="Enter the line"
                text="Enter the sportsbook or pick'em line you want to compare against, then tap Analyze."
              />
              <InstructionStep
                number="6"
                title="Read the result"
                text="Review the projection, OVER/UNDER recommendation, confidence, recent form, matchup, and risk notes before deciding."
                last
              />
              <TouchableOpacity
                style={styles.instructionsAction}
                onPress={() => router.push('/(tabs)/scan')}
                activeOpacity={0.8}
              >
                <Ionicons name="scan-outline" size={16} color={Colors.background} />
                <Text style={styles.instructionsActionText}>Go to Predict</Text>
              </TouchableOpacity>
            </View>
          )}
        </View>

        {/* ── iOS native: Apple IAP subscription section ── */}
        {isIOSNative && !isLifetime && !isOwner && (
          <>
            <Text style={styles.sectionLabel}>Subscription</Text>
            {iapLoading ? (
              <View style={styles.menuGroup}>
                {[1, 2].map(i => (
                  <View key={i} style={[styles.menuRow, { borderBottomWidth: i < 2 ? 1 : 0 }]}>
                    <View style={[styles.menuIcon, { backgroundColor: '#1c1c1c' }]} />
                    <View style={{ flex: 1, gap: 6 }}>
                      <SkeletonLine w="45%" h={13} />
                      <SkeletonLine w="60%" h={10} />
                    </View>
                  </View>
                ))}
              </View>
            ) : hasIAP ? (
              <IAPSubscriptionInfo />
            ) : (
              <IAPPaywall />
            )}
          </>
        )}

        {/* ── Web / Android: Stripe subscription management ── */}
        {showStripeManagement && (
          <>
            <Text style={styles.sectionLabel}>Subscription</Text>
            {subLoading && !subStatus ? (
              <View style={styles.menuGroup}>
                {[1, 2, 3].map(i => (
                  <View key={i} style={[styles.menuRow, { borderBottomWidth: i < 3 ? 1 : 0 }]}>
                    <View style={[styles.menuIcon, { backgroundColor: '#1c1c1c' }]} />
                    <View style={{ flex: 1, gap: 6 }}>
                      <SkeletonLine w="40%" h={13} />
                      <SkeletonLine w="55%" h={10} />
                    </View>
                  </View>
                ))}
              </View>
            ) : subStatus ? (
              <View style={styles.menuGroup}>
                <MenuRow icon="card-outline" label="Plan" value={subStatus.plan || '—'} />
                <MenuRow icon="pulse-outline" label="Status" value={statusLabel} valueColor={statusColor} />
                {subStatus.expiresAt && (
                  <MenuRow icon="calendar-outline" label="Access ends" value={formatDate(subStatus.expiresAt)} />
                )}
                <View style={{ padding: 14, gap: 6 }}>
                  <Text style={{ color: Colors.primary, fontWeight: '800', fontSize: 13 }}>
                    Website billing is active
                  </Text>
                  <Text style={{ color: Colors.textSecondary, fontSize: 12, lineHeight: 18 }}>
                    Manage your website subscription or choose a different billing cycle below.
                  </Text>
                  <TouchableOpacity
                    style={{ marginTop: 6, backgroundColor: Colors.primary, borderRadius: 7, paddingVertical: 10, alignItems: 'center' }}
                    onPress={() => setPlanPickerVisible(true)}
                  >
                    <Text style={{ color: '#000', fontSize: 11, fontWeight: '900' }}>
                      {isCanceled ? 'RESUBSCRIBE' : 'CHANGE PLAN'}
                    </Text>
                  </TouchableOpacity>
                  {!isCanceled && (
                    <TouchableOpacity
                      style={{ marginTop: 4, borderColor: Colors.borderSubtle, borderWidth: 1, borderRadius: 7, paddingVertical: 9, alignItems: 'center' }}
                      onPress={handleCancel}
                    >
                      <Text style={{ color: Colors.textSecondary, fontSize: 11, fontWeight: '800' }}>CANCEL AT PERIOD END</Text>
                    </TouchableOpacity>
                  )}
                </View>
              </View>
            ) : (
              <View style={[styles.menuGroup, styles.subLoadingWrap]}>
                <Ionicons name="alert-circle-outline" size={18} color={Colors.textTertiary} />
                <Text style={styles.subLoadingText}>Could not load subscription info</Text>
              </View>
            )}
          </>
        )}

        {/* ── Owner: API-Football Diagnostics ── */}
        {isOwner && (
          <>
            <Text style={styles.sectionLabel}>Diagnostics</Text>
            <View style={[styles.menuGroup, { padding: 14, gap: 10 }]}>
              {/* Quota breaker status row */}
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                <View style={[styles.menuIcon, {
                  backgroundColor: quotaStatus?.active ? '#2a1010' : '#0e1f0e',
                }]}>
                  <Ionicons
                    name={quotaStatus?.active ? 'warning-outline' : 'checkmark-circle-outline'}
                    size={18}
                    color={quotaStatus?.active ? '#f87171' : '#4ade80'}
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={{ color: Colors.text, fontSize: 13, fontWeight: '700' }}>
                    API-Football Quota Breaker
                  </Text>
                  <Text style={{ color: Colors.textSecondary, fontSize: 11, marginTop: 2 }}>
                    {quotaStatus === null
                      ? 'Checking…'
                      : quotaStatus.active
                        ? `Active — tripped ${quotaStatus.trippedDate ?? 'today'} (UTC). Predictions blocked.`
                        : 'Clear — predictions unblocked.'}
                  </Text>
                </View>
              </View>

              {/* Reset button */}
              <TouchableOpacity
                style={{
                  backgroundColor: quotaStatus?.active ? Colors.primary : '#1a1a1a',
                  borderRadius: 8,
                  paddingVertical: 10,
                  alignItems: 'center',
                  borderWidth: quotaStatus?.active ? 0 : 1,
                  borderColor: Colors.borderSubtle,
                  opacity: quotaResetting ? 0.6 : 1,
                }}
                onPress={handleQuotaReset}
                disabled={quotaResetting}
                activeOpacity={0.8}
              >
                {quotaResetting ? (
                  <ActivityIndicator size="small" color={quotaStatus?.active ? Colors.background : Colors.textSecondary} />
                ) : (
                  <Text style={{
                    color: quotaStatus?.active ? Colors.background : Colors.textSecondary,
                    fontSize: 11,
                    fontWeight: '900',
                  }}>
                    RESET API QUOTA BREAKER
                  </Text>
                )}
              </TouchableOpacity>

              {/* Inline feedback message */}
              {!!quotaMsg && (
                <Text style={{
                  color: quotaMsg.includes('unblocked') ? '#4ade80' : Colors.textSecondary,
                  fontSize: 11,
                  lineHeight: 16,
                  textAlign: 'center',
                }}>
                  {quotaMsg}
                </Text>
              )}

              {/* Refresh status link */}
              <TouchableOpacity
                onPress={fetchQuotaStatus}
                style={{ alignItems: 'center', paddingTop: 2 }}
                activeOpacity={0.7}
              >
                <Text style={{ color: Colors.textTertiary, fontSize: 10 }}>
                  Tap to refresh status
                </Text>
              </TouchableOpacity>
            </View>
          </>
        )}

        <Text style={styles.sectionLabel}>About</Text>
        <View style={styles.menuGroup}>
          <MenuRow icon="football-outline" label="Sport" value="Soccer (All Major Leagues)" />
          <MenuRow icon="analytics-outline" label="Engine" value="Reverse Formula + Model" />
          <MenuRow icon="information-circle-outline" label="Version" value="1.0.0" />
        </View>

        <Text style={styles.sectionLabel}>Session</Text>
        <View style={styles.menuGroup}>
          <MenuRow icon="log-out-outline" label="Sign Out" onPress={handleLogout} danger />
          <MenuRow icon="trash-outline" label="Delete Account" onPress={handleDeleteAccount} danger />
        </View>

        <View style={styles.footer}>
          <Image source={require('../../assets/logo.png')} style={styles.footerLogo} resizeMode="contain" />
          <Text style={styles.footerText}>Reverse Picks · Soccer Model Analytics</Text>
        </View>
      </ScrollView>

      <PlanPickerModal
        visible={planPickerVisible}
        currentPlanKey={isCanceled ? undefined : subStatus?.planKey}
        loading={actionLoading}
        onSelect={isCanceled ? handleResubscribePlan : handleChangePlan}
        onClose={() => setPlanPickerVisible(false)}
        isResubscribe={isCanceled}
      />

      {/* Username modal */}
      <Modal visible={usernameModal} transparent animationType="fade" onRequestClose={() => setUsernameModal(false)}>
        <KeyboardAvoidingView
          style={{ flex: 1 }}
          behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
          keyboardVerticalOffset={Platform.OS === 'ios' ? 20 : 0}
        >
          <View style={styles.modalOverlay}>
            {/* Backdrop — tapping outside the sheet dismisses */}
            <TouchableOpacity
              style={[StyleSheet.absoluteFill, { zIndex: 1 }]}
              activeOpacity={1}
              onPress={() => setUsernameModal(false)}
            />
            {/* Sheet — catches touches so they don't bubble to backdrop */}
            <View
              style={[styles.modalSheet, { zIndex: 2 }]}
              onStartShouldSetResponder={() => true}
              onResponderTerminationRequest={() => false}
            >
              <View style={styles.modalHandle} />
              <Text style={styles.modalTitle}>{profile.username ? 'Change Username' : 'Choose Username'}</Text>
              <Text style={styles.modalSubtitle}>3–20 characters. Letters, numbers, and underscores only.</Text>
              <TextInput
                style={styles.modalInput}
                value={usernameInput}
                onChangeText={(t) => { setUsernameInput(t); setUsernameError(''); }}
                placeholder="e.g. soccer_fan_99"
                placeholderTextColor={Colors.textTertiary}
                autoCapitalize="none"
                autoCorrect={false}
                maxLength={20}
                returnKeyType="done"
                onSubmitEditing={handleSetUsername}
              />
              {usernameError ? <Text style={styles.modalError}>{usernameError}</Text> : null}
              <TouchableOpacity
                style={[styles.buyBtn, { opacity: usernameLoading || !usernameInput.trim() ? 0.6 : 1 }]}
                onPress={handleSetUsername}
                disabled={usernameLoading || !usernameInput.trim()}
                activeOpacity={0.8}
              >
                {usernameLoading ? (
                  <ActivityIndicator size="small" color={Colors.background} />
                ) : (
                  <Text style={styles.buyBtnText}>{profile.username ? 'Update' : 'Set Username'}</Text>
                )}
              </TouchableOpacity>
              <TouchableOpacity style={styles.modalCancel} onPress={() => setUsernameModal(false)}>
                <Text style={styles.modalCancelText}>Cancel</Text>
              </TouchableOpacity>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: { paddingHorizontal: 20, paddingBottom: 16 },
  headerTitle: { fontSize: 28, fontWeight: '800', color: Colors.text },
  headerIcon: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  dmBadge: {
    position: 'absolute', top: 2, right: 2,
    backgroundColor: Colors.primary, borderRadius: 7,
    minWidth: 14, height: 14, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 2,
  },
  dmBadgeText: { color: '#000', fontSize: 9, fontWeight: '800', lineHeight: 14 },
  body: { paddingHorizontal: 20 },
  profileCard: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    padding: 20, flexDirection: 'row', alignItems: 'center',
    gap: 16, borderWidth: 1, borderColor: Colors.border, marginBottom: 28,
  },
  avatar: {
    width: 56, height: 56, borderRadius: 28,
    backgroundColor: Colors.primaryDim,
    borderWidth: 2, borderColor: Colors.primary,
    alignItems: 'center', justifyContent: 'center',
  },
  avatarText: { fontSize: 20, fontWeight: '800', color: Colors.primary },
  avatarEditBadge: {
    position: 'absolute',
    bottom: -2, right: -2,
    width: 22, height: 22,
    borderRadius: 11,
    backgroundColor: Colors.primary,
    alignItems: 'center', justifyContent: 'center',
    borderWidth: 2, borderColor: Colors.background,
  },
  profileInfo: { flex: 1 },
  profileEmail: { fontSize: 15, fontWeight: '600', color: Colors.text, marginBottom: 6 },
  accessBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: Colors.primaryDim, paddingHorizontal: 10,
    paddingVertical: 4, borderRadius: 20, alignSelf: 'flex-start',
  },
  accessText: { fontSize: 11, color: Colors.primary, fontWeight: '700' },
  sectionLabel: {
    fontSize: 11, color: Colors.textSecondary, fontWeight: '700',
    letterSpacing: 1, marginBottom: 8, marginTop: 4, paddingHorizontal: 4,
  },
  menuGroup: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    borderWidth: 1, borderColor: Colors.border, marginBottom: 24, overflow: 'hidden',
  },
  menuRow: {
    flexDirection: 'row', alignItems: 'center',
    padding: 16, gap: 14,
    borderBottomWidth: 1, borderBottomColor: Colors.border,
  },
  menuIcon: {
    width: 34, height: 34, borderRadius: 8,
    backgroundColor: Colors.primaryDim,
    alignItems: 'center', justifyContent: 'center',
  },
  menuIconDanger: { backgroundColor: Colors.errorDim },
  menuContent: { flex: 1 },
  menuLabel: { fontSize: 15, color: Colors.text, fontWeight: '500' },
  menuLabelDanger: { color: Colors.error },
  menuValue: { fontSize: 12, color: Colors.textSecondary, marginTop: 2 },

  // Getting started guide
  instructionsCard: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    borderWidth: 1,
    borderColor: Colors.border,
    marginBottom: 24,
    overflow: 'hidden',
  },
  instructionsHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    gap: 12,
  },
  instructionsIcon: {
    width: 38,
    height: 38,
    borderRadius: 10,
    backgroundColor: Colors.primaryDim,
    alignItems: 'center',
    justifyContent: 'center',
  },
  instructionsTitleWrap: { flex: 1 },
  instructionsTitle: { fontSize: 15, color: Colors.text, fontWeight: '700' },
  instructionsSubtitle: {
    fontSize: 11,
    color: Colors.textSecondary,
    marginTop: 3,
    lineHeight: 16,
  },
  instructionsBody: {
    paddingHorizontal: 16,
    paddingBottom: 16,
    borderTopWidth: 1,
    borderTopColor: Colors.border,
  },
  instructionStep: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
    paddingVertical: 13,
  },
  instructionStepBorder: {
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
  },
  instructionNumber: {
    width: 25,
    height: 25,
    borderRadius: 13,
    backgroundColor: Colors.primaryDim,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 1,
  },
  instructionNumberText: { color: Colors.primary, fontSize: 12, fontWeight: '800' },
  instructionCopy: { flex: 1 },
  instructionStepTitle: { color: Colors.text, fontSize: 13, fontWeight: '700' },
  instructionStepText: {
    color: Colors.textSecondary,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 3,
  },
  instructionsAction: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    backgroundColor: Colors.primary,
    borderRadius: 12,
    paddingVertical: 12,
    marginTop: 8,
  },
  instructionsActionText: {
    color: Colors.background,
    fontSize: 13,
    fontWeight: '800',
  },

  // Paywall
  paywallHeader: { alignItems: 'center', gap: 8, marginBottom: 20 },
  paywallTitle: { fontSize: 22, fontWeight: '800', color: Colors.text },
  paywallSub: { fontSize: 13, color: Colors.textSecondary, textAlign: 'center' },
  paywallDisclosure: { fontSize: 11, color: Colors.textTertiary, textAlign: 'center', lineHeight: 16, marginHorizontal: 24, marginTop: 16, marginBottom: 8 },
  paywallLoading: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    borderWidth: 1, borderColor: Colors.border,
    padding: 24, marginBottom: 24, justifyContent: 'center',
  },
  paywallLoadingText: { fontSize: 13, color: Colors.textTertiary },
  paywallEmpty: { padding: 24, alignItems: 'center' },
  paywallEmptyText: { fontSize: 13, color: Colors.textTertiary },

  planOption: {
    flexDirection: 'row', alignItems: 'center',
    padding: 16,
    borderBottomWidth: 1, borderBottomColor: Colors.border,
  },
  planOptionCurrent: { backgroundColor: Colors.primaryDim },
  planInfo: { flex: 1 },
  planDesc: { fontSize: 12, color: Colors.textSecondary, marginTop: 2 },
  planName: { fontSize: 16, fontWeight: '700', color: Colors.text },
  planNameCurrent: { color: Colors.primary },
  planRight: { alignItems: 'flex-end', gap: 2 },
  planPrice: { fontSize: 14, fontWeight: '700', color: Colors.primary },

  buyBtn: {
    backgroundColor: Colors.primary, borderRadius: 14,
    padding: 14, alignItems: 'center', marginTop: 8,
  },
  buyBtnText: { fontSize: 15, fontWeight: '700', color: Colors.background },

  restoreBtn: {
    alignItems: 'center', paddingVertical: 12, marginBottom: 8,
  },
  restoreBtnText: { fontSize: 13, color: Colors.textSecondary },

  footer: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 8, paddingTop: 8,
  },
  footerLogo: { width: 20, height: 20, opacity: 0.5 },
  footerText: { fontSize: 12, color: Colors.textTertiary },

  subLoadingWrap: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 10, paddingVertical: 24,
  },
  subLoadingText: { fontSize: 13, color: Colors.textTertiary },

  modalOverlay: {
    flex: 1, backgroundColor: Colors.overlay, justifyContent: 'flex-end',
  },
  modalSheet: {
    backgroundColor: Colors.card, borderTopLeftRadius: 24,
    borderTopRightRadius: 24, padding: 24, paddingBottom: 40,
  },
  modalHandle: {
    width: 40, height: 4, borderRadius: 2,
    backgroundColor: Colors.textTertiary,
    alignSelf: 'center', marginBottom: 20,
  },
  modalTitle: {
    fontSize: 20, fontWeight: '800', color: Colors.text,
    textAlign: 'center', marginBottom: 4,
  },
  modalSubtitle: {
    fontSize: 13, color: Colors.textSecondary,
    textAlign: 'center', marginBottom: 20,
  },
  currentBadge: {
    backgroundColor: Colors.primaryDim, paddingHorizontal: 10,
    paddingVertical: 4, borderRadius: 20,
  },
  currentBadgeText: {
    fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 0.8,
  },
  modalCancel: {
    marginTop: 12, padding: 14, borderRadius: 14,
    alignItems: 'center', backgroundColor: '#1a1a1a',
  },
  modalCancelText: { fontSize: 15, fontWeight: '600', color: Colors.textSecondary },
  modalInput: {
    backgroundColor: '#1A1A1A', borderRadius: 12, paddingHorizontal: 16,
    paddingVertical: 12, fontSize: 16, color: Colors.text,
    borderWidth: 0.5, borderColor: Colors.border, marginBottom: 12,
  },
  modalError: {
    fontSize: 13, color: Colors.error, textAlign: 'center', marginBottom: 12,
  },
});
