import React, { useCallback, useEffect, useState } from 'react';
import { Platform, Text, TouchableOpacity } from 'react-native';
import * as Speech from 'expo-speech';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { callLissaSpeak } from '@/lib/api';

type SpeechSession = {
  email: string;
  token: string;
};

type Props = {
  text?: string | null;
  session?: SpeechSession | null;
  label?: string;
};

let audioElement: HTMLAudioElement | null = null;

function cleanNarration(text: string): string {
  return text
    .replace(/#{1,3}\s*/g, '')
    .replace(/\*\*/g, '')
    .replace(/^\s*[-•]\s*/gm, '')
    .replace(/\[.*?\]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 1200);
}

function pcmToWav(base64: string, sampleRate: number): ArrayBuffer {
  const raw = atob(base64);
  const pcm = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) pcm[i] = raw.charCodeAt(i);
  const wav = new ArrayBuffer(44 + pcm.length);
  const view = new DataView(wav);
  const write = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i += 1) {
      view.setUint8(offset + i, value.charCodeAt(i));
    }
  };
  write(0, 'RIFF');
  view.setUint32(4, 36 + pcm.length, true);
  write(8, 'WAVE');
  write(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
  view.setUint32(40, pcm.length, true);
  new Uint8Array(wav, 44).set(pcm);
  return wav;
}

function stopAnySpeech() {
  void Speech.stop();
  if (typeof speechSynthesis !== 'undefined') {
    try {
      speechSynthesis.cancel();
    } catch {
      // Browser speech is an optional fallback.
    }
  }
  if (audioElement) {
    audioElement.pause();
    audioElement.onended = null;
    audioElement.onerror = null;
    audioElement.removeAttribute('src');
    audioElement.load();
  }
}

function speakWithNativeFallback(text: string): Promise<void> {
  return new Promise((resolve) => {
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      resolve();
    };
    Speech.speak(text, {
      language: 'en-US',
      rate: 1.04,
      pitch: 1,
      onDone: finish,
      onStopped: finish,
      onError: finish,
    });
    setTimeout(finish, Math.max(5000, Math.min(30000, text.length * 55)));
  });
}

function speakWithBrowserFallback(text: string): Promise<void> {
  if (typeof speechSynthesis === 'undefined' || typeof SpeechSynthesisUtterance === 'undefined') {
    return speakWithNativeFallback(text);
  }
  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'en-US';
    utterance.rate = 1.04;
    utterance.pitch = 1;
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve();
    speechSynthesis.cancel();
    speechSynthesis.speak(utterance);
  });
}

async function speakWithGemini(text: string, session: SpeechSession): Promise<boolean> {
  if (Platform.OS !== 'web' || typeof document === 'undefined') return false;
  try {
    if (!audioElement) {
      audioElement = document.createElement('audio');
      audioElement.preload = 'auto';
    }
    // Prime the element in the same user gesture as the button tap.
    audioElement.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=';
    await audioElement.play().catch(() => undefined);
    const response = await Promise.race([
      callLissaSpeak(session.email, session.token, text, 'Kore'),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), 10000)),
    ]);
    if (!response?.audio) return false;
    const mimeType = String(response.mimeType || 'audio/L16;rate=24000').toLowerCase();
    const sampleRate = Number(mimeType.match(/rate=(\d+)/)?.[1] || 24000);
    const source = mimeType.includes('l16') || mimeType.includes('pcm')
      ? URL.createObjectURL(new Blob([
          pcmToWav(response.audio, sampleRate),
        ], { type: 'audio/wav' }))
      : `data:${response.mimeType || 'audio/mpeg'};base64,${response.audio}`;
    await new Promise<boolean>((resolve) => {
      const element = audioElement!;
      const cleanup = () => {
        element.onended = null;
        element.onerror = null;
        if (source.startsWith('blob:')) URL.revokeObjectURL(source);
      };
      element.onended = () => {
        cleanup();
        resolve(true);
      };
      element.onerror = () => {
        cleanup();
        resolve(false);
      };
      element.src = source;
      element.play().catch(() => {
        cleanup();
        resolve(false);
      });
    });
    return true;
  } catch {
    return false;
  }
}

export default function AnalysisSpeechButton({ text, session, label = 'Listen' }: Props) {
  const [speaking, setSpeaking] = useState(false);
  const narration = cleanNarration(String(text || ''));

  const stop = useCallback(() => {
    stopAnySpeech();
    setSpeaking(false);
  }, []);

  useEffect(() => () => {
    stopAnySpeech();
  }, []);

  const handlePress = useCallback(async () => {
    if (!narration) return;
    if (speaking) {
      stop();
      return;
    }
    stopAnySpeech();
    setSpeaking(true);
    try {
      const played = session
        ? await speakWithGemini(narration, session)
        : false;
      if (!played) {
        if (Platform.OS === 'web') {
          await speakWithBrowserFallback(narration);
        } else {
          await speakWithNativeFallback(narration);
        }
      }
    } finally {
      setSpeaking(false);
    }
  }, [narration, session, speaking, stop]);

  if (!narration) return null;

  return (
    <TouchableOpacity
      onPress={handlePress}
      activeOpacity={0.8}
      accessibilityRole="button"
      accessibilityLabel={speaking ? 'Stop analysis narration' : `Listen to ${label.toLowerCase()}`}
      style={{
        alignSelf: 'flex-start',
        flexDirection: 'row',
        alignItems: 'center',
        gap: 5,
        borderRadius: 7,
        borderWidth: 1,
        borderColor: speaking ? `${Colors.primary}88` : Colors.borderSubtle,
        backgroundColor: speaking ? `${Colors.primary}18` : Colors.cardSecondary,
        paddingHorizontal: 8,
        paddingVertical: 5,
        marginTop: 8,
      }}
    >
      <Ionicons
        name={speaking ? 'stop-circle-outline' : 'volume-medium-outline'}
        size={13}
        color={speaking ? Colors.primary : Colors.textSecondary}
      />
      <Text style={{ fontSize: 9, fontWeight: '800', color: speaking ? Colors.primary : Colors.textSecondary, letterSpacing: 0.4 }}>
        {speaking ? 'STOP' : label.toUpperCase()}
      </Text>
    </TouchableOpacity>
  );
}