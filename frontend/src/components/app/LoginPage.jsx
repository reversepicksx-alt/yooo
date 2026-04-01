import React, { useState, useEffect } from 'react';
import { Zap, Loader2, Lock, Mail, ShieldAlert, CreditCard, Check, ArrowLeft, User } from 'lucide-react';
import { verifyWhop, authLogin, setPassword as apiSetPassword, resetPassword, squareSubscribe, getSquarePlans, getSquareConfig } from '../../api';
import { PaymentForm, CreditCard as SquareCreditCard } from 'react-square-web-payments-sdk';

export function LoginPage({ onAuth }) {
  const [step, setStep] = useState('email');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [accessType, setAccessType] = useState(null);

  // Square config from backend (dynamic)
  const [squareAppId, setSquareAppId] = useState('');
  const [squareLocationId, setSquareLocationId] = useState('');

  // Square subscribe state
  const [showSubscribe, setShowSubscribe] = useState(false);
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [subEmail, setSubEmail] = useState('');
  const [subFirstName, setSubFirstName] = useState('');
  const [subLastName, setSubLastName] = useState('');
  const [subPassword, setSubPassword] = useState('');
  const [subConfirmPw, setSubConfirmPw] = useState('');
  const [subStep, setSubStep] = useState('plans'); // plans -> details -> payment
  const [subLoading, setSubLoading] = useState(false);
  const [subError, setSubError] = useState(null);
  const [plans, setPlans] = useState([]);

  // Fetch Square config from backend on mount
  useEffect(() => {
    getSquareConfig()
      .then(res => {
        setSquareAppId(res.appId || '');
        setSquareLocationId(res.locationId || '');
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (showSubscribe && plans.length === 0) {
      getSquarePlans().then(res => setPlans(res.plans || [])).catch(() => {});
    }
  }, [showSubscribe, plans.length]);

  const handleEmailSubmit = async (e) => {
    e.preventDefault();
    if (!email.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await verifyWhop(email);
      if (res.verified) {
        localStorage.setItem('rp_email', res.email);
        localStorage.setItem('rp_token', res.session_token);
        localStorage.setItem('rp_access', res.access_type);
        onAuth({ email: res.email, token: res.session_token, accessType: res.access_type });
      } else if (res.requires_password) {
        setStep('password');
      } else if (res.requires_password_setup) {
        setAccessType(res.access_type);
        setStep('setup');
      } else {
        setError(res.message || 'No active membership found.');
      }
    } catch (err) {
      setError(err.message || 'Verification failed.');
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    if (!password) return;
    setLoading(true);
    setError(null);
    try {
      const res = await authLogin(email, password);
      if (res.verified) {
        localStorage.setItem('rp_email', res.email);
        localStorage.setItem('rp_token', res.session_token);
        localStorage.setItem('rp_access', res.access_type);
        onAuth({ email: res.email, token: res.session_token, accessType: res.access_type });
      } else {
        setError(res.message || 'Login failed.');
      }
    } catch (err) {
      setError(err.message || 'Login failed.');
    } finally {
      setLoading(false);
    }
  };

  const handleSetPassword = async (e) => {
    e.preventDefault();
    if (password.length < 6) { setError('Password must be at least 6 characters.'); return; }
    if (password !== confirmPassword) { setError('Passwords do not match.'); return; }
    setLoading(true);
    setError(null);
    try {
      const res = await apiSetPassword(email, password);
      if (res.verified) {
        localStorage.setItem('rp_email', res.email);
        localStorage.setItem('rp_token', res.session_token);
        localStorage.setItem('rp_access', res.access_type);
        onAuth({ email: res.email, token: res.session_token, accessType: res.access_type });
      }
    } catch (err) {
      setError(err.message || 'Failed to set password.');
    } finally {
      setLoading(false);
    }
  };

  const handleResetPassword = async (e) => {
    e.preventDefault();
    if (password.length < 6) { setError('Password must be at least 6 characters.'); return; }
    if (password !== confirmPassword) { setError('Passwords do not match.'); return; }
    setLoading(true);
    setError(null);
    try {
      const res = await resetPassword(email, password);
      if (res.verified) {
        localStorage.setItem('rp_email', res.email);
        localStorage.setItem('rp_token', res.session_token);
        localStorage.setItem('rp_access', res.access_type);
        onAuth({ email: res.email, token: res.session_token, accessType: res.access_type });
      }
    } catch (err) {
      setError(err.message || 'Failed to reset password.');
    } finally {
      setLoading(false);
    }
  };

  const startForgotPassword = async () => {
    setLoading(true);
    setError(null);
    setPassword('');
    setConfirmPassword('');
    try {
      const res = await verifyWhop(email);
      if (res.verified) {
        localStorage.setItem('rp_email', res.email);
        localStorage.setItem('rp_token', res.session_token);
        localStorage.setItem('rp_access', res.access_type);
        onAuth({ email: res.email, token: res.session_token, accessType: res.access_type });
      } else if (res.requires_password || res.requires_password_setup) {
        setAccessType(res.access_type || accessType || 'Member');
        setStep('reset');
      } else {
        setError(res.message || 'No active membership found. Cannot reset password.');
      }
    } catch (err) {
      setError(err.message || 'Verification failed.');
    } finally {
      setLoading(false);
    }
  };

  // Square payment callback
  const handleCardTokenized = async (token) => {
    if (!token?.token) { setSubError('Card tokenization failed.'); return; }
    if (subPassword.length < 6) { setSubError('Password must be at least 6 characters.'); return; }
    if (subPassword !== subConfirmPw) { setSubError('Passwords do not match.'); return; }

    setSubLoading(true);
    setSubError(null);
    try {
      const res = await squareSubscribe({
        email: subEmail,
        firstName: subFirstName,
        lastName: subLastName,
        sourceId: token.token,
        planKey: selectedPlan,
        password: subPassword,
      });
      if (res.success) {
        localStorage.setItem('rp_email', res.email);
        localStorage.setItem('rp_token', res.session_token);
        localStorage.setItem('rp_access', res.access_type);
        onAuth({ email: res.email, token: res.session_token, accessType: res.access_type });
      }
    } catch (err) {
      setSubError(err.message || 'Subscription failed.');
    } finally {
      setSubLoading(false);
    }
  };

  const resetSubscribeFlow = () => {
    setShowSubscribe(false);
    setSubStep('plans');
    setSelectedPlan(null);
    setSubEmail('');
    setSubFirstName('');
    setSubLastName('');
    setSubPassword('');
    setSubConfirmPw('');
    setSubError(null);
  };

  // ── SUBSCRIBE FLOW ──
  if (showSubscribe) {
    return (
      <div className="login-page" data-testid="subscribe-page">
        <div className="login-bg-glow" />
        <div className="login-bg-logo"><img src="/rp-logo.png" alt="" /></div>
        <div className="login-container" style={{ maxWidth: subStep === 'plans' ? 520 : 420 }}>
          <div className="login-logo">
            <img src="/rp-logo.png" alt="ReversePicks" className="login-logo-img" />
            <div className="logo-text" style={{ fontSize: 28 }}>Reverse<span>Picks</span></div>
          </div>

          {subStep === 'plans' && (
            <>
              <p style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.15em', textTransform: 'uppercase', color: 'var(--accent)', textAlign: 'center', marginBottom: 16 }}>Choose Your Plan</p>
              <div style={{ display: 'grid', gap: 10 }}>
                {[
                  { key: 'weekly', name: 'Weekly', price: '$11', period: '/week', desc: 'Billed weekly' },
                  { key: 'monthly', name: 'Monthly', price: '$39.99', period: '/month', desc: 'Save 9%', popular: true },
                  { key: 'quarterly', name: '3 Months', price: '$99.99', period: '/3mo', desc: 'Save 24%' },
                ].map(plan => (
                  <button
                    key={plan.key}
                    data-testid={`plan-${plan.key}`}
                    onClick={() => { setSelectedPlan(plan.key); setSubStep('details'); }}
                    style={{
                      position: 'relative',
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '16px 20px', borderRadius: 12,
                      background: plan.popular ? 'rgba(16,185,129,0.08)' : 'rgba(255,255,255,0.03)',
                      border: plan.popular ? '2px solid rgba(16,185,129,0.4)' : '1px solid rgba(255,255,255,0.08)',
                      cursor: 'pointer', transition: 'all 0.2s',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.transform = 'translateY(-1px)'; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = plan.popular ? 'rgba(16,185,129,0.4)' : 'rgba(255,255,255,0.08)'; e.currentTarget.style.transform = 'none'; }}
                  >
                    <div style={{ textAlign: 'left' }}>
                      <div style={{ fontSize: 15, fontWeight: 800, color: 'var(--text-primary)' }}>{plan.name}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{plan.desc}</div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <span style={{ fontSize: 24, fontWeight: 900, color: 'var(--accent)', fontFamily: "'JetBrains Mono', monospace" }}>{plan.price}</span>
                      <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{plan.period}</span>
                    </div>
                    {plan.popular && (
                      <div style={{ position: 'absolute', top: -9, right: 16, fontSize: 9, fontWeight: 800, letterSpacing: '0.1em', textTransform: 'uppercase', background: '#10b981', color: '#000', padding: '2px 10px', borderRadius: 20 }}>
                        Most Popular
                      </div>
                    )}
                  </button>
                ))}
              </div>
              <button className="btn-secondary" onClick={resetSubscribeFlow} style={{ marginTop: 16, width: '100%' }}>
                <ArrowLeft style={{ width: 14, height: 14 }} /> Back to Login
              </button>
            </>
          )}

          {subStep === 'details' && (
            <>
              <p style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.15em', textTransform: 'uppercase', color: 'var(--accent)', textAlign: 'center', marginBottom: 16 }}>Create Account</p>
              <form onSubmit={(e) => {
                e.preventDefault();
                if (!subEmail || !subFirstName || !subLastName) { setSubError('All fields required.'); return; }
                if (subPassword.length < 6) { setSubError('Password must be at least 6 characters.'); return; }
                if (subPassword !== subConfirmPw) { setSubError('Passwords do not match.'); return; }
                setSubError(null);
                setSubStep('payment');
              }} className="login-form">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  <div className="login-field">
                    <div className="login-field-icon"><User style={{ width: 16, height: 16 }} /></div>
                    <input type="text" placeholder="First name" value={subFirstName} onChange={e => setSubFirstName(e.target.value)} required data-testid="sub-first-name" />
                  </div>
                  <div className="login-field">
                    <div className="login-field-icon"><User style={{ width: 16, height: 16 }} /></div>
                    <input type="text" placeholder="Last name" value={subLastName} onChange={e => setSubLastName(e.target.value)} required data-testid="sub-last-name" />
                  </div>
                </div>
                <div className="login-field">
                  <div className="login-field-icon"><Mail style={{ width: 16, height: 16 }} /></div>
                  <input type="email" placeholder="Email" value={subEmail} onChange={e => setSubEmail(e.target.value)} required data-testid="sub-email" />
                </div>
                <div className="login-field">
                  <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
                  <input type="password" placeholder="Password (min 6 chars)" value={subPassword} onChange={e => setSubPassword(e.target.value)} required data-testid="sub-password" />
                </div>
                <div className="login-field">
                  <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
                  <input type="password" placeholder="Confirm password" value={subConfirmPw} onChange={e => setSubConfirmPw(e.target.value)} required data-testid="sub-confirm-pw" />
                </div>
                <button className="btn-primary" type="submit" data-testid="sub-continue-btn">
                  <CreditCard style={{ width: 16, height: 16 }} /> Continue to Payment
                </button>
                <button type="button" className="btn-secondary" onClick={() => setSubStep('plans')}>
                  <ArrowLeft style={{ width: 14, height: 14 }} /> Back
                </button>
              </form>
            </>
          )}

          {subStep === 'payment' && (
            <>
              <div style={{ textAlign: 'center', marginBottom: 16 }}>
                <div className="badge neon" style={{ marginBottom: 8 }}>
                  {selectedPlan === 'weekly' ? '$11/week' : selectedPlan === 'monthly' ? '$39.99/month' : '$99.99/3 months'}
                </div>
                <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>Enter card details to complete subscription</p>
              </div>
              <div data-testid="square-payment-form">
                {squareAppId && squareLocationId ? (
                <PaymentForm
                  applicationId={squareAppId}
                  locationId={squareLocationId}
                  cardTokenizeResponseReceived={(token) => handleCardTokenized(token)}
                  createPaymentRequest={() => ({
                    countryCode: 'US',
                    currencyCode: 'USD',
                  })}
                >
                  <SquareCreditCard
                    style={{
                      '.input-container': { borderColor: 'rgba(255,255,255,0.15)', borderRadius: '8px' },
                      '.input-container.is-focus': { borderColor: '#10b981' },
                      '.message-text': { color: '#f43f5e' },
                      '.message-icon': { color: '#f43f5e' },
                      input: { color: '#fff', fontSize: '14px' },
                      'input::placeholder': { color: 'rgba(255,255,255,0.3)' },
                    }}
                    render={(Button) => (
                      <Button
                        css={{
                          background: 'linear-gradient(135deg, var(--accent), var(--neon))',
                          color: '#000',
                          fontWeight: 800,
                          fontSize: 14,
                          borderRadius: 10,
                          padding: '14px',
                          marginTop: 12,
                          width: '100%',
                          cursor: 'pointer',
                          border: 'none',
                          transition: 'opacity 0.2s',
                          '&:hover': { opacity: 0.9 },
                          '&:disabled': { opacity: 0.5, cursor: 'not-allowed' },
                        }}
                        data-testid="sub-pay-btn"
                      >
                        {subLoading ? 'Processing...' : 'Subscribe Now'}
                      </Button>
                    )}
                  />
                </PaymentForm>
                ) : (
                  <div style={{ textAlign: 'center', padding: 20, color: 'var(--text-muted)', fontSize: 13 }}>Loading payment form...</div>
                )}
              </div>
              <button className="btn-secondary" onClick={() => setSubStep('details')} style={{ marginTop: 12, width: '100%' }}>
                <ArrowLeft style={{ width: 14, height: 14 }} /> Back
              </button>
            </>
          )}

          {subError && (
            <div className="error-box" style={{ marginTop: 16 }}>
              <ShieldAlert /><p>{subError}</p>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── EXISTING LOGIN FLOW ──
  return (
    <div className="login-page" data-testid="login-page">
      <div className="login-bg-glow" />
      <div className="login-bg-logo">
        <img src="/rp-logo.png" alt="" />
      </div>
      <div className="login-container">
        <div className="login-logo">
          <img src="/rp-logo.png" alt="ReversePicks" className="login-logo-img" />
          <div className="logo-text" style={{ fontSize: 28 }}>Reverse<span>Picks</span></div>
          <p style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.15em', textTransform: 'uppercase', color: 'var(--text-muted)', marginTop: 4 }}>Elite Prop Intelligence</p>
        </div>

        {step === 'email' && (
          <form onSubmit={handleEmailSubmit} className="login-form" data-testid="email-form">
            <div className="login-field">
              <div className="login-field-icon"><Mail style={{ width: 16, height: 16 }} /></div>
              <input type="email" placeholder="Enter your email" value={email}
                onChange={e => setEmail(e.target.value)} autoFocus data-testid="email-input" />
            </div>
            <button className="btn-primary" type="submit" disabled={loading || !email.trim()} data-testid="verify-btn">
              {loading ? <Loader2 className="animate-spin" /> : <Zap style={{ fill: 'currentColor' }} />}
              {loading ? 'Verifying...' : 'Verify Access'}
            </button>
          </form>
        )}

        {step === 'password' && (
          <form onSubmit={handleLogin} className="login-form" data-testid="password-form">
            <div className="badge neon" style={{ alignSelf: 'center', marginBottom: 8 }}>Membership Verified</div>
            <div className="login-field">
              <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
              <input type="password" placeholder="Enter your password" value={password}
                onChange={e => setPassword(e.target.value)} autoFocus data-testid="password-input" />
            </div>
            <button className="btn-primary" type="submit" disabled={loading || !password} data-testid="login-btn">
              {loading ? <Loader2 className="animate-spin" /> : <Zap style={{ fill: 'currentColor' }} />}
              {loading ? 'Logging in...' : 'Log In'}
            </button>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <button type="button" className="btn-secondary" onClick={() => { setStep('email'); setPassword(''); setError(null); }}>
                Back
              </button>
              <button type="button" className="forgot-password-link" onClick={startForgotPassword}
                disabled={loading} data-testid="forgot-password-btn">
                Forgot Password?
              </button>
            </div>
          </form>
        )}

        {step === 'reset' && (
          <form onSubmit={handleResetPassword} className="login-form" data-testid="reset-form">
            <div className="badge neon" style={{ alignSelf: 'center', marginBottom: 8 }}>
              {accessType} Access Re-Verified
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', textAlign: 'center' }}>Set a new password for your account</p>
            <div className="login-field">
              <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
              <input type="password" placeholder="New password (min 6 chars)" value={password}
                onChange={e => setPassword(e.target.value)} autoFocus data-testid="reset-new-password-input" />
            </div>
            <div className="login-field">
              <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
              <input type="password" placeholder="Confirm new password" value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)} data-testid="reset-confirm-password-input" />
            </div>
            <button className="btn-primary" type="submit" disabled={loading || password.length < 6} data-testid="reset-password-btn">
              {loading ? <Loader2 className="animate-spin" /> : <Zap style={{ fill: 'currentColor' }} />}
              {loading ? 'Resetting...' : 'Reset Password & Enter'}
            </button>
            <button type="button" className="btn-secondary" onClick={() => { setStep('password'); setPassword(''); setConfirmPassword(''); setError(null); }}>
              Back to Login
            </button>
          </form>
        )}

        {step === 'setup' && (
          <form onSubmit={handleSetPassword} className="login-form" data-testid="setup-form">
            <div className="badge neon" style={{ alignSelf: 'center', marginBottom: 8 }}>
              {accessType} Access Confirmed
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', textAlign: 'center' }}>Set a password for future logins</p>
            <div className="login-field">
              <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
              <input type="password" placeholder="Create password (min 6 chars)" value={password}
                onChange={e => setPassword(e.target.value)} autoFocus data-testid="new-password-input" />
            </div>
            <div className="login-field">
              <div className="login-field-icon"><Lock style={{ width: 16, height: 16 }} /></div>
              <input type="password" placeholder="Confirm password" value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)} data-testid="confirm-password-input" />
            </div>
            <button className="btn-primary" type="submit" disabled={loading || password.length < 6} data-testid="set-password-btn">
              {loading ? <Loader2 className="animate-spin" /> : <Zap style={{ fill: 'currentColor' }} />}
              {loading ? 'Setting up...' : 'Set Password & Enter'}
            </button>
          </form>
        )}

        {error && (
          <div className="error-box" style={{ marginTop: 16 }}>
            <ShieldAlert /><p>{error}</p>
          </div>
        )}

        <div style={{ marginTop: 24, textAlign: 'center' }} data-testid="signup-options">
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 10 }}>Not a member yet?</p>
          <button
            onClick={() => setShowSubscribe(true)}
            data-testid="subscribe-btn"
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 8,
              fontSize: 14, fontWeight: 800, color: '#000',
              background: 'linear-gradient(135deg, var(--accent), var(--neon))',
              border: 'none', borderRadius: 10, padding: '12px 28px',
              cursor: 'pointer', transition: 'opacity 0.2s',
            }}
            onMouseEnter={e => e.currentTarget.style.opacity = '0.85'}
            onMouseLeave={e => e.currentTarget.style.opacity = '1'}
          >
            <CreditCard style={{ width: 16, height: 16 }} />
            Subscribe Now
          </button>
        </div>
      </div>
    </div>
  );
}
