import React, { useState } from 'react';
import { ShieldAlert, Loader2, Lock, ArrowRight, CheckCircle2 } from 'lucide-react';

export function LoginPage({ onLogin }: { onLogin: (data: any) => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  
  // 'email' | 'login' | 'setup' | 'reset'
  const [step, setStep] = useState<'email' | 'login' | 'setup' | 'reset'>('email');

  const getDeviceId = () => {
    let id = localStorage.getItem("rp_device_id");
    if (!id) {
      id = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now();
      localStorage.setItem("rp_device_id", id);
    }
    return id;
  };

  const handleEmailSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      const res = await fetch('/api/auth/verify-whop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim(),
          device_id: getDeviceId()
        })
      });
      
      const data = await res.json();
      
      if (data.verified) {
        // Owner bypass or already verified via some other means
        localStorage.setItem("rp_session_token", data.session_token || "");
        onLogin({
          email: data.email,
          accessType: data.access_type || "Premium",
          sessionToken: data.session_token || ""
        });
      } else if (data.requires_password) {
        setStep('login');
      } else if (data.requires_password_setup) {
        setStep('setup');
      } else {
        setError(data.message || 'No active membership found.');
      }
    } catch (err) {
      setError('Failed to connect to authentication server.');
    } finally {
      setLoading(false);
    }
  };

  const handleLoginSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim(),
          password
        })
      });
      
      const data = await res.json();
      
      if (data.verified) {
        localStorage.setItem("rp_session_token", data.session_token || "");
        onLogin({
          email: data.email,
          accessType: data.access_type || "Premium",
          sessionToken: data.session_token || ""
        });
      } else {
        setError(data.message || 'Invalid credentials.');
      }
    } catch (err) {
      setError('Failed to connect to authentication server.');
    } finally {
      setLoading(false);
    }
  };

  const handleSetupSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    if (password.length < 6) {
      setError('Password must be at least 6 characters.');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const res = await fetch('/api/auth/set-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim(),
          password
        })
      });
      
      const data = await res.json();
      
      if (data.verified) {
        localStorage.setItem("rp_session_token", data.session_token || "");
        onLogin({
          email: data.email,
          accessType: data.access_type || "Premium",
          sessionToken: data.session_token || ""
        });
      } else {
        setError(data.message || 'Failed to set password.');
      }
    } catch (err) {
      setError('Failed to connect to authentication server.');
    } finally {
      setLoading(false);
    }
  };

  const handleResetSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    if (password.length < 6) {
      setError('Password must be at least 6 characters.');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const res = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim(),
          password
        })
      });
      
      const data = await res.json();
      
      if (data.verified) {
        localStorage.setItem("rp_session_token", data.session_token || "");
        onLogin({
          email: data.email,
          accessType: data.access_type || "Premium",
          sessionToken: data.session_token || ""
        });
      } else {
        setError(data.message || 'Failed to reset password.');
      }
    } catch (err) {
      setError('Failed to connect to authentication server.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-900 flex flex-col items-center justify-center p-4">
      <div className="w-full max-w-md bg-white border border-zinc-200 rounded-3xl p-8 shadow-xl">
        <div className="flex justify-center mb-6">
          <div className="w-16 h-16 bg-emerald-500/10 rounded-full flex items-center justify-center border border-emerald-500/20">
            {step === 'email' ? <ShieldAlert className="w-8 h-8 text-emerald-500" /> : <Lock className="w-8 h-8 text-emerald-500" />}
          </div>
        </div>
        <h1 className="text-2xl font-black text-center mb-2 text-zinc-900">Reverse Picks</h1>
        
        {step === 'email' && (
          <>
            <p className="text-zinc-500 text-center mb-8 text-sm font-medium">Enter your email to access premium predictions.</p>
            <form onSubmit={handleEmailSubmit} className="space-y-4">
              <div>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="Email address"
                  className="w-full bg-white border border-zinc-200 rounded-xl px-4 py-3 text-zinc-900 placeholder-zinc-400 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all shadow-sm"
                  required
                />
              </div>
              
              {error && (
                <div className="bg-rose-500/10 border border-rose-500/20 text-rose-600 text-sm p-3 rounded-xl text-center font-medium">
                  {error}
                </div>
              )}
              
              <button
                type="submit"
                disabled={loading}
                className="w-full bg-emerald-500 text-white font-black py-3 rounded-xl hover:bg-emerald-600 transition-all shadow-lg shadow-emerald-500/20 flex items-center justify-center disabled:opacity-50"
              >
                {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : 'Continue'}
              </button>
            </form>
          </>
        )}

        {step === 'login' && (
          <>
            <p className="text-zinc-500 text-center mb-8 text-sm font-medium">Welcome back. Please enter your password.</p>
            <form onSubmit={handleLoginSubmit} className="space-y-4">
              <div>
                <input
                  type="email"
                  value={email}
                  disabled
                  className="w-full bg-zinc-50 border border-zinc-200 rounded-xl px-4 py-3 text-zinc-400 cursor-not-allowed shadow-inner"
                />
              </div>
              <div>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Password"
                  className="w-full bg-white border border-zinc-200 rounded-xl px-4 py-3 text-zinc-900 placeholder-zinc-400 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all shadow-sm"
                  required
                />
              </div>
              
              {error && (
                <div className="bg-rose-500/10 border border-rose-500/20 text-rose-600 text-sm p-3 rounded-xl text-center font-medium">
                  {error}
                </div>
              )}
              
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={() => {
                    setStep('reset');
                    setPassword('');
                    setConfirmPassword('');
                    setError('');
                  }}
                  className="text-sm text-emerald-600 hover:text-emerald-700 font-bold transition-colors"
                >
                  Forgot Password?
                </button>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full bg-emerald-500 text-white font-black py-3 rounded-xl hover:bg-emerald-600 transition-all shadow-lg shadow-emerald-500/20 flex items-center justify-center disabled:opacity-50"
              >
                {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : 'Sign In'}
              </button>
              
              <button
                type="button"
                onClick={() => { setStep('email'); setPassword(''); setError(''); }}
                className="w-full text-zinc-500 text-sm hover:text-zinc-800 font-bold transition-colors mt-4"
              >
                Use a different email
              </button>
            </form>
          </>
        )}

        {step === 'reset' && (
          <>
            <p className="text-zinc-500 text-center mb-8 text-sm font-medium">Reset your password.</p>
            <form onSubmit={handleResetSubmit} className="space-y-4">
              <div>
                <input
                  type="email"
                  value={email}
                  disabled
                  className="w-full bg-zinc-50 border border-zinc-200 rounded-xl px-4 py-3 text-zinc-400 cursor-not-allowed shadow-inner"
                />
              </div>
              <div>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="New Password (min. 6 characters)"
                  className="w-full bg-white border border-zinc-200 rounded-xl px-4 py-3 text-zinc-900 placeholder-zinc-400 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all shadow-sm"
                  required
                />
              </div>
              <div>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="Confirm New Password"
                  className="w-full bg-white border border-zinc-200 rounded-xl px-4 py-3 text-zinc-900 placeholder-zinc-400 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all shadow-sm"
                  required
                />
              </div>
              
              {error && (
                <div className="bg-rose-500/10 border border-rose-500/20 text-rose-600 text-sm p-3 rounded-xl text-center font-medium">
                  {error}
                </div>
              )}
              
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => {
                    setStep('login');
                    setPassword('');
                    setConfirmPassword('');
                    setError('');
                  }}
                  disabled={loading}
                  className="w-1/3 bg-zinc-100 text-zinc-600 font-bold py-3 rounded-xl hover:bg-zinc-200 transition-colors flex items-center justify-center disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={loading}
                  className="w-2/3 bg-emerald-500 text-white font-black py-3 rounded-xl hover:bg-emerald-600 transition-all shadow-lg shadow-emerald-500/20 flex items-center justify-center disabled:opacity-50"
                >
                  {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : 'Reset Password'}
                </button>
              </div>
            </form>
          </>
        )}

        {step === 'setup' && (
          <>
            <p className="text-emerald-600 text-center mb-2 text-sm font-bold flex items-center justify-center gap-2">
              <CheckCircle2 className="w-4 h-4" /> Subscription Verified
            </p>
            <p className="text-zinc-500 text-center mb-8 text-sm font-medium">Please set a password for future logins.</p>
            <form onSubmit={handleSetupSubmit} className="space-y-4">
              <div>
                <input
                  type="email"
                  value={email}
                  disabled
                  className="w-full bg-zinc-50 border border-zinc-200 rounded-xl px-4 py-3 text-zinc-400 cursor-not-allowed shadow-inner"
                />
              </div>
              <div>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Create Password (min 6 chars)"
                  className="w-full bg-white border border-zinc-200 rounded-xl px-4 py-3 text-zinc-900 placeholder-zinc-400 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all shadow-sm"
                  required
                  minLength={6}
                />
              </div>
              <div>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="Confirm Password"
                  className="w-full bg-white border border-zinc-200 rounded-xl px-4 py-3 text-zinc-900 placeholder-zinc-400 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all shadow-sm"
                  required
                  minLength={6}
                />
              </div>
              
              {error && (
                <div className="bg-rose-500/10 border border-rose-500/20 text-rose-600 text-sm p-3 rounded-xl text-center font-medium">
                  {error}
                </div>
              )}
              
              <button
                type="submit"
                disabled={loading}
                className="w-full bg-emerald-500 text-white font-black py-3 rounded-xl hover:bg-emerald-600 transition-all shadow-lg shadow-emerald-500/20 flex items-center justify-center disabled:opacity-50 gap-2"
              >
                {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : (
                  <>Set Password & Enter <ArrowRight className="w-4 h-4" /></>
                )}
              </button>
            </form>
          </>
        )}
        
        {step === 'email' && (
          <div className="mt-6 text-center">
            <a href="https://whop.com/biz_xLCux4k1X7U3AU" target="_blank" rel="noreferrer" className="text-xs text-zinc-500 hover:text-emerald-600 font-bold transition-colors">
              Don't have access? Get Premium
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
