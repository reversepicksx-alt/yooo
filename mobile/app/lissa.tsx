import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import { sendLissaMessage, startLissa } from '@/lib/api';
import LissaVoiceAssistant from '@/components/LissaVoiceAssistant';

type Message = {
  id: string;
  role: 'assistant' | 'user';
  text: string;
};

const starterPrompts = [
  'Show my recent performance',
  'Find my passing picks',
  'What can you do?',
];

export default function LissaScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const [sessionId, setSessionId] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const listRef = useRef<FlatList<Message>>(null);
  const topPad = Platform.OS === 'web' ? 54 : insets.top + 8;
  const bottomPad = Platform.OS === 'web' ? 20 : Math.max(insets.bottom, 12);

  useEffect(() => {
    if (!session?.email || !session.token || session.accessType?.toLowerCase() !== 'owner') {
      setInitializing(false);
      return;
    }
    startLissa(session.email, session.token)
      .then((result) => {
        setSessionId(result.sessionId);
        setMessages([{ id: 'welcome', role: 'assistant', text: result.message || 'Lissa is online.' }]);
      })
      .catch(() => {
        setMessages([{
          id: 'error',
          role: 'assistant',
          text: 'Lissa could not reach the owner ledger. Nothing was changed. Please try again.',
        }]);
      })
      .finally(() => setInitializing(false));
  }, [session?.email, session?.token, session?.accessType]);

  useEffect(() => {
    if (messages.length) {
      setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 80);
    }
  }, [messages.length]);

  const send = async (preset?: string) => {
    const text = (preset ?? input).trim();
    if (!text || loading || !session?.email || !session.token || !sessionId) return;
    setInput('');
    setMessages((current) => [...current, {
      id: `user-${Date.now()}`,
      role: 'user',
      text,
    }]);
    setLoading(true);
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    try {
      const result = await sendLissaMessage(session.email, session.token, sessionId, text);
      setMessages((current) => [...current, {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        text: result.response || 'I could not produce a safe answer for that request.',
      }]);
    } catch (error) {
      setMessages((current) => [...current, {
        id: `error-${Date.now()}`,
        role: 'assistant',
        text: error instanceof Error ? error.message : 'Lissa is temporarily unavailable. Nothing was changed.',
      }]);
    } finally {
      setLoading(false);
    }
  };

  if (initializing) {
    return (
      <View style={[styles.root, { paddingTop: topPad }]}>
        <View style={styles.header}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
            <Ionicons name="chevron-back" size={22} color={Colors.text} />
          </TouchableOpacity>
          <Text style={styles.title}>Lissa</Text>
        </View>
        <View style={styles.center}>
          <ActivityIndicator color={Colors.primary} />
        </View>
      </View>
    );
  }

  if (session?.accessType?.toLowerCase() !== 'owner') {
    return (
      <View style={[styles.root, styles.center, { paddingTop: topPad }]}>
        <Ionicons name="lock-closed-outline" size={30} color={Colors.primary} />
        <Text style={styles.lockTitle}>Owner access required</Text>
        <Text style={styles.lockCopy}>Lissa is private and is not available to subscriber accounts.</Text>
        <TouchableOpacity style={styles.secondaryButton} onPress={() => router.back()}>
          <Text style={styles.secondaryButtonText}>Go back</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={0}
    >
      <View style={[styles.header, { paddingTop: topPad }]}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
          <Ionicons name="chevron-back" size={22} color={Colors.text} />
        </TouchableOpacity>
        <View style={styles.headerCopy}>
          <View style={styles.titleRow}>
            <View style={styles.signal} />
            <Text style={styles.title}>Lissa</Text>
          </View>
          <Text style={styles.subtitle}>Owner intelligence · read-only</Text>
        </View>
        <View style={styles.privateBadge}>
          <Ionicons name="lock-closed" size={11} color={Colors.primary} />
          <Text style={styles.privateText}>PRIVATE</Text>
        </View>
      </View>

      <LissaVoiceAssistant
        email={session?.email}
        token={session?.token}
        sessionId={sessionId || 'lissa-voice'}
        onAnswer={(text) => setMessages((current) => [
          ...current,
          { id: `voice-${Date.now()}`, role: 'assistant', text },
        ])}
      />

      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.messageList}
        renderItem={({ item }) => (
          <View style={[styles.messageRow, item.role === 'user' && styles.userRow]}>
            <View style={[styles.avatar, item.role === 'user' && styles.userAvatar]}>
              <Text style={styles.avatarText}>{item.role === 'user' ? 'You' : 'L'}</Text>
            </View>
            <View style={[styles.bubble, item.role === 'user' && styles.userBubble]}>
              <Text style={[styles.messageText, item.role === 'user' && styles.userMessageText]}>
                {item.text}
              </Text>
            </View>
          </View>
        )}
        ListFooterComponent={loading ? (
          <View style={styles.messageRow}>
            <View style={styles.avatar}><Text style={styles.avatarText}>L</Text></View>
            <View style={styles.typingBubble}>
              <ActivityIndicator size="small" color={Colors.primary} />
            </View>
          </View>
        ) : null}
      />

      {messages.length <= 1 && (
        <View style={styles.promptStrip}>
          {starterPrompts.map((prompt) => (
            <TouchableOpacity key={prompt} style={styles.prompt} onPress={() => send(prompt)}>
              <Text style={styles.promptText}>{prompt}</Text>
            </TouchableOpacity>
          ))}
        </View>
      )}

      <View style={[styles.inputArea, { paddingBottom: bottomPad }]}>
        <TextInput
          style={styles.input}
          value={input}
          onChangeText={setInput}
          placeholder="Ask Lissa about your ledger…"
          placeholderTextColor={Colors.textTertiary}
          multiline
          maxLength={1200}
          onSubmitEditing={() => send()}
          blurOnSubmit={false}
        />
        <TouchableOpacity
          style={[styles.sendButton, (!input.trim() || loading) && styles.sendDisabled]}
          onPress={() => send()}
          disabled={!input.trim() || loading}
        >
          <Ionicons name="arrow-up" size={18} color={input.trim() && !loading ? '#06110d' : Colors.textTertiary} />
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 16,
    paddingBottom: 14,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
    backgroundColor: Colors.background,
  },
  backButton: { width: 34, height: 34, alignItems: 'center', justifyContent: 'center' },
  headerCopy: { flex: 1 },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  signal: { width: 8, height: 8, borderRadius: 4, backgroundColor: Colors.primary },
  title: { color: Colors.text, fontSize: 20, fontWeight: '800', letterSpacing: 0.2 },
  subtitle: { color: Colors.textSecondary, fontSize: 11, marginTop: 2 },
  privateBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  privateText: { color: Colors.primary, fontSize: 9, fontWeight: '800', letterSpacing: 0.6 },
  messageList: { padding: 16, paddingBottom: 12, gap: 14 },
  messageRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 9 },
  userRow: { flexDirection: 'row-reverse' },
  avatar: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: Colors.primaryDim,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: Colors.border,
  },
  userAvatar: { backgroundColor: Colors.primary, borderColor: Colors.primary },
  avatarText: { color: Colors.primary, fontSize: 10, fontWeight: '900' },
  bubble: {
    maxWidth: '84%',
    backgroundColor: Colors.card,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    borderRadius: 15,
    borderTopLeftRadius: 4,
    paddingHorizontal: 13,
    paddingVertical: 11,
  },
  userBubble: {
    backgroundColor: Colors.primary,
    borderColor: Colors.primary,
    borderTopLeftRadius: 15,
    borderTopRightRadius: 4,
  },
  messageText: { color: Colors.text, fontSize: 14, lineHeight: 21 },
  userMessageText: { color: '#06110d' },
  typingBubble: {
    width: 52,
    height: 42,
    borderRadius: 14,
    backgroundColor: Colors.card,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  promptStrip: { paddingHorizontal: 16, gap: 8, marginBottom: 8 },
  prompt: {
    alignSelf: 'flex-start',
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: 10,
    paddingHorizontal: 11,
    paddingVertical: 8,
  },
  promptText: { color: Colors.primary, fontSize: 12, fontWeight: '700' },
  inputArea: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 8,
    paddingHorizontal: 16,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: Colors.borderSubtle,
  },
  input: {
    flex: 1,
    minHeight: 44,
    maxHeight: 110,
    backgroundColor: Colors.card,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    borderRadius: 16,
    paddingHorizontal: 13,
    paddingVertical: 11,
    color: Colors.text,
    fontSize: 14,
  },
  sendButton: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: Colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendDisabled: { backgroundColor: Colors.card, borderWidth: 1, borderColor: Colors.borderSubtle },
  center: { alignItems: 'center', justifyContent: 'center', gap: 12, padding: 24 },
  lockTitle: { color: Colors.text, fontSize: 18, fontWeight: '800' },
  lockCopy: { color: Colors.textSecondary, textAlign: 'center', lineHeight: 20, maxWidth: 300 },
  secondaryButton: {
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: 10,
    paddingHorizontal: 18,
    paddingVertical: 10,
    marginTop: 6,
  },
  secondaryButtonText: { color: Colors.primary, fontWeight: '800' },
});