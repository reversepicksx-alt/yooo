import React, { createContext, useContext, useEffect, useState } from 'react';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { authLogin, authLogout, heartbeatSession, verifySession, AuthResponse } from '@/lib/api';

interface Session {
  email: string;
  token: string;
  accessType?: string;
}

interface AuthContextType {
  session: Session | null;
  email?: string;
  accessType?: string;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  loginWithResponse: (resp: AuthResponse) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

const storage = {
  async get(key: string) {
    if (Platform.OS === 'web') return localStorage.getItem(key);
    return SecureStore.getItemAsync(key);
  },
  async set(key: string, value: string) {
    if (Platform.OS === 'web') { localStorage.setItem(key, value); return; }
    return SecureStore.setItemAsync(key, value);
  },
  async delete(key: string) {
    if (Platform.OS === 'web') { localStorage.removeItem(key); return; }
    return SecureStore.deleteItemAsync(key);
  },
};

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const email = await storage.get('rp_email');
        const token = await storage.get('rp_token');
        if (email && token) {
          let explicitlyInvalid = false;
          let freshAccessType: string | undefined;
          try {
            // Race the session check against a 4s timeout so a slow/unreachable
            // backend never hangs the app on a permanent black screen.
            const result = await Promise.race([
              verifySession(email, token) as Promise<{ valid?: boolean; access_type?: string }>,
              new Promise<{ valid?: boolean; access_type?: string }>(resolve =>
                setTimeout(() => resolve({}), 4000)
              ),
            ]);
            if (result?.valid === false) explicitlyInvalid = true;
            if (result?.valid === true && result.access_type) freshAccessType = result.access_type;
          } catch {
            // Network error — assume session still valid; backend will 401 if truly expired.
          }
          if (explicitlyInvalid) {
            await storage.delete('rp_email');
            await storage.delete('rp_token');
            await storage.delete('rp_access_type');
          } else {
            // Prefer the freshly re-verified access type from the backend over the
            // stale cached value — otherwise a session that was fixed server-side
            // (e.g. a delayed Apple/RevenueCat webhook that has now synced) would
            // stay stuck showing the old "NoSubscription" status until logout/login.
            const cachedAccessType = await storage.get('rp_access_type') || undefined;
            const accessType = freshAccessType || cachedAccessType;
            if (freshAccessType && freshAccessType !== cachedAccessType) {
              await storage.set('rp_access_type', freshAccessType);
            }
            setSession({ email, token, accessType });
          }
        }
      } catch {
        // SecureStore read failure — leave session null (user re-logins)
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  // Keep authenticated presence current for the community online indicator.
  // The endpoint is deliberately cheap and does not re-run entitlement checks.
  useEffect(() => {
    if (!session) return;
    const beat = () => {
      heartbeatSession(session.email, session.token).catch(() => {});
    };
    beat();
    const timer = setInterval(beat, 60_000);
    return () => clearInterval(timer);
  }, [session]);

  const saveSession = async (resp: AuthResponse) => {
    await storage.set('rp_email', resp.email);
    await storage.set('rp_token', resp.session_token);
    if (resp.access_type) await storage.set('rp_access_type', resp.access_type);
    setSession({ email: resp.email, token: resp.session_token, accessType: resp.access_type });
  };

  const login = async (email: string, password: string) => {
    const resp: AuthResponse = await authLogin(email.toLowerCase().trim(), password);
    await saveSession(resp);
  };

  const loginWithResponse = async (resp: AuthResponse) => {
    await saveSession(resp);
  };

  const logout = async () => {
    // Clear local state immediately so the UI responds right away
    const snap = session;
    await storage.delete('rp_email');
    await storage.delete('rp_token');
    await storage.delete('rp_access_type');
    setSession(null);
    // Fire-and-forget: invalidate session on server (best-effort)
    if (snap) {
      authLogout(snap.email, snap.token).catch(() => {});
    }
  };

  return (
    <AuthContext.Provider value={{ session, email: session?.email, accessType: session?.accessType, isLoading, login, loginWithResponse, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be inside AuthProvider');
  return ctx;
}
