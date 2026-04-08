import React, { createContext, useContext, useEffect, useState } from 'react';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { authLogin, authLogout, verifySession, AuthResponse } from '@/lib/api';

interface Session {
  email: string;
  token: string;
  accessType?: string;
}

interface AuthContextType {
  session: Session | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
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
          await verifySession(email, token);
          const accessType = await storage.get('rp_access_type') || undefined;
          setSession({ email, token, accessType });
        }
      } catch {
        await storage.delete('rp_email');
        await storage.delete('rp_token');
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  const login = async (email: string, password: string) => {
    const resp: AuthResponse = await authLogin(email.toLowerCase().trim(), password);
    await storage.set('rp_email', resp.email);
    await storage.set('rp_token', resp.session_token);
    if (resp.access_type) await storage.set('rp_access_type', resp.access_type);
    setSession({ email: resp.email, token: resp.session_token, accessType: resp.access_type });
  };

  const logout = async () => {
    if (session) {
      try { await authLogout(session.email, session.token); } catch {}
    }
    await storage.delete('rp_email');
    await storage.delete('rp_token');
    await storage.delete('rp_access_type');
    setSession(null);
  };

  return (
    <AuthContext.Provider value={{ session, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be inside AuthProvider');
  return ctx;
}
