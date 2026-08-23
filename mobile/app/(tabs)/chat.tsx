import React, { useState, useRef, useEffect } from 'react';
import {
  View, Text, StyleSheet, FlatList, TextInput,
  TouchableOpacity, ActivityIndicator, KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { Redirect } from 'expo-router';
import * as SecureStore from 'expo-secure-store';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { startLissa, sendLissaMessage } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

interface Msg {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  tools?: string[];
}

async function loadChatHistory(email: string): Promise<Msg[]> {
  const key = `jarvis-chat:${email.toLowerCase()}`;
  try {
    const raw = Platform.OS === 'web'
      ? localStorage.getItem(key)
      : await SecureStore.getItemAsync(key);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.slice(-100) : [];
  } catch {
    return [];
  }
}

async function saveChatHistory(email: string, messages: Msg[]) {
  const key = `jarvis-chat:${email.toLowerCase()}`;
  try {
    const raw = JSON.stringify(messages.slice(-100));
    if (Platform.OS === 'web') localStorage.setItem(key, raw);
    else await SecureStore.setItemAsync(key, raw);
  } catch {
    // History is a convenience; a storage failure must not block conversation.
  }
}

export default function ChatScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const flatRef = useRef<FlatList>(null);
  const topPad = Platform.OS === 'web' ? 67 : insets.top;
  const bottomPad = Platform.OS === 'web' ? 34 : insets.bottom;

  useEffect(() => {
    if (!session?.email || !session.token) return;
    let cancelled = false;
    (async () => {
      try {
        const history = await loadChatHistory(session.email);
        const resp = await startLissa(session.email, session.token);
        if (cancelled) return;
        setSessionId(resp.sessionId);
        setMessages(history.length ? history : [{
          id: '0',
          role: 'assistant',
          text: resp.message || resp.response || 'I’m JARVIS. Tell me what to run.',
        }]);
      } catch {
        setMessages([{
          id: '0',
          role: 'assistant',
          text: 'JARVIS is unavailable right now. Please check your owner session and try again.',
        }]);
      } finally {
        if (!cancelled) setInitializing(false);
      }
    })();
    return () => { cancelled = true; };
  }, [session?.email, session?.token]);

  useEffect(() => {
    if (session?.email && messages.length) void saveChatHistory(session.email, messages);
  }, [session?.email, messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || loading || !sessionId) return;
    setInput('');
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);

    const userMsg: Msg = {
      id: Date.now().toString(),
      role: 'user',
      text,
    };
    setMessages(prev => [...prev, userMsg]);
    setLoading(true);

    try {
      const sid = sessionId;
      if (!session?.email || !session.token) throw new Error('Owner session required');
      const resp = await sendLissaMessage(session.email, session.token, sid, text);
      setMessages(prev => [...prev, {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        text: resp.response || resp.message || 'I could not find a verified answer for that yet.',
        tools: (resp.tools || resp.orchestration?.tools || []).map(tool => `${tool.name}:${tool.status}`),
      }]);
    } catch {
      setMessages(prev => [...prev, {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        text: 'Connection error. Please try again.',
      }]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (messages.length > 0) {
      setTimeout(() => flatRef.current?.scrollToEnd({ animated: true }), 100);
    }
  }, [messages.length]);

  if (session?.accessType?.toLowerCase() !== 'owner') {
    return <Redirect href="/(tabs)/account" />;
  }

  if (initializing) {
    return (
      <View style={[styles.root, { paddingTop: topPad }]}>
        <View style={styles.header}>
          <Text style={styles.headerTitle}>JARVIS</Text>
        </View>
        <View style={styles.center}>
          <ActivityIndicator color={Colors.primary} />
        </View>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={[styles.root, { paddingTop: topPad }]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={0}
    >
      <View style={styles.header}>
        <View style={styles.headerIdentity}>
          <View style={styles.headerMark}>
            <Ionicons name="sparkles" size={16} color={Colors.primary} />
          </View>
          <View>
            <Text style={styles.headerTitle}>JARVIS</Text>
            <Text style={styles.headerSub}>Reverse Picks intelligence</Text>
          </View>
        </View>
        <TouchableOpacity style={styles.headerAction} accessibilityLabel="JARVIS options">
          <Ionicons name="ellipsis-horizontal" size={20} color={Colors.textSecondary} />
        </TouchableOpacity>
      </View>

      <FlatList
        ref={flatRef}
        data={messages}
        keyExtractor={item => item.id}
        contentContainerStyle={styles.messageList}
        renderItem={({ item }) => (
          <View style={[styles.message, item.role === 'user' ? styles.messageUser : styles.messageAssistant]}>
            {item.role === 'assistant' && (
              <View style={styles.avatarDot}>
                <Ionicons name="sparkles" size={11} color={Colors.primary} />
              </View>
            )}
            <View style={styles.messageContent}>
              <Text style={[styles.messageText, item.role === 'user' && styles.messageTextUser]}>
                {item.text}
              </Text>
              {!!item.tools?.length && (
                <Text style={styles.toolText}>
                  {item.tools.map((tool: string) => tool.replace(':available', ' ✓').replace(':partial', ' …').replace(':UNKNOWN', ' ?')).join('  ')}
                </Text>
              )}
            </View>
          </View>
        )}
        ListFooterComponent={loading ? (
          <View style={styles.typingRow}>
            <View style={styles.avatarDot}>
              <Ionicons name="sparkles" size={11} color={Colors.primary} />
            </View>
            <ActivityIndicator color={Colors.primary} size="small" />
          </View>
        ) : null}
        scrollEnabled={messages.length > 0}
      />

      <View style={[styles.composerArea, { paddingBottom: bottomPad + 10 }]}>
        <View style={styles.inputRow}>
          <TouchableOpacity style={styles.composerIcon} accessibilityLabel="Add attachment">
            <Ionicons name="add" size={24} color={Colors.textSecondary} />
          </TouchableOpacity>
        <TextInput
          style={styles.input}
          placeholder="Message JARVIS"
          placeholderTextColor={Colors.textSecondary}
          value={input}
          onChangeText={setInput}
          multiline
          maxLength={500}
          returnKeyType="send"
          onSubmitEditing={send}
        />
        {!input.trim() && (
          <TouchableOpacity style={styles.composerIcon} accessibilityLabel="Voice input">
            <Ionicons name="mic-outline" size={21} color={Colors.textSecondary} />
          </TouchableOpacity>
        )}
        <TouchableOpacity
          style={[styles.sendBtn, (!input.trim() || loading) && styles.sendBtnDisabled]}
          onPress={send}
          disabled={!input.trim() || loading}
        >
          <Ionicons name="arrow-up" size={18} color={input.trim() && !loading ? '#000' : Colors.textTertiary} />
        </TouchableOpacity>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: {
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
  },
  headerIdentity: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  headerMark: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Colors.primaryDim,
  },
  headerTitle: { fontSize: 18, fontWeight: '800', color: Colors.text, letterSpacing: 0.4 },
  headerSub: { fontSize: 12, color: Colors.textSecondary, marginTop: 1 },
  headerAction: { padding: 8 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  messageList: { paddingHorizontal: 18, paddingTop: 20, paddingBottom: 18 },
  message: {
    width: '100%',
    marginBottom: 22,
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  messageUser: { alignSelf: 'flex-end', flexDirection: 'row-reverse', width: 'auto', maxWidth: '86%' },
  messageAssistant: { alignSelf: 'stretch' },
  avatarDot: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: Colors.primaryDim,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 4,
    flexShrink: 0,
  },
  messageText: {
    flexShrink: 1,
    color: Colors.text,
    fontSize: 16,
    lineHeight: 24,
  },
  messageTextUser: {
    backgroundColor: Colors.primary,
    color: '#000',
    paddingHorizontal: 16,
    paddingVertical: 11,
    borderRadius: 20,
    fontWeight: '500',
  },
  messageContent: { flex: 1, minWidth: 0 },
  toolText: {
    color: Colors.textTertiary,
    fontSize: 11,
    marginTop: 7,
    letterSpacing: 0.2,
  },
  typingRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 2, marginBottom: 8 },
  composerArea: {
    paddingHorizontal: 12,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: Colors.borderSubtle,
    backgroundColor: Colors.background,
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    minHeight: 52,
    paddingHorizontal: 8,
    borderRadius: 28,
    backgroundColor: Colors.card,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  composerIcon: { width: 34, height: 38, alignItems: 'center', justifyContent: 'center' },
  input: {
    flex: 1,
    paddingHorizontal: 6,
    paddingVertical: 9,
    color: Colors.text,
    fontSize: 16,
    maxHeight: 100,
  },
  sendBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: Colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendBtnDisabled: { backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.border },
});
