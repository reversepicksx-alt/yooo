import React, { useState, useRef, useEffect, useCallback } from 'react';
import { 
  Modal, 
  View, 
  Text, 
  StyleSheet, 
  TouchableOpacity, 
  TextInput, 
  ScrollView, 
  KeyboardAvoidingView, 
  Platform,
  ActivityIndicator,
  Dimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { startChat, sendChatMessage } from '@/lib/api';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  loading?: boolean;
}

interface AIAssistantProps {
  visible: boolean;
  onClose: () => void;
}

const SUGGESTED_PROMPTS = [
  "Why did my last pick miss?",
  "Best league today?",
  "Any strong under picks?",
  "Who is the safest player today?",
];

const { width: SCREEN_WIDTH } = Dimensions.get('window');

export default function AIAssistant({ visible, onClose }: AIAssistantProps) {
  const insets = useSafeAreaInsets();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollViewRef = useRef<ScrollView>(null);

  const reset = useCallback(() => {
    setMessages([]);
    setInputValue('');
    setIsTyping(false);
    setSessionId(null);
    setError(null);
  }, []);

  // Start a real chat session when the modal opens
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    setIsTyping(true);
    setError(null);
    startChat()
      .then((res) => {
        if (cancelled) return;
        setSessionId(res.session_id);
        setMessages([
          {
            id: 'welcome',
            role: 'assistant',
            content: res.message || 'Hey — I\'m your betting AI. Ask me about picks, matchups, or player form.',
          }
        ]);
      })
      .catch((e) => {
        if (cancelled) return;
        setError('Could not connect to AI. Try again.');
      })
      .finally(() => {
        if (!cancelled) setIsTyping(false);
      });
    return () => {
      cancelled = true;
    };
  }, [visible]);

  const scrollToBottom = useCallback(() => {
    scrollViewRef.current?.scrollToEnd({ animated: true });
  }, []);

  const handleSend = async (text: string) => {
    if (!text.trim() || isTyping) return;
    if (!sessionId) {
      setError('AI session not ready yet. Wait a moment.');
      return;
    }

    const trimmed = text.trim();
    const userMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: trimmed,
    };
    const loadingId = (Date.now() + 1).toString();

    setMessages((prev) => [...prev, userMsg, { id: loadingId, role: 'assistant', content: '', loading: true }]);
    setInputValue('');
    setIsTyping(true);
    setError(null);

    try {
      const res = await sendChatMessage(sessionId, trimmed);
      setMessages((prev) =>
        prev.map((m) => (m.id === loadingId ? { ...m, loading: false, content: res.response || 'No response.' } : m))
      );
    } catch (e) {
      setMessages((prev) => prev.filter((m) => m.id !== loadingId));
      setError('AI response failed. Try again.');
    } finally {
      setIsTyping(false);
      setTimeout(scrollToBottom, 100);
    }
  };

  const showSuggestions = messages.length === 1 && !isTyping && !error;

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView 
        style={styles.backdrop} 
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 0 : 0}
      >
        <View style={[styles.sheet, { paddingTop: 20, paddingBottom: insets.bottom || 20 }]}>
          {/* Header */}
          <View style={styles.header}>
            <View style={styles.headerTitleContainer}>
              <View style={styles.headerIcon}>
                <Ionicons name="sparkles" size={20} color={Colors.background} />
              </View>
              <View>
                <Text style={styles.headerTitle}>Betting AI</Text>
                <Text style={styles.headerSubtitle}>Online</Text>
              </View>
            </View>
            <View style={styles.headerActions}>
              <TouchableOpacity onPress={reset} style={styles.iconBtn}>
                <Ionicons name="refresh" size={18} color={Colors.text} />
              </TouchableOpacity>
              <TouchableOpacity onPress={onClose} style={styles.iconBtn}>
                <Ionicons name="close" size={20} color={Colors.text} />
              </TouchableOpacity>
            </View>
          </View>

          {/* Chat Area */}
          <ScrollView 
            ref={scrollViewRef}
            style={styles.chatArea} 
            contentContainerStyle={styles.chatContent}
            onContentSizeChange={scrollToBottom}
            keyboardShouldPersistTaps="handled"
          >
            {messages.map((msg) => (
              <View 
                key={msg.id} 
                style={[
                  styles.bubbleRow,
                  msg.role === 'user' ? styles.bubbleRowUser : styles.bubbleRowAssistant,
                ]}
              >
                {msg.role === 'assistant' && !msg.loading && (
                  <View style={styles.botAvatar}>
                    <Ionicons name="hardware-chip-outline" size={16} color={Colors.primary} />
                  </View>
                )}
                <View 
                  style={[
                    styles.bubble,
                    msg.role === 'user' ? styles.bubbleUser : styles.bubbleAssistant,
                    msg.loading && styles.bubbleLoading,
                  ]}
                >
                  {msg.loading ? (
                    <ActivityIndicator size="small" color={Colors.primary} />
                  ) : (
                    <Text style={msg.role === 'user' ? styles.textUser : styles.textAssistant}>
                      {msg.content}
                    </Text>
                  )}
                </View>
              </View>
            ))}

            {error && (
              <View style={styles.errorBanner}>
                <Ionicons name="alert-circle" size={16} color={Colors.error} />
                <Text style={styles.errorText}>{error}</Text>
              </View>
            )}

            {/* Suggested Prompts */}
            {showSuggestions && (
              <View style={styles.suggestionsContainer}>
                {SUGGESTED_PROMPTS.map((prompt, idx) => (
                  <TouchableOpacity 
                    key={idx} 
                    style={styles.suggestionBadge}
                    onPress={() => handleSend(prompt)}
                  >
                    <Text style={styles.suggestionText}>{prompt}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            )}
          </ScrollView>

          {/* Input Area */}
          <View style={styles.inputContainer}>
            <TextInput
              style={styles.input}
              value={inputValue}
              onChangeText={setInputValue}
              placeholder="Ask anything..."
              placeholderTextColor={Colors.textTertiary}
              onSubmitEditing={() => handleSend(inputValue)}
              returnKeyType="send"
              multiline={false}
              editable={!isTyping && !!sessionId}
            />
            <TouchableOpacity 
              onPress={() => handleSend(inputValue)} 
              disabled={!inputValue.trim() || isTyping || !sessionId}
              style={[
                styles.sendBtn,
                (!inputValue.trim() || isTyping || !sessionId) && styles.sendBtnDisabled
              ]}
            >
              <Ionicons name="send" size={18} color={Colors.background} />
            </TouchableOpacity>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.75)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: Colors.background,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    height: '85%',
    display: 'flex',
    flexDirection: 'column',
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    borderBottomWidth: 0,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
  },
  headerTitleContainer: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  headerIcon: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: Colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
  },
  headerTitle: {
    color: Colors.text,
    fontSize: 18,
    fontWeight: '800',
  },
  headerSubtitle: {
    color: Colors.primary,
    fontSize: 12,
    fontWeight: '600',
    marginTop: 2,
  },
  headerActions: {
    flexDirection: 'row',
    gap: 8,
  },
  iconBtn: {
    padding: 8,
    backgroundColor: Colors.cardSecondary,
    borderRadius: 20,
  },
  chatArea: {
    flex: 1,
  },
  chatContent: {
    padding: 16,
    paddingBottom: 24,
    gap: 12,
  },
  bubbleRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    width: '100%',
  },
  bubbleRowUser: {
    justifyContent: 'flex-end',
  },
  bubbleRowAssistant: {
    justifyContent: 'flex-start',
  },
  botAvatar: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: Colors.primaryDim,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 8,
    flexShrink: 0,
  },
  bubble: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 20,
    maxWidth: SCREEN_WIDTH * 0.78,
    minWidth: 0,
  },
  bubbleUser: {
    backgroundColor: Colors.primary,
    borderBottomRightRadius: 4,
    marginLeft: 40,
  },
  bubbleAssistant: {
    backgroundColor: Colors.cardSecondary,
    borderBottomLeftRadius: 4,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  bubbleLoading: {
    paddingVertical: 16,
    paddingHorizontal: 20,
    width: 64,
    alignItems: 'center',
    justifyContent: 'center',
  },
  textUser: {
    color: Colors.background,
    fontSize: 15,
    lineHeight: 22,
    fontWeight: '600',
  },
  textAssistant: {
    color: Colors.text,
    fontSize: 15,
    lineHeight: 22,
  },
  errorBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: Colors.errorDim,
    borderRadius: 12,
    padding: 12,
    marginTop: 4,
  },
  errorText: {
    color: Colors.error,
    fontSize: 13,
    fontWeight: '600',
  },
  suggestionsContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 8,
  },
  suggestionBadge: {
    backgroundColor: Colors.card,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 20,
  },
  suggestionText: {
    color: Colors.textSecondary,
    fontSize: 13,
    fontWeight: '600',
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderTopWidth: 1,
    borderTopColor: Colors.borderSubtle,
    backgroundColor: Colors.background,
  },
  input: {
    flex: 1,
    backgroundColor: Colors.cardSecondary,
    color: Colors.text,
    fontSize: 15,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 24,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    marginRight: 12,
    maxHeight: 100,
  },
  sendBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: Colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendBtnDisabled: {
    opacity: 0.4,
  }
});
