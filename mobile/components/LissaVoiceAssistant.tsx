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
import { LissaContext, sendLissaMessage, callLissaSpeak } from '@/lib/api';

type Props = {
  email?: string;
  token?: string;
  sessionId?: string;
  context?: LissaContext;
  compact?: boolean;
  minimal?: boolean;
  requireWakeWord?: boolean;
  autoStart?: boolean;
  onAnswer?: (text: string) => void;
};

const WAKE_WORD = /\b(?:reverse|lissa|lisa|point\s*two|point\s*2)\b/i;

function stripWakeWord(text: string): string {
  return text.replace(WAKE_WORD, '').replace(/^[,.:;!?-\s]+/, '').trim();
}

function immediateResponse(question: string): string | null {
  const normalized = question
    .toLowerCase()
    .replace(/['']/g, '')
    .replace(/[?!.,]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (
    /\b(can you hear me|do you hear me|are you there|are you listening)\b/.test(normalized)
    || /^(hello|hi|hey)( reverse| lissa| lisa)?$/.test(normalized)
  ) {
    return "Yeah, I'm here, Jossel.";
  }
  return null;
}

function addressReverse(text: string): string {
  return /\bjossel\b/i.test(text) ? text : `Jossel, ${text}`;
}

// ---------------------------------------------------------------------------
// Audio unlock helpers — iOS Safari only allows audio from non-gesture contexts
// if the audio system was first unlocked inside a user-gesture (tap) handler.
// We use an <audio> element + a locked AudioContext, both primed in primeSpeech.
// ---------------------------------------------------------------------------

// Shared HTML <audio> element — unlocked once via gesture, reused for all TTS
let _audioEl: HTMLAudioElement | null = null;
let _audioElReady = false;

// Silent 44-byte WAV used to unlock the audio element in the gesture handler
const _SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=';

/** Call once from a tap handler to unlock the audio element for later async use. */
async function unlockAudioElement(): Promise<void> {
  if (typeof document === 'undefined') return;
  try {
    if (!_audioEl) {
      _audioEl = document.createElement('audio');
      _audioEl.preload = 'auto';
    }
    _audioEl.src = _SILENT_WAV;
    await _audioEl.play().catch(() => {});
    _audioElReady = true;
  } catch {}
}

/**
 * Convert L16 PCM base64 → WAV file → play via the pre-unlocked <audio> element.
 * Works from non-gesture async contexts on iOS Safari because the element itself
 * was already unlocked during primeSpeech.
 */
function playL16AsWav(base64: string, mimeType: string): Promise<boolean> {
  if (!_audioEl || !_audioElReady) return Promise.resolve(false);
  return new Promise((resolve) => {
    try {
      const rateMatch = mimeType.match(/rate=(\d+)/i);
      const sampleRate = rateMatch ? parseInt(rateMatch[1], 10) : 24000;
      // Decode base64 PCM
      const raw = atob(base64);
      const dataLen = raw.length;
      // Build a proper WAV file header (44 bytes) around the PCM data
      const wav = new ArrayBuffer(44 + dataLen);
      const v = new DataView(wav);
      const s = (off: number, str: string) =>
        Array.from(str).forEach((c, i) => v.setUint8(off + i, c.charCodeAt(0)));
      s(0, 'RIFF'); v.setUint32(4, 36 + dataLen, true);
      s(8, 'WAVE'); s(12, 'fmt '); v.setUint32(16, 16, true);
      v.setUint16(20, 1, true);             // PCM
      v.setUint16(22, 1, true);             // mono
      v.setUint32(24, sampleRate, true);
      v.setUint32(28, sampleRate * 2, true);// byte rate
      v.setUint16(32, 2, true);             // block align
      v.setUint16(34, 16, true);            // 16-bit
      s(36, 'data'); v.setUint32(40, dataLen, true);
      const pcm = new Uint8Array(wav, 44);
      for (let i = 0; i < dataLen; i++) pcm[i] = raw.charCodeAt(i);
      const blob = new Blob([wav], { type: 'audio/wav' });
      const url = URL.createObjectURL(blob);
      const el = _audioEl!;
      const cleanup = () => URL.revokeObjectURL(url);
      el.onended = () => { cleanup(); resolve(true); };
      el.onerror = () => { cleanup(); resolve(false); };
      el.src = url;
      el.play().catch(() => { cleanup(); resolve(false); });
    } catch (e) {
      console.error('[.2 PCM→WAV]', e);
      resolve(false);
    }
  });
}

function speakAndWait(text: string, onStart?: () => void, voice?: string): Promise<boolean> {
  return new Promise((resolve) => {
    let finished = false;
    let started = false;
    const finish = (success = started) => {
      if (finished) return;
      finished = true;
      clearTimeout(timeout);
      resolve(success);
    };
    const timeout = setTimeout(() => finish(false), Math.min(20_000, Math.max(1_500, text.length * 95)));
    Speech.speak(text, {
      language: 'en-US',
      rate: 1.08,
      pitch: 1.0,
      ...(voice ? { voice } : {}),
      onStart: () => {
        started = true;
        onStart?.();
      },
      onDone: () => finish(true),
      onStopped: () => finish(started),
      onError: () => finish(false),
    });
  });
}

/**
 * Try Gemini TTS first (human voice, plays via pre-unlocked <audio> element so
 * iOS Safari never blocks it); fall back to browser TTS on failure or native.
 */
async function speakWithAI(
  text: string,
  email: string,
  token: string,
  onStart?: () => void,
  fallbackVoice?: string,
): Promise<boolean> {
  // Only use Gemini TTS path when the audio element is already unlocked by a gesture.
  // Never call getSharedAudioCtx() here — creating a context outside a gesture = suspended.
  if (Platform.OS === 'web' && _audioElReady) {
    try {
      const tts = await Promise.race([
        callLissaSpeak(email, token, text, 'Kore'),
        new Promise<null>((res) => setTimeout(() => res(null), 10_000)),
      ]);
      if (tts?.audio) {
        onStart?.();
        const mimeType = tts.mimeType || 'audio/L16;rate=24000';
        const isPcm = mimeType.toLowerCase().includes('l16') || mimeType.toLowerCase().includes('pcm');
        const ok = isPcm
          ? await playL16AsWav(tts.audio, mimeType)
          : await new Promise<boolean>((res) => {
              // For MP3 or other native formats, also use the unlocked element
              const el = _audioEl!;
              const url = `data:${mimeType};base64,${tts.audio}`;
              el.onended = () => res(true);
              el.onerror = () => res(false);
              el.src = url;
              el.play().catch(() => res(false));
            });
        if (ok) return true;
        console.warn('[.2] Gemini TTS audio failed to play — falling back');
      } else {
        console.warn('[.2] Gemini TTS returned no audio');
      }
    } catch (e) {
      console.error('[.2] speakWithAI error:', e);
    }
  }
  // Browser TTS fallback (may be blocked on iOS Safari from non-gesture contexts,
  // but works on desktop and native builds)
  return speakAndWait(text, onStart, fallbackVoice);
}

export default function LissaVoiceAssistant({
  email,
  token,
  sessionId = 'voice',
  context,
  compact = false,
  minimal = false,
  requireWakeWord = true,
  autoStart = true,
  onAnswer,
}: Props) {
  const [armed, setArmed] = useState(false);
  const [listening, setListening] = useState(false);
  const [awaitingQuestion, setAwaitingQuestion] = useState(false);
  const [busy, setBusy] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [answer, setAnswer] = useState('');
  const [speechReady, setSpeechReady] = useState(false);
  const [error, setError] = useState('');
  const [voiceMode, setVoiceMode] = useState<'wake' | 'awaiting' | 'thinking' | 'speaking'>('wake');
  const armedRef = useRef(false);
  const awaitingRef = useRef(false);
  const busyRef = useRef(false);
  const recognitionActiveRef = useRef(false);
  const startingRef = useRef(false);
  const lastFinalRef = useRef({ text: '', at: 0 });
  const restartTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const questionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const contextRef = useRef(context);
  const voiceRef = useRef<string | undefined>(undefined);
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

  useEffect(() => {
    void Speech.getAvailableVoicesAsync().then((voices) => {
      const english = voices.filter((voice) => String(voice.language || '').toLowerCase().startsWith('en'));
      const female = english.find((voice) => String((voice as any).gender || '').toLowerCase() === 'female');
      const named = english.find((voice) =>
        /samantha|karen|ava|victoria|zira|jenny|aria|female|siri/i.test(String((voice as any).name || '')),
      );
      voiceRef.current = (female || named || english[0])?.identifier;
    }).catch(() => undefined);
  }, []);

  const clearRestartTimer = () => {
    if (restartTimer.current) {
      clearTimeout(restartTimer.current);
      restartTimer.current = null;
    }
  };

  const clearQuestionTimer = () => {
    if (questionTimer.current) {
      clearTimeout(questionTimer.current);
      questionTimer.current = null;
    }
  };

  const closeQuestionWindow = () => {
    clearQuestionTimer();
    awaitingRef.current = false;
    setAwaitingQuestion(false);
    if (armedRef.current && !busyRef.current) setVoiceMode('wake');
  };

  const openQuestionWindow = () => {
    clearQuestionTimer();
    awaitingRef.current = true;
    setAwaitingQuestion(true);
    setVoiceMode('awaiting');
    // A missed follow-up must not leave Lissa in a mode where unrelated
    // speech is treated as a question minutes later.
    questionTimer.current = setTimeout(() => {
      questionTimer.current = null;
      closeQuestionWindow();
    }, 6500);
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
        contextualStrings: ['Reverse', 'Lissa', 'Lisa', 'point two', 'Jossel', 'Reverse Picks', 'pass attempts', 'key passes'],
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
      closeQuestionWindow();
    }
  };

  const activate = async (primeVoice = Platform.OS !== 'web') => {
    setError('');
    try {
      if (primeVoice) await primeSpeech();
      const permission = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
      if (!permission.granted) {
        armedRef.current = false;
        setArmed(false);
        setError('Microphone and speech permission are required for .2 voice mode.');
        return;
      }
      armedRef.current = true;
      setArmed(true);
      closeQuestionWindow();
      setVoiceMode('wake');
      await startRecognition();
    } catch (err) {
      armedRef.current = false;
      setArmed(false);
      setError(err instanceof Error ? err.message : 'Voice mode could not start.');
    }
  };

  const primeSpeech = async () => {
    // iOS Safari requires speechSynthesis.speak() AND AudioContext.resume() to be
    // called directly inside a user-gesture handler. We do both here on tap so
    // every subsequent TTS call (browser or Gemini PCM) works without restriction.
    if (!speechReady) {
      // A single period at high speed is barely audible but guaranteed to be
      // processed as a phoneme — unlike '\u200B' which Safari may silently ignore.
      await speakAndWait('.', () => setSpeechReady(true), voiceRef.current);
    }
    // Unlock the <audio> element for Gemini TTS — must happen inside this tap handler.
    if (Platform.OS === 'web') await unlockAudioElement();
    setSpeechReady(true);
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
          setError('Voice mode is reconnecting. Tap Activate .2 if needed.');
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
    setVoiceMode('thinking');
    setError('');
    setTranscript(clean);
    closeQuestionWindow();
    // Do not let the recognizer capture Lissa's spoken answer as the next
    // question. It will be restarted after Speech.speak completes.
    stopListening(false);
    try {
      const immediate = immediateResponse(clean);
      const result = immediate
        ? null
        : await sendLissaMessage(email, token, sessionId, clean, contextRef.current);
      const text = addressReverse(immediate || result?.response || 'I could not produce a safe answer for that analysis.');
      setAnswer(text);
      onAnswer?.(text);
      try {
        await Speech.stop();
        setVoiceMode('speaking');
        const spoke = await speakWithAI(text, email ?? '', token ?? '', () => setSpeechReady(true), voiceRef.current);
        if (!spoke) setSpeechReady(false);
      } catch {}
    } catch (err) {
      const isTimeout = err instanceof Error && err.message.toLowerCase().includes('timed out');
      const errText = isTimeout
        ? "I took too long to respond. Try again."
        : '.2 is temporarily unavailable. Try again in a moment.';
      setError(errText);
      // Speak the error so the user hears what happened, not just sees it
      try {
        await Speech.stop();
        setVoiceMode('speaking');
        await speakWithAI(addressReverse(errText), email ?? '', token ?? '', () => setSpeechReady(true), voiceRef.current);
      } catch {}
    } finally {
      busyRef.current = false;
      setBusy(false);
      if (armedRef.current) {
        setVoiceMode('wake');
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
    // expo-speech-recognition can return a growing result list in continuous
    // mode. The last result is the current utterance; index 0 may be an old
    // wake-word fragment and can make Lissa answer a question never asked.
    const text = event.results?.[event.results.length - 1]?.transcript?.trim() || '';
    if (!text) return;
    if (!event.isFinal) {
      if (awaitingRef.current) setTranscript(text);
      return;
    }
    if (!armedRef.current || busyRef.current) return;
    const now = Date.now();
    const normalizedText = text.toLowerCase().replace(/\s+/g, ' ').trim();
    if (normalizedText === lastFinalRef.current.text && now - lastFinalRef.current.at < 3000) return;
    lastFinalRef.current = { text: normalizedText, at: now };

    if (awaitingRef.current) {
      const followUp = stripWakeWord(text);
      if (followUp && followUp.split(/\s+/).filter(Boolean).length >= 2) {
        closeQuestionWindow();
        setTranscript(followUp);
        void sendQuestionRef.current(followUp);
      }
      return;
    }

    if (WAKE_WORD.test(text)) {
      const command = stripWakeWord(text);
      if (command && command.split(/\s+/).filter(Boolean).length >= 2) {
        setTranscript(command);
        void sendQuestionRef.current(command);
      } else {
        openQuestionWindow();
      }
    } else if (!requireWakeWord && text.split(/\s+/).filter(Boolean).length >= 2) {
      // Optional hands-free mode is retained for non-global callers, but the
      // authenticated global assistant requires the wake word so ambient or
      // segmented recognition never produces an unsolicited answer.
      void sendQuestionRef.current(text);
    }
  });
  useSpeechRecognitionEvent('error', (event) => {
    recognitionActiveRef.current = false;
    setListening(false);
    if (event.error === 'aborted') return;
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
      armedRef.current = false;
      setArmed(false);
      setError('Voice permission is unavailable. Tap Activate .2 to try again.');
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
    clearQuestionTimer();
    try {
      ExpoSpeechRecognitionModule.abort();
    } catch {}
    Speech.stop().catch(() => undefined);
  }, []);

  if (!supported) {
    if (minimal) {
      return (
        <View style={styles.minimalUnavailable}>
          <Ionicons name="mic-off-outline" size={14} color={Colors.textTertiary} />
          <Text style={styles.minimalText}>.2 unavailable</Text>
        </View>
      );
    }
    return (
      <View style={[styles.unavailable, compact && styles.compactUnavailable]}>
        <Ionicons name="mic-off-outline" size={15} color={Colors.textTertiary} />
        <Text style={styles.unavailableText}>Voice recognition is not available in this build.</Text>
      </View>
    );
  }

  if (minimal) {
    return (
      <View style={styles.minimalWrap}>
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={armed ? 'Enable .2 spoken answers' : 'Enable .2 microphone and spoken answers'}
          style={styles.minimalButton}
          onPress={() => {
            if (armed) void primeSpeech();
            else void activate(true);
          }}
          disabled={busy}
        >
          <View style={[styles.minimalDot, armed && listening && styles.minimalDotLive]} />
          <Ionicons name="mic" size={15} color={armed ? Colors.primary : Colors.textSecondary} />
          <Text style={styles.minimalName}>.2</Text>
          <Text style={styles.minimalStatus}>
          {busy ? (voiceMode === 'thinking' ? 'thinking…' : 'speaking…') : armed && !speechReady ? 'tap for voice' : armed && awaitingQuestion ? 'ask now' : armed && listening ? 'say "Reverse"' : 'tap to enable'}
          </Text>
          {busy && <ActivityIndicator size="small" color={Colors.primary} />}
        </TouchableOpacity>
        {!!answer && !busy && (
          <Text style={styles.minimalAnswer} numberOfLines={2}>
            {answer}
          </Text>
        )}
        {!!error && <Text style={styles.minimalError} numberOfLines={1}>{error}</Text>}
      </View>
    );
  }

  return (
    <View style={[styles.wrap, compact && styles.compactWrap]}>
      <View style={styles.controlRow}>
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={armed ? 'Turn off .2 wake mode' : 'Activate .2 wake mode'}
          style={[styles.voiceButton, armed && styles.voiceButtonActive]}
          onPress={() => (armed ? stopListening() : activate())}
          disabled={busy}
        >
          <Ionicons name={armed ? 'mic' : 'mic-outline'} size={16} color={armed ? '#06110d' : Colors.primary} />
          <Text style={[styles.voiceButtonText, armed && styles.voiceButtonTextActive]}>
            {armed ? (listening ? 'Listening for "Reverse"' : 'Reconnecting…') : 'Activate .2'}
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
           {awaitingQuestion ? 'Go ahead — ask your question now.' : 'Say "Reverse" to activate. Won\'t answer background speech.'}
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
  minimalWrap: {
    alignItems: 'center',
    maxWidth: '92%',
  },
  minimalButton: {
    minHeight: 38,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    paddingHorizontal: 13,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: '#234d2c',
    backgroundColor: '#07140b',
    shadowColor: '#39FF14',
    shadowOpacity: 0.14,
    shadowRadius: 10,
    elevation: 8,
  },
  minimalDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: Colors.textTertiary,
  },
  minimalDotLive: {
    backgroundColor: Colors.primary,
    shadowColor: Colors.primary,
    shadowOpacity: 0.9,
    shadowRadius: 5,
  },
  minimalName: { color: Colors.text, fontSize: 13, fontWeight: '900' },
  minimalStatus: { color: Colors.textSecondary, fontSize: 10 },
  minimalError: {
    color: '#ff8b83',
    fontSize: 9,
    marginTop: 3,
    maxWidth: 260,
    textAlign: 'center',
  },
  minimalAnswer: {
    color: Colors.textSecondary,
    fontSize: 10,
    lineHeight: 14,
    maxWidth: 300,
    marginTop: 4,
    textAlign: 'center',
  },
  minimalUnavailable: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 9,
    borderRadius: 18,
    backgroundColor: '#0b0b0b',
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  minimalText: { color: Colors.textTertiary, fontSize: 10 },
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