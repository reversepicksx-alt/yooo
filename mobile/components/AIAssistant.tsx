import React, { useState, useRef, useEffect } from 'react';
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
  ActivityIndicator
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

interface AIAssistantProps {
  visible: boolean;
  onClose: () => void;
}

const SUGGESTED_PROMPTS = [
  "Why did my last pick miss?",
  "Best league today?",
  "Any strong under picks?"
];

export default function AIAssistant({ visible, onClose }: AIAssistantProps) {
  const insets = useSafeAreaInsets();
  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      role: 'assistant',
      content: 'Hello! I am your betting AI assistant. How can I help you today?'
    }
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const scrollViewRef = useRef<ScrollView>(null);

  useEffect(() => {
    if (visible) {
      // Potentially reset state when opened, but for now we keep the session
    }
  }, [visible]);

  const handleSend = (text: string) => {
    if (!text.trim()) return;

    const newUserMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: text.trim()
    };

    setMessages(prev => [...prev, newUserMsg]);
    setInputValue('');
    setIsTyping(true);

    // Simulated AI typing indicator and response
    setTimeout(() => {
      const newBotMsg: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `I am an AI assistant. You asked: "${text}". I am analyzing the latest data to provide you with insights soon.`
      };
      setMessages(prev => [...prev, newBotMsg]);
      setIsTyping(false);
    }, 1500);
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView 
        style={styles.backdrop} 
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={[styles.sheet, { paddingTop: 20, paddingBottom: insets.bottom || 20 }]}>
          {/* Header */}
          <View style={styles.header}>
            <View style={styles.headerTitleContainer}>
              <Ionicons name="sparkles" size={20} color={Colors.primary} style={styles.headerIcon} />
              <View>
                <Text style={styles.headerTitle}>Betting AI</Text>
                <Text style={styles.headerSubtitle}>Online</Text>
              </View>
            </View>
            <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
              <Ionicons name="close" size={20} color={Colors.text} />
            </TouchableOpacity>
          </View>

          {/* Chat Area */}
          <ScrollView 
            ref={scrollViewRef}
            style={styles.chatArea} 
            contentContainerStyle={styles.chatContent}
            onContentSizeChange={() => scrollViewRef.current?.scrollToEnd({ animated: true })}
            keyboardShouldPersistTaps="handled"
          >
            {messages.map((msg) => (
              <View 
                key={msg.id} 
                style={[
                  styles.messageBubble, 
                  msg.role === 'user' ? styles.messageUser : styles.messageAssistant
                ]}
              >
                {msg.role === 'assistant' && (
                  <View style={styles.botAvatar}>
                    <Ionicons name="hardware-chip-outline" size={16} color={Colors.primary} />
                  </View>
                )}
                <View style={[
                  styles.messageContent,
                  msg.role === 'user' ? styles.messageContentUser : styles.messageContentAssistant
                ]}>
                  <Text style={msg.role === 'user' ? styles.messageTextUser : styles.messageTextAssistant}>
                    {msg.content}
                  </Text>
                </View>
              </View>
            ))}

            {isTyping && (
              <View style={[styles.messageBubble, styles.messageAssistant]}>
                <View style={styles.botAvatar}>
                  <Ionicons name="hardware-chip-outline" size={16} color={Colors.primary} />
                </View>
                <View style={[styles.messageContent, styles.messageContentAssistant, styles.typingIndicator]}>
                  <ActivityIndicator size="small" color={Colors.primary} />
                </View>
              </View>
            )}

            {/* Suggested Prompts */}
            {messages.length === 1 && !isTyping && (
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
            />
            <TouchableOpacity 
              onPress={() => handleSend(inputValue)} 
              disabled={!inputValue.trim() || isTyping}
              style={[styles.sendBtn, (!inputValue.trim() || isTyping) && styles.sendBtnDisabled]}
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
    backgroundColor: 'rgba(0,0,0,0.6)',
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
    marginRight: 10,
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
  closeBtn: {
    padding: 8,
    backgroundColor: Colors.cardSecondary,
    borderRadius: 20,
  },
  chatArea: {
    flex: 1,
  },
  chatContent: {
    padding: 16,
    gap: 16,
  },
  messageBubble: {
    flexDirection: 'row',
    maxWidth: '85%',
    alignItems: 'flex-end',
    marginBottom: 4,
  },
  messageUser: {
    alignSelf: 'flex-end',
  },
  messageAssistant: {
    alignSelf: 'flex-start',
  },
  botAvatar: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: Colors.primaryDim,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 8,
  },
  messageContent: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 20,
  },
  messageContentUser: {
    backgroundColor: Colors.primary,
    borderBottomRightRadius: 4,
  },
  messageContentAssistant: {
    backgroundColor: Colors.cardSecondary,
    borderBottomLeftRadius: 4,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  typingIndicator: {
    paddingVertical: 14,
    paddingHorizontal: 20,
  },
  messageTextUser: {
    color: Colors.background,
    fontSize: 15,
    lineHeight: 22,
    fontWeight: '600',
  },
  messageTextAssistant: {
    color: Colors.text,
    fontSize: 15,
    lineHeight: 22,
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
    opacity: 0.5,
  }
});
