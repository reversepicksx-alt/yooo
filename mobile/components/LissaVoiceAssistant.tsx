import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Speech from 'expo-speech';
import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from 'expo-speech-recognition';
import Colors from '@/constants/colors';
import { LissaContext, sendLissaMessage } from '@/lib/api';

type Props = {
  email?: string;
  token?: string;
  sessionId?: string;
  context?: LissaContext;
  compact?: boolean;
  autoStart?: boolean;
  onAnswer?: (text: string) => void;
};

const WAKE_WORD = /\blissa\b/i;

function stripWakeWord(text: string): string {
  return text.replace(WAKE_WORD, '').replace(/^[,.:;!?-\s]+/, '').trim();
}

export default function LissaVoiceAssistant({
  email,
  token,
  sessionId = 'voice',
  context,
  compact = false,
  autoStart = true,
  onAnswer,
}: Props) {
  const [armed, setArmed] = useState(false);
  const [listening, setListening] = useState(false);
  const [awaitingQuestion, setAwaitingQuestion] = useState(false);
  const [busy, setBusy] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [answer, setAnswer] = useState('');
  const [error, setError] = useState('');
  const armedRef = useRef(false);
  const awaitingRef = useRef(false);
  const busyRef = useRef(false);
  const recognitionActiveRef = useRef(false);
  const startingRef = useRef(false);
  const lastFinalRef = useRef({ text: '', at: 0 });
  const restartTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const contextRef = useRef(context);
  const sendQuestionRef = useRef<(text: string) => Promise<void>>(async () => undefined);

  useEffect(() => {
    contextRef.current = context;
  }, [context]);

  const supported = useMemo(() => {
    try {
      return ExpoSpeechRecognitionModule.isRecognitionAvailable();
    } catch {
      return false;
    }
  }, []);

  const clearRestartTimer = () => {
    if (restartTimer.current) {
      clearTimeout(restartTimer.current);
      restartTimer.current = null;
    }
  };

  const scheduleRestart = (delay = 350) => {
    clearRestartTimer();
    if (!armedRef.current || busyRef.current) return;
    restartTimer.current = setTimeout(() => {
      restartTimer.current = null;
      void startRecognition();
    }, delay);
  };

  const startRecognition = async () => {
    if (!armedRef.current || busyRef.current || startingRef.current || recognitionActiveRef.current) return;
    startingRef.current = true;
    try {
      const state = await ExpoSpeechRecognitionModule.getStateAsync().catch(() => 'inactive');
      if (!armedRef.current || busyRef.current) return;
      if (state === 'recognizing' || state === 'starting') {
        recognitionActiveRef.current = true;
        setListening(true);
        return;
      }
      if (state === 'stopping') {
        scheduleRestart(600);
        return;
      }
      ExpoSpeechRecognitionModule.start({
        lang: 'en-US',
        interimResults: true,
        continuous: true,
        maxAlternatives: 1,
        addsPunctuation: true,
        contextualStrings: ['Lissa', 'Reverse Picks', 'pass attempts', 'key passes'],
      });
    } catch (err) {
      recognitionActiveRef.current = false;
      setListening(false);
      if (armedRef.current) {
        const message = err instanceof Error ? err.message : 'Voice recognition paused.';
        setError(`Voice is reconnecting… ${message}`);
        scheduleRestart(900);
      }
    } finally {
      startingRef.current = false;
    }
  };

  const stopListening = (disarm = true) => {
    clearRestartTimer();
    setError('');
    try {
      ExpoSpeechRecognitionModule.abort();
    } catch {}
    recognitionActiveRef.current = false;
    setListening(false);
    if (disarm) {
      armedRef.current = false;
      setArmed(false);
      awaitingRef.current = false;
      setAwaitingQuestion(false);
    }
  };

  const activate = async () => {
    setError('');
    try {
      const permission = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
      if (!permission.granted) {
        armedRef.current = false;
        setArmed(false);
        setError('Microphone and speech permission are required for Lissa voice mode.');
        return;
      }
      armedRef.current = true;
      setArmed(true);
      setAwaitingQuestion(false);
      awaitingRef.current = false;
      await startRecognition();
    } catch (err) {
      armedRef.current = false;
      setArmed(false);
      setError(err instanceof Error ? err.message : 'Voice mode could not start.');
    }
  };

  useEffect(() => {
    if (!autoStart || !supported || !email || !token) return;
    let cancelled = false;
    const boot = async () => {
      try {
        const permission = await ExpoSpeechRecognitionModule.getPermissionsAsync();
        if (cancelled) return;
        // Browsers require a user gesture before requesting microphone access.
        // Native builds can ask on entry, so Lissa becomes hands-free after the
        // first permission grant without making the web preview throw a false
        // "permission denied" error on every screen visit.
        if (permission.granted || Platform.OS !== 'web') {
          await activate();
        }
      } catch {
        if (!cancelled && Platform.OS !== 'web') {
          setError('Voice mode is reconnecting. Tap Activate Lissa if needed.');
        }
      }
    };
    void boot();
    return () => {
      cancelled = true;
    };
  }, [autoStart, supported, email, token]);

  const submitQuestion = async (question: string) => {
    const clean = question.trim();
    if (!clean || busyRef.current || !email || !token) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    setTranscript(clean);
    setAwaitingQuestion(false);
    awaitingRef.current = false;
    // Do not let the recognizer capture Lissa's spoken answer as the next
    // question. It will be restarted after Speech.speak completes.
    stopListening(false);
    try {
      const result = await sendLissaMessage(email, token, sessionId, clean, contextRef.current);
      const text = result.response || 'I could not produce a safe answer for that analysis.';
      setAnswer(text);
      onAnswer?.(text);
      try {
        await Speech.stop();
        await Speech.speak(text, { language: 'en-US', rate: 0.96, pitch: 1.0 });
      } catch {}
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Lissa is temporarily unavailable.');
    } finally {
      busyRef.current = false;
      setBusy(false);
      if (armedRef.current) {
        scheduleRestart(500);
      }
    }
  };

  sendQuestionRef.current = submitQuestion;

  useSpeechRecognitionEvent('start', () => {
    recognitionActiveRef.current = true;
    setListening(true);
    setError('');
  });
  useSpeechRecognitionEvent('end', () => {
    recognitionActiveRef.current = false;
    setListening(false);
    scheduleRestart(350);
  });
  useSpeechRecognitionEvent('result', (event) => {
    const text = event.results?.[0]?.transcript?.trim() || '';
    if (!text) return;
    setTranscript(text);
    if (!event.isFinal || !armedRef.current || busyRef.current) return;
    const now = Date.now();
    if (text === lastFinalRef.current.text && now - lastFinalRef.current.at < 1500) return;
    lastFinalRef.current = { text, at: now };

    if (awaitingRef.current) {
      const followUp = stripWakeWord(text);
      if (followUp) void sendQuestionRef.current(followUp);
      return;
    }

    if (WAKE_WORD.test(text)) {
      const command = stripWakeWord(text);
      if (command) {
        void sendQuestionRef.current(command);
      } else {
        awaitingRef.current = true;
        setAwaitingQuestion(true);
      }
    }
  });
  useSpeechRecognitionEvent('error', (event) => {
    recognitionActiveRef.current = false;
    setListening(false);
    if (event.error === 'aborted') return;
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
      armedRef.current = false;
      setArmed(false);
      setError('Voice permission is unavailable. Tap Activate Lissa to try again.');
      return;
    }
    if (armedRef.current) {
      const transient = ['busy', 'no-speech', 'speech-timeout', 'network', 'interrupted', 'audio-capture'].includes(event.error);
      setError(transient ? 'Voice is reconnecting…' : (event.message || `Speech recognition error: ${event.error}`));
      scheduleRestart(event.error === 'busy' ? 700 : 900);
    }
  });

  useEffect(() => () => {
    clearRestartTimer();
    try {
      ExpoSpeechRecognitionModule.abort();
    } catch {}
    Speech.stop().catch(() => undefined);
  }, []);

  if (!supported) {
    return (
      <View style={[styles.unavailable, compact && styles.compactUnavailable]}>
        <Ionicons name="mic-off-outline" size={15} color={Colors.textTertiary} />
        <Text style={styles.unavailableText}>Voice recognition is not available in this build.</Text>
      </View>
    );
  }

  return (
    <View style={[styles.wrap, compact && styles.compactWrap]}>
      <View style={styles.controlRow}>
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={armed ? 'Turn off Lissa wake mode' : 'Activate Lissa wake mode'}
          style={[styles.voiceButton, armed && styles.voiceButtonActive]}
          onPress={() => (armed ? stopListening() : activate())}
          disabled={busy}
        >
          <Ionicons name={armed ? 'mic' : 'mic-outline'} size={16} color={armed ? '#06110d' : Colors.primary} />
          <Text style={[styles.voiceButtonText, armed && styles.voiceButtonTextActive]}>
            {armed ? (listening ? 'Listening for “Lissa”' : 'Reconnecting…') : 'Activate Lissa'}
          </Text>
        </TouchableOpacity>
        {armed && (
          <TouchableOpacity onPress={() => stopListening()} style={styles.stopButton}>
            <Ionicons name="stop-circle-outline" size={17} color={Colors.textSecondary} />
          </TouchableOpacity>
        )}
        {busy && <ActivityIndicator size="small" color={Colors.primary} />}
      </View>
      {armed && (
        <Text style={styles.statusText}>
          {awaitingQuestion ? 'I’m listening. Ask your question now.' : 'Always listening for “Lissa”. Ask about the screen you are viewing.'}
        </Text>
      )}
      {!!transcript && !compact && <Text style={styles.transcript}>Heard: “{transcript}”</Text>}
      {!!answer && !compact && <Text style={styles.answer}>{answer}</Text>}
      {!!error && <Text style={styles.error}>{error}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    backgroundColor: '#0b160d',
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: 14,
    padding: 11,
    marginHorizontal: 16,
    marginVertical: 8,
    gap: 7,
  },
  compactWrap: { marginHorizontal: 0, marginVertical: 8 },
  controlRow: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  voiceButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: 10,
    paddingHorizontal: 11,
    paddingVertical: 8,
  },
  voiceButtonActive: { backgroundColor: Colors.primary, borderColor: Colors.primary },
  voiceButtonText: { color: Colors.primary, fontSize: 12, fontWeight: '800' },
  voiceButtonTextActive: { color: '#06110d' },
  stopButton: { padding: 5 },
  statusText: { color: '#a9c7bb', fontSize: 11 },
  transcript: { color: Colors.textSecondary, fontSize: 12, fontStyle: 'italic' },
  answer: { color: Colors.text, fontSize: 13, lineHeight: 19 },
  error: { color: '#ff8b83', fontSize: 11, lineHeight: 16 },
  unavailable: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    marginHorizontal: 16,
    marginVertical: 8,
  },
  compactUnavailable: { marginHorizontal: 0 },
  unavailableText: { color: Colors.textTertiary, fontSize: 11 },
});