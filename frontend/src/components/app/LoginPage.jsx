import React, { useState } from 'react';
import { Zap, Loader2, Lock, Mail, ShieldAlert } from 'lucide-react';
import { verifyWhop, authLogin, setPassword as apiSetPassword, resetPassword } from '../../api';

export function LoginPage({ onAuth }) {
  const [step, setStep] = useState('email');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [accessType, setAccessType] = useState(null);

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

        <div style={{ marginTop: 24, textAlign: 'center' }} data-testid="whop-signup-link">
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>Not yet a premium member?</p>
          <a href="https://whop.com/checkout/plan_XZqvPZlrENzGt" target="_blank" rel="noopener noreferrer"
            style={{ fontSize: 13, fontWeight: 600, color: 'var(--neon)', textDecoration: 'none', transition: 'opacity 0.2s' }}
            onMouseEnter={e => e.currentTarget.style.opacity = '0.7'}
            onMouseLeave={e => e.currentTarget.style.opacity = '1'}>
            Join on Whop &rarr;
          </a>
        </div>
      </div>
    </div>
  );
}
