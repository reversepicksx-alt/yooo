import React, { useState, useEffect } from 'react';
import {
  User, Shield, BarChart3, Mail, Lock, Loader2, Zap, Activity,
  LogOut, Settings, Edit3, Eye, EyeOff, Check, X,
  CreditCard, ArrowRightLeft, Calendar, AlertCircle
} from 'lucide-react';
import { getSquareSubscriptionStatus, changeSquarePlan, cancelSquareSubscription, squareResubscribeCheckout } from '../../api';
import { toast } from 'sonner';

const PLAN_OPTIONS = [
  { key: 'weekly', name: 'Weekly', label: '$11/week', amount: 1100 },
  { key: 'monthly', name: 'Monthly', label: '$39.99/month', amount: 3999 },
  { key: 'quarterly', name: 'Quarterly', label: '$99.99/3 months', amount: 9999 },
];

function SubscriptionManager({ email }) {
  const [subStatus, setSubStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [changingPlan, setChangingPlan] = useState(false);
  const [showPlans, setShowPlans] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [canceling, setCanceling] = useState(false);

  useEffect(() => {
    if (!email) return;
    fetchStatus();
  }, [email]);

  async function fetchStatus() {
    setLoading(true);
    try {
      const data = await getSquareSubscriptionStatus(email);
      setSubStatus(data);
    } catch {
      setSubStatus(null);
    } finally {
      setLoading(false);
    }
  }

  async function handleChangePlan(newKey) {
    if (changingPlan) return;
    setChangingPlan(true);
    try {
      if (isCanceled) {
        // Resubscribe: redirect to Square checkout for new payment
        const redirectUrl = window.location.origin;
        const result = await squareResubscribeCheckout(email, newKey, redirectUrl);
        if (result.checkoutUrl) {
          window.location.href = result.checkoutUrl;
          return; // Redirecting — don't reset state
        }
        toast.error('Failed to create checkout link.');
      } else {
        const result = await changeSquarePlan(email, newKey);
        toast.success(result.message || `Switched to ${result.new_plan}`);
        setShowPlans(false);
        await fetchStatus();
      }
    } catch (err) {
      toast.error(err.message || 'Failed to change plan');
    } finally {
      setChangingPlan(false);
    }
  }

  async function handleCancel() {
    if (canceling) return;
    setCanceling(true);
    try {
      await cancelSquareSubscription(email);
      toast.success('Subscription canceled successfully');
      setShowCancelConfirm(false);
      await fetchStatus();
    } catch (err) {
      toast.error(err.message || 'Failed to cancel subscription');
    } finally {
      setCanceling(false);
    }
  }

  if (loading) {
    return (
      <div className="profile-section" data-testid="subscription-section">
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 12 }}>Subscription</div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, gap: 8, color: 'var(--text-muted)', fontSize: 13 }}>
          <Loader2 className="animate-spin" style={{ width: 16, height: 16 }} /> Loading...
        </div>
      </div>
    );
  }

  if (!subStatus || !subStatus.active) return null;

  const currentPlanKey = subStatus.planKey || '';
  const otherPlans = PLAN_OPTIONS.filter(p => p.key !== currentPlanKey);
  const isCanceled = subStatus.status === 'CANCELED';

  // Calculate days remaining
  let daysLeft = null;
  if (subStatus.expiresAt) {
    const diff = new Date(subStatus.expiresAt) - new Date();
    daysLeft = Math.max(0, Math.ceil(diff / (1000 * 60 * 60 * 24)));
  }

  return (
    <div className="profile-section" data-testid="subscription-section">
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 12 }}>Subscription</div>
      <div className="space-y-3">
        <div className="profile-field">
          <div className="profile-field-icon"><CreditCard style={{ width: 16, height: 16, color: '#818cf8' }} /></div>
          <div className="profile-field-content">
            <div className="profile-field-label">Current Plan</div>
            <div className="profile-field-value" data-testid="current-plan-name">
              {subStatus.plan || 'Unknown'}{subStatus.planLabel ? ` — ${subStatus.planLabel}` : ''}
            </div>
          </div>
        </div>

        {subStatus.status && (
          <div className="profile-field">
            <div className="profile-field-icon"><Activity style={{ width: 16, height: 16, color: isCanceled ? '#f43f5e' : subStatus.status === 'ACTIVE' ? 'var(--accent)' : '#f59e0b' }} /></div>
            <div className="profile-field-content">
              <div className="profile-field-label">Status</div>
              <div className="profile-field-value" style={{ color: isCanceled ? '#f43f5e' : subStatus.status === 'ACTIVE' ? 'var(--accent)' : '#f59e0b' }} data-testid="sub-status">
                {isCanceled ? 'CANCELING' : subStatus.status}
              </div>
            </div>
          </div>
        )}

        {/* Days remaining / billing info */}
        {subStatus.expiresAt && (
          <div className="profile-field">
            <div className="profile-field-icon"><Calendar style={{ width: 16, height: 16 }} /></div>
            <div className="profile-field-content">
              <div className="profile-field-label">{isCanceled ? 'Access Ends' : 'Next Billing'}</div>
              <div className="profile-field-value" data-testid="sub-next-billing">
                {new Date(subStatus.expiresAt).toLocaleDateString()}
                {daysLeft !== null && (
                  <span style={{ marginLeft: 8, fontSize: 11, color: isCanceled ? '#f43f5e' : 'var(--text-muted)', fontWeight: 700 }}>
                    ({daysLeft} {daysLeft === 1 ? 'day' : 'days'} left)
                  </span>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Canceled notice */}
        {isCanceled && (
          <div data-testid="cancel-notice" style={{
            padding: 12, borderRadius: 10, fontSize: 11, lineHeight: 1.5,
            background: 'rgba(244, 63, 94, 0.06)', border: '1.5px solid rgba(244, 63, 94, 0.15)',
            color: 'var(--text-secondary)',
          }}>
            Your subscription has been canceled. You still have full access until <strong style={{ color: '#f43f5e' }}>{new Date(subStatus.expiresAt).toLocaleDateString()}</strong>.
            You can resubscribe anytime below.
          </div>
        )}

        {subStatus.cardLast4 && !isCanceled && (
          <div className="profile-field">
            <div className="profile-field-icon"><CreditCard style={{ width: 16, height: 16 }} /></div>
            <div className="profile-field-content">
              <div className="profile-field-label">Payment Method</div>
              <div className="profile-field-value" data-testid="sub-card">
                {subStatus.cardBrand || 'Card'} ····{subStatus.cardLast4}
              </div>
            </div>
          </div>
        )}

        {/* Change Plan Toggle — available for active and canceled (to resubscribe) */}
        <button
          className="btn-secondary"
          onClick={() => setShowPlans(!showPlans)}
          data-testid="change-plan-toggle"
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, width: '100%', fontSize: 13 }}
        >
          <ArrowRightLeft style={{ width: 15, height: 15 }} />
          {isCanceled ? (showPlans ? 'Close' : 'Resubscribe') : (showPlans ? 'Close' : 'Change Plan')}
        </button>

        {/* Plan Options */}
        {showPlans && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }} data-testid="plan-options">
            <div style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <AlertCircle style={{ width: 12, height: 12 }} />
              Change takes effect at your next billing cycle
            </div>
            {otherPlans.map(plan => (
              <button
                key={plan.key}
                className="btn-primary"
                onClick={() => handleChangePlan(plan.key)}
                disabled={changingPlan}
                data-testid={`switch-to-${plan.key}`}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                  width: '100%', fontSize: 13, padding: '10px 16px',
                }}
              >
                {changingPlan
                  ? <Loader2 className="animate-spin" style={{ width: 15, height: 15 }} />
                  : <ArrowRightLeft style={{ width: 15, height: 15 }} />}
                Switch to {plan.name} ({plan.label})
              </button>
            ))}
          </div>
        )}

        {/* Cancel Subscription — only show when active (not already canceled) */}
        {!isCanceled && !showCancelConfirm && (
          <button
            onClick={() => setShowCancelConfirm(true)}
            data-testid="cancel-sub-btn"
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
              width: '100%', fontSize: 12, padding: '10px 16px', marginTop: 8,
              background: 'transparent', border: '1.5px solid rgba(244, 63, 94, 0.25)',
              borderRadius: 10, color: '#f43f5e', cursor: 'pointer', fontWeight: 700,
              letterSpacing: '0.04em', transition: 'all 0.2s',
            }}
          >
            <X style={{ width: 14, height: 14 }} />
            Cancel Subscription
          </button>
        )}
        {!isCanceled && showCancelConfirm && (
          <div data-testid="cancel-confirm-box" style={{
            marginTop: 8, padding: 14, borderRadius: 10,
            background: 'rgba(244, 63, 94, 0.06)',
            border: '1.5px solid rgba(244, 63, 94, 0.2)',
          }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#f43f5e', marginBottom: 6 }}>
              Are you sure?
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12, lineHeight: 1.5 }}>
              Your access will remain active until the end of your current billing period.
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                onClick={handleCancel}
                disabled={canceling}
                data-testid="cancel-confirm-yes"
                style={{
                  flex: 1, padding: '8px 12px', borderRadius: 8, border: 'none',
                  background: '#f43f5e', color: '#fff', fontSize: 12, fontWeight: 800,
                  cursor: canceling ? 'wait' : 'pointer', display: 'flex',
                  alignItems: 'center', justifyContent: 'center', gap: 6,
                }}
              >
                {canceling ? <Loader2 className="animate-spin" style={{ width: 13, height: 13 }} /> : null}
                {canceling ? 'Canceling...' : 'Yes, Cancel'}
              </button>
              <button
                onClick={() => setShowCancelConfirm(false)}
                data-testid="cancel-confirm-no"
                style={{
                  flex: 1, padding: '8px 12px', borderRadius: 8,
                  border: '1.5px solid rgba(255,255,255,0.1)',
                  background: 'transparent', color: 'var(--text-secondary)',
                  fontSize: 12, fontWeight: 700, cursor: 'pointer',
                }}
              >
                Keep Plan
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function ProfileTab({
  auth, savedPicks, apiStatus, isOwner,
  profileNewPw, setProfileNewPw, profileConfirmPw, setProfileConfirmPw,
  profilePwLoading, handleProfilePasswordReset,
  adminSettings, adminEditKey, setAdminEditKey,
  adminEditValue, setAdminEditValue, adminKeyLoading,
  adminTestResult, setAdminTestResult,
  adminShowKey, setAdminShowKey,
  handleTestApiKey, handleSaveAdminSetting,
  handleLogout,
}) {

  return (
    <div className="animate-fade-in space-y-6" data-testid="profile-tab">
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, paddingTop: 8, paddingBottom: 8 }}>
        <div className="profile-avatar">
          <User />
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.3px' }}>{auth.email?.split('@')[0]}</div>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent)', letterSpacing: '0.08em', textTransform: 'uppercase', marginTop: 2 }}>{auth.accessType || 'Member'}</div>
        </div>
      </div>

      <div className="profile-section" data-testid="profile-account-section">
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 12 }}>Account</div>
        <div className="space-y-3">
          <div className="profile-field">
            <div className="profile-field-icon"><Mail style={{ width: 16, height: 16 }} /></div>
            <div className="profile-field-content">
              <div className="profile-field-label">Email</div>
              <div className="profile-field-value" data-testid="profile-email">{auth.email}</div>
            </div>
          </div>
          <div className="profile-field">
            <div className="profile-field-icon"><Shield style={{ width: 16, height: 16 }} /></div>
            <div className="profile-field-content">
              <div className="profile-field-label">Access Level</div>
              <div className="profile-field-value" data-testid="profile-access">{auth.accessType || 'Member'}</div>
            </div>
          </div>
          <div className="profile-field">
            <div className="profile-field-icon"><BarChart3 style={{ width: 16, height: 16 }} /></div>
            <div className="profile-field-content">
              <div className="profile-field-label">Total Picks</div>
              <div className="profile-field-value" data-testid="profile-total-picks">{savedPicks.length}</div>
            </div>
          </div>
        </div>
      </div>

      {/* Subscription Management — shown for all users (shows loading then auto-hides if no sub found) */}
      <SubscriptionManager email={auth.email} />

      <div className="profile-section" data-testid="profile-password-section">
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 12 }}>Reset Password</div>
        <div className="space-y-3">
          <div className="profile-field" style={{ padding: '8px 12px' }}>
            <div className="profile-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
            <input type="password" value={profileNewPw} onChange={e => setProfileNewPw(e.target.value)}
              placeholder="New password (min 6 chars)" data-testid="profile-new-pw"
              style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', fontSize: 13, fontFamily: 'inherit' }} />
          </div>
          <div className="profile-field" style={{ padding: '8px 12px' }}>
            <div className="profile-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
            <input type="password" value={profileConfirmPw} onChange={e => setProfileConfirmPw(e.target.value)}
              placeholder="Confirm new password" data-testid="profile-confirm-pw"
              style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', fontSize: 13, fontFamily: 'inherit' }} />
          </div>
          <button className="btn-primary" onClick={handleProfilePasswordReset}
            disabled={profilePwLoading || profileNewPw.length < 6} data-testid="profile-reset-pw-btn">
            {profilePwLoading ? <Loader2 className="animate-spin" style={{ width: 16, height: 16 }} /> : <Lock style={{ width: 16, height: 16 }} />}
            {profilePwLoading ? 'Updating...' : 'Update Password'}
          </button>
        </div>
      </div>

      <div className="profile-section">
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 12 }}>App</div>
        <div className="space-y-3">
          <div className="profile-field">
            <div className="profile-field-icon"><Zap style={{ width: 16, height: 16 }} /></div>
            <div className="profile-field-content">
              <div className="profile-field-label">Version</div>
              <div className="profile-field-value">v2.3</div>
            </div>
          </div>
          <div className="profile-field">
            <div className="profile-field-icon"><Activity style={{ width: 16, height: 16 }} /></div>
            <div className="profile-field-content">
              <div className="profile-field-label">API Status</div>
              <div className="profile-field-value" style={{ color: apiStatus === 'online' ? 'var(--accent)' : 'var(--danger)' }}>
                {apiStatus === 'online' ? 'Connected' : 'Offline'}
              </div>
            </div>
          </div>
        </div>
      </div>

      {isOwner && (
        <div className="profile-section" data-testid="admin-settings-section">
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#f59e0b', marginBottom: 12 }}>
            <Settings style={{ width: 12, height: 12, display: 'inline', marginRight: 4, verticalAlign: 'middle' }} />
            Admin Settings
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.4, marginBottom: 12 }}>
            Manage your API keys and payment settings. Changes take effect instantly — no redeployment needed.
          </div>
          <div className="space-y-3">
            {[
              { key: 'API_FOOTBALL_KEY', label: 'API-Sports Key', testable: true },
              { key: 'SQUARE_ACCESS_TOKEN', label: 'Square Access Token' },
              { key: 'SQUARE_APPLICATION_ID', label: 'Square App ID' },
              { key: 'SQUARE_LOCATION_ID', label: 'Square Location ID' },
              { key: 'SQUARE_ENVIRONMENT', label: 'Square Environment' },
            ].map(({ key, label, testable }) => (
              <div key={key}>
                <div className="profile-field" style={{ cursor: 'pointer' }}
                  onClick={() => { if (adminEditKey !== key) { setAdminEditKey(key); setAdminEditValue(''); setAdminTestResult(null); } }}>
                  <div className="profile-field-icon"><Shield style={{ width: 16, height: 16, color: key.startsWith('SQUARE') ? '#818cf8' : '#f59e0b' }} /></div>
                  <div className="profile-field-content">
                    <div className="profile-field-label">{label}</div>
                    <div className="profile-field-value" style={{ fontFamily: 'monospace', fontSize: 11 }} data-testid={`admin-val-${key}`}>
                      {adminSettings[key]?.masked_value || 'Not set'}
                    </div>
                  </div>
                  <Edit3 style={{ width: 13, height: 13, color: 'var(--text-muted)', flexShrink: 0 }} />
                </div>
                {adminEditKey === key && (
                  <div style={{ marginTop: 8, marginLeft: 4, marginBottom: 4 }}>
                    <div className="profile-field" style={{ padding: '8px 12px' }}>
                      <input
                        type={adminShowKey ? 'text' : 'password'}
                        value={adminEditValue}
                        onChange={e => { setAdminEditValue(e.target.value); setAdminTestResult(null); }}
                        placeholder={key === 'SQUARE_ENVIRONMENT' ? 'sandbox or production' : `Paste new ${label}`}
                        data-testid={`admin-input-${key}`}
                        autoFocus
                        style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', fontSize: 13, fontFamily: 'monospace' }}
                      />
                      <button onClick={() => setAdminShowKey(!adminShowKey)} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4 }}>
                        {adminShowKey ? <EyeOff style={{ width: 14, height: 14, color: 'var(--text-muted)' }} /> : <Eye style={{ width: 14, height: 14, color: 'var(--text-muted)' }} />}
                      </button>
                    </div>
                    {testable && adminTestResult && (
                      <div style={{
                        fontSize: 12, padding: '8px 12px', borderRadius: 8, marginTop: 6,
                        background: adminTestResult.valid ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                        color: adminTestResult.valid ? '#22c55e' : '#ef4444',
                        border: `1px solid ${adminTestResult.valid ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)'}`,
                      }} data-testid="admin-test-result">
                        {adminTestResult.valid
                          ? `Valid \u2014 ${adminTestResult.plan} plan (${adminTestResult.account})`
                          : `Invalid \u2014 ${adminTestResult.error}`}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                      {testable && (
                        <button className="btn-secondary" onClick={handleTestApiKey}
                          disabled={adminKeyLoading || !adminEditValue.trim()} data-testid="admin-test-key-btn"
                          style={{ flex: 1, fontSize: 12 }}>
                          {adminKeyLoading ? <Loader2 className="animate-spin" style={{ width: 14, height: 14 }} /> : <Activity style={{ width: 14, height: 14 }} />}
                          Test
                        </button>
                      )}
                      <button className="btn-primary" onClick={() => handleSaveAdminSetting(key)}
                        disabled={adminKeyLoading || !adminEditValue.trim()} data-testid={`admin-save-${key}`}
                        style={{ flex: 1, fontSize: 12 }}>
                        {adminKeyLoading ? <Loader2 className="animate-spin" style={{ width: 14, height: 14 }} /> : <Check style={{ width: 14, height: 14 }} />}
                        Save
                      </button>
                      <button className="btn-secondary" onClick={() => { setAdminEditKey(null); setAdminEditValue(''); setAdminTestResult(null); }}
                        style={{ fontSize: 12, padding: '8px 12px' }}>
                        <X style={{ width: 14, height: 14 }} />
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <button className="btn-secondary" onClick={handleLogout} data-testid="profile-logout-btn"
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--danger)' }}>
        <LogOut style={{ width: 16, height: 16 }} /> Log Out
      </button>
    </div>
  );
}
