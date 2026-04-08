import { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ActivityIndicator,
  ScrollView,
} from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '@/contexts/AuthContext';
import Colors from '@/constants/colors';
import { verifyAccess, setPassword, authLogin } from '@/lib/api';

type Step = 'email' | 'password' | 'setup';

export default function AuthScreen() {
  const insets = useSafeAreaInsets();
  const { login } = useAuth();
  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleCheckEmail = async () => {
    if (!email.trim()) { setError('Please enter your email.'); return; }
    setLoading(true);
    setError('');
    try {
      const result = await verifyAccess(email.trim());
      if (result.requires_password_setup) {
        setStep('setup');
      } else if (result.requires_password) {
        setStep('password');
      } else if (result.verified && result.session_token) {
        await login(email.trim(), '');
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        router.replace('/(tabs)/scan');
      } else {
        setError(result.message || 'No active membership found. Contact your administrator.');
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to verify access.');
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = async () => {
    if (!password.trim()) { setError('Please enter your password.'); return; }
    setLoading(true);
    setError('');
    try {
      await login(email.trim(), password);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      router.replace('/(tabs)/scan');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Login failed.');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setLoading(false);
    }
  };

  const handleSetPassword = async () => {
    if (password.length < 6) { setError('Password must be at least 6 characters.'); return; }
    if (password !== confirmPassword) { setError('Passwords do not match.'); return; }
    setLoading(true);
    setError('');
    try {
      const result = await setPassword(email.trim(), password);
      await login(email.trim(), password);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      router.replace('/(tabs)/scan');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to set password.');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setLoading(false);
    }
  };

  const goBack = () => {
    setStep('email');
    setPassword('');
    setConfirmPassword('');
    setError('');
  };

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView
        contentContainerStyle={[styles.container, { paddingTop: insets.top + 60, paddingBottom: insets.bottom + 40 }]}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.logoRow}>
          <View style={styles.logoCircle}>
            <Ionicons name="football" size={36} color={Colors.primary} />
          </View>
        </View>

        <Text style={styles.title}>ReversePicks</Text>
        <Text style={styles.subtitle}>Soccer AI Analytics</Text>

        <View style={styles.form}>
          {step === 'email' && (
            <>
              <View style={styles.inputWrapper}>
                <Ionicons name="mail-outline" size={18} color={Colors.textSecondary} style={styles.inputIcon} />
                <TextInput
                  style={styles.input}
                  placeholder="Email address"
                  placeholderTextColor={Colors.textSecondary}
                  value={email}
                  onChangeText={setEmail}
                  keyboardType="email-address"
                  autoCapitalize="none"
                  autoCorrect={false}
                  onSubmitEditing={handleCheckEmail}
                  returnKeyType="next"
                />
              </View>

              {!!error && <ErrorBox message={error} />}

              <TouchableOpacity
                style={[styles.loginBtn, loading && styles.loginBtnDisabled]}
                onPress={handleCheckEmail}
                disabled={loading}
                activeOpacity={0.8}
              >
                {loading
                  ? <ActivityIndicator color="#000" />
                  : <Text style={styles.loginBtnText}>Continue</Text>
                }
              </TouchableOpacity>
            </>
          )}

          {step === 'password' && (
            <>
              <View style={styles.emailBadge}>
                <Ionicons name="mail" size={14} color={Colors.primary} />
                <Text style={styles.emailBadgeText}>{email}</Text>
                <TouchableOpacity onPress={goBack} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                  <Ionicons name="close-circle" size={16} color={Colors.textSecondary} />
                </TouchableOpacity>
              </View>

              <PasswordInput
                value={password}
                onChange={setPassword}
                placeholder="Password"
                show={showPassword}
                onToggle={() => setShowPassword(!showPassword)}
                onSubmit={handleLogin}
              />

              {!!error && <ErrorBox message={error} />}

              <TouchableOpacity
                style={[styles.loginBtn, loading && styles.loginBtnDisabled]}
                onPress={handleLogin}
                disabled={loading}
                activeOpacity={0.8}
              >
                {loading
                  ? <ActivityIndicator color="#000" />
                  : <Text style={styles.loginBtnText}>Sign In</Text>
                }
              </TouchableOpacity>
            </>
          )}

          {step === 'setup' && (
            <>
              <View style={styles.emailBadge}>
                <Ionicons name="mail" size={14} color={Colors.primary} />
                <Text style={styles.emailBadgeText}>{email}</Text>
                <TouchableOpacity onPress={goBack} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                  <Ionicons name="close-circle" size={16} color={Colors.textSecondary} />
                </TouchableOpacity>
              </View>

              <Text style={styles.setupLabel}>Create your password to get started</Text>

              <PasswordInput
                value={password}
                onChange={setPassword}
                placeholder="Choose a password"
                show={showPassword}
                onToggle={() => setShowPassword(!showPassword)}
              />
              <PasswordInput
                value={confirmPassword}
                onChange={setConfirmPassword}
                placeholder="Confirm password"
                show={showPassword}
                onToggle={() => setShowPassword(!showPassword)}
                onSubmit={handleSetPassword}
              />

              {!!error && <ErrorBox message={error} />}

              <TouchableOpacity
                style={[styles.loginBtn, loading && styles.loginBtnDisabled]}
                onPress={handleSetPassword}
                disabled={loading}
                activeOpacity={0.8}
              >
                {loading
                  ? <ActivityIndicator color="#000" />
                  : <Text style={styles.loginBtnText}>Set Password & Sign In</Text>
                }
              </TouchableOpacity>
            </>
          )}
        </View>

        <Text style={styles.footnote}>
          Don't have an account? Contact your administrator to get access.
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <View style={styles.errorBox}>
      <Ionicons name="alert-circle-outline" size={16} color={Colors.error} />
      <Text style={styles.errorText}>{message}</Text>
    </View>
  );
}

function PasswordInput({
  value, onChange, placeholder, show, onToggle, onSubmit,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  show: boolean;
  onToggle: () => void;
  onSubmit?: () => void;
}) {
  return (
    <View style={styles.inputWrapper}>
      <Ionicons name="lock-closed-outline" size={18} color={Colors.textSecondary} style={styles.inputIcon} />
      <TextInput
        style={styles.input}
        placeholder={placeholder}
        placeholderTextColor={Colors.textSecondary}
        value={value}
        onChangeText={onChange}
        secureTextEntry={!show}
        onSubmitEditing={onSubmit}
        returnKeyType={onSubmit ? 'done' : 'next'}
      />
      <TouchableOpacity onPress={onToggle} style={styles.eyeBtn}>
        <Ionicons name={show ? 'eye-off-outline' : 'eye-outline'} size={18} color={Colors.textSecondary} />
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  container: { paddingHorizontal: 28, alignItems: 'center' },
  logoRow: { marginBottom: 20 },
  logoCircle: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: Colors.primaryDim,
    borderWidth: 1,
    borderColor: Colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  title: { fontSize: 32, fontWeight: '800', color: Colors.text, letterSpacing: -0.5 },
  subtitle: { fontSize: 15, color: Colors.textSecondary, marginTop: 6, marginBottom: 44 },
  form: { width: '100%', gap: 12 },
  emailBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  emailBadgeText: { flex: 1, color: Colors.text, fontSize: 14 },
  setupLabel: { color: Colors.textSecondary, fontSize: 13, textAlign: 'center', marginTop: 4 },
  inputWrapper: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: 14,
    height: 52,
  },
  inputIcon: { marginRight: 10 },
  input: { flex: 1, color: Colors.text, fontSize: 15 },
  eyeBtn: { padding: 4 },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.errorDim,
    padding: 12,
    borderRadius: Colors.radius,
    gap: 8,
  },
  errorText: { color: Colors.error, fontSize: 13, flex: 1 },
  loginBtn: {
    backgroundColor: Colors.primary,
    borderRadius: Colors.radius,
    height: 52,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 8,
  },
  loginBtnDisabled: { opacity: 0.6 },
  loginBtnText: { color: '#000', fontWeight: '700', fontSize: 16 },
  footnote: { color: Colors.textTertiary, fontSize: 13, textAlign: 'center', marginTop: 36, lineHeight: 20 },
});
