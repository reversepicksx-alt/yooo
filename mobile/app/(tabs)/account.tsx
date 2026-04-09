import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  Alert, Platform, Image, Modal, ActivityIndicator, Linking,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import {
  getSubscriptionStatus, cancelSubscription, changePlan,
  resubscribeCheckout, PLAN_OPTIONS, type SubscriptionStatus,
} from '@/lib/api';

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

function formatDate(iso?: string): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return '—';
  }
}

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

export default function AccountScreen() {
  const insets = useSafeAreaInsets();
  const { session, logout } = useAuth();
  const topPad = Platform.OS === 'web' ? 67 : insets.top;
  const bottomPad = Platform.OS === 'web' ? 34 : insets.bottom;

  const [subStatus, setSubStatus] = useState<SubscriptionStatus | null>(null);
  const [subLoading, setSubLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [planPickerVisible, setPlanPickerVisible] = useState(false);

  const isSquareSub = session?.accessType?.toLowerCase().includes('square');
  const isLifetime = session?.accessType?.toLowerCase().includes('lifetime');
  const isWhop = session?.accessType?.toLowerCase().includes('whop');
  const isOwner = session?.accessType?.toLowerCase() === 'owner';
  const showSubManagement = isSquareSub && !isLifetime && !isOwner;

  const fetchSubStatus = useCallback(async () => {
    if (!session?.email || !showSubManagement) return;
    setSubLoading(true);
    try {
      const status = await getSubscriptionStatus(session.email);
      setSubStatus(status);
    } catch {
      setSubStatus(null);
    } finally {
      setSubLoading(false);
    }
  }, [session?.email, showSubManagement]);

  useEffect(() => {
    fetchSubStatus();
  }, [fetchSubStatus]);

  const handleCancel = async () => {
    const doCancel = async () => {
      if (!session?.email) return;
      setActionLoading(true);
      try {
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
        await cancelSubscription(session.email);
        await fetchSubStatus();
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      } catch (e: any) {
        const msg = e?.message || 'Failed to cancel. Please try again.';
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
      await changePlan(session.email, newKey);
      await fetchSubStatus();
      setPlanPickerVisible(false);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: any) {
      const msg = e?.message || 'Failed to change plan. Please try again.';
      if (Platform.OS === 'web') {
        window.alert(msg);
      } else {
        Alert.alert('Error', msg);
      }
    } finally {
      setActionLoading(false);
    }
  };

  const handleResubscribe = () => {
    setPlanPickerVisible(true);
  };

  const handleResubscribePlan = async (planKey: string) => {
    if (!session?.email) return;
    setActionLoading(true);
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      const result = await resubscribeCheckout(session.email, planKey);
      const url = result.checkoutUrl || result.checkout_url || result.redirect_url;
      if (url) {
        setPlanPickerVisible(false);
        if (Platform.OS === 'web' && typeof window !== 'undefined') {
          window.open(url, '_blank');
        } else {
          await Linking.openURL(url);
        }
      } else {
        throw new Error('Could not create checkout. Please try again.');
      }
    } catch (e: any) {
      const msg = e?.message || 'Failed to create checkout. Please try again.';
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
    if (Platform.OS === 'web') {
      if (typeof window !== 'undefined' && !window.confirm('Sign out of ReversePicks?')) return;
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
      await logout();
    } else {
      Alert.alert('Sign Out', 'Are you sure you want to sign out?', [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Sign Out',
          style: 'destructive',
          onPress: async () => {
            await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
            await logout();
          },
        },
      ]);
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
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Account</Text>
      </View>
      <ScrollView contentContainerStyle={[styles.body, { paddingBottom: bottomPad + 20 }]}>
        <View style={styles.profileCard}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{initials}</Text>
          </View>
          <View style={styles.profileInfo}>
            <Text style={styles.profileEmail} numberOfLines={1}>{session?.email}</Text>
            <View style={styles.accessBadge}>
              <Ionicons name="shield-checkmark" size={11} color={Colors.primary} />
              <Text style={styles.accessText}>{accessLabel}</Text>
            </View>
          </View>
        </View>

        <Text style={styles.sectionLabel}>Account</Text>
        <View style={styles.menuGroup}>
          <MenuRow icon="mail-outline" label="Email" value={session?.email} />
          <MenuRow icon="shield-outline" label="Access Level" value={accessLabel} />
        </View>

        {showSubManagement && (
          <>
            <Text style={styles.sectionLabel}>Subscription</Text>
            {subLoading && !subStatus ? (
              <View style={[styles.menuGroup, styles.subLoadingWrap]}>
                <ActivityIndicator size="small" color={Colors.primary} />
                <Text style={styles.subLoadingText}>Loading subscription…</Text>
              </View>
            ) : subStatus ? (
              <View style={styles.menuGroup}>
                <MenuRow
                  icon="card-outline"
                  label="Plan"
                  value={subStatus.planLabel || subStatus.plan || '—'}
                />
                <MenuRow
                  icon="pulse-outline"
                  label="Status"
                  value={statusLabel}
                  valueColor={statusColor}
                />
                {subStatus.expiresAt && !isCanceled && (
                  <MenuRow
                    icon="calendar-outline"
                    label="Next Billing"
                    value={formatDate(subStatus.expiresAt)}
                  />
                )}
                {subStatus.cardLast4 && (
                  <MenuRow
                    icon="wallet-outline"
                    label="Payment"
                    value={`${subStatus.cardBrand || 'Card'} •••• ${subStatus.cardLast4}`}
                  />
                )}
                {!isCanceled && (
                  <MenuRow
                    icon="swap-horizontal-outline"
                    label="Change Plan"
                    onPress={() => setPlanPickerVisible(true)}
                    loading={actionLoading}
                  />
                )}
                {isCanceled ? (
                  <MenuRow
                    icon="refresh-outline"
                    label="Resubscribe"
                    value="Choose a new plan"
                    onPress={handleResubscribe}
                    loading={actionLoading}
                  />
                ) : (
                  <MenuRow
                    icon="close-circle-outline"
                    label="Cancel Subscription"
                    onPress={handleCancel}
                    danger
                    loading={actionLoading}
                  />
                )}
              </View>
            ) : (
              <View style={[styles.menuGroup, styles.subLoadingWrap]}>
                <Ionicons name="alert-circle-outline" size={18} color={Colors.textTertiary} />
                <Text style={styles.subLoadingText}>Could not load subscription info</Text>
              </View>
            )}
          </>
        )}

        {(isLifetime || isOwner) && (
          <>
            <Text style={styles.sectionLabel}>Subscription</Text>
            <View style={styles.menuGroup}>
              <MenuRow
                icon="infinite-outline"
                label="Plan"
                value="Lifetime Access"
              />
              <MenuRow
                icon="pulse-outline"
                label="Status"
                value="Active Forever"
                valueColor={Colors.primary}
              />
            </View>
          </>
        )}

        {isWhop && (
          <>
            <Text style={styles.sectionLabel}>Subscription</Text>
            <View style={styles.menuGroup}>
              <MenuRow
                icon="globe-outline"
                label="Managed By"
                value="Whop"
              />
              <MenuRow
                icon="pulse-outline"
                label="Status"
                value="Active"
                valueColor={Colors.primary}
              />
            </View>
          </>
        )}

        <Text style={styles.sectionLabel}>About</Text>
        <View style={styles.menuGroup}>
          <MenuRow icon="football-outline" label="Sport" value="Soccer (All Major Leagues)" />
          <MenuRow icon="analytics-outline" label="Engine" value="Reverse Formula + AI" />
          <MenuRow icon="information-circle-outline" label="Version" value="1.0.0" />
        </View>

        <Text style={styles.sectionLabel}>Session</Text>
        <View style={styles.menuGroup}>
          <MenuRow icon="log-out-outline" label="Sign Out" onPress={handleLogout} danger />
        </View>

        <View style={styles.footer}>
          <Image source={require('../../assets/logo.png')} style={styles.footerLogo} resizeMode="contain" />
          <Text style={styles.footerText}>ReversePicks · Soccer AI Analytics</Text>
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
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: { paddingHorizontal: 20, paddingBottom: 16 },
  headerTitle: { fontSize: 28, fontWeight: '800', color: Colors.text },
  body: { paddingHorizontal: 20 },
  profileCard: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    padding: 20,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 16,
    borderWidth: 1,
    borderColor: Colors.border,
    marginBottom: 28,
  },
  avatar: {
    width: 56, height: 56, borderRadius: 28,
    backgroundColor: Colors.primaryDim,
    borderWidth: 2, borderColor: Colors.primary,
    alignItems: 'center', justifyContent: 'center',
  },
  avatarText: { fontSize: 20, fontWeight: '800', color: Colors.primary },
  profileInfo: { flex: 1 },
  profileEmail: { fontSize: 15, fontWeight: '600', color: Colors.text, marginBottom: 6 },
  accessBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: Colors.primaryDim,
    paddingHorizontal: 10, paddingVertical: 4,
    borderRadius: 20, alignSelf: 'flex-start',
  },
  accessText: { fontSize: 11, color: Colors.primary, fontWeight: '700' },
  sectionLabel: {
    fontSize: 11, color: Colors.textSecondary, fontWeight: '700',
    letterSpacing: 1, marginBottom: 8, marginTop: 4, paddingHorizontal: 4,
  },
  menuGroup: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    borderWidth: 1, borderColor: Colors.border,
    marginBottom: 24, overflow: 'hidden',
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
    flex: 1, backgroundColor: Colors.overlay,
    justifyContent: 'flex-end',
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
  planOption: {
    flexDirection: 'row', alignItems: 'center',
    padding: 16, borderRadius: 14,
    backgroundColor: '#1a1a1a', marginBottom: 8,
    borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  planOptionCurrent: {
    borderColor: Colors.primary, backgroundColor: Colors.primaryDim,
  },
  planInfo: { flex: 1 },
  planName: { fontSize: 16, fontWeight: '700', color: Colors.text },
  planNameCurrent: { color: Colors.primary },
  planPrice: { fontSize: 13, color: Colors.textSecondary, marginTop: 2 },
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
});
