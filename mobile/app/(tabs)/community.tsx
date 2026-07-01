import React, { useState, useEffect, useRef, useCallback, memo } from 'react';
import {
  View, Text, FlatList, TextInput, TouchableOpacity, Image,
  KeyboardAvoidingView, Platform, StyleSheet, ActivityIndicator,
  Modal, Pressable, Alert, StatusBar, Dimensions, Keyboard, TouchableWithoutFeedback,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import * as ImagePicker from 'expo-image-picker';
import NotificationBell from '@/components/NotificationBell';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { useAuth } from '@/contexts/AuthContext';
import {
  CommunityMessage,
  fetchCommunityMessages,
  sendCommunityMessage,
  reactToCommunityMessage,
  deleteCommunityMessage,
  fetchCommunityParticipants,
  searchUsers,
} from '@/lib/api';

const { width: SW } = Dimensions.get('window');
const POLL_MS = 4000;
const REACTION_EMOJIS = ['🔥', '💯', '✅', '❌', '👀', '💪'];
const AVATAR_PALETTE = [
  '#39FF14', '#0A84FF', '#FF6B6B', '#FFD700',
  '#B14FFF', '#FF8C00', '#00CED1', '#FF2D92',
];

// ─── Types ────────────────────────────────────────────────────────────────────

type Participant = { id: string; name: string; username?: string | null };

type MessageGroup = {
  groupId: string;
  senderId: string;
  name: string;
  color: string;
  messages: CommunityMessage[];
  dayLabel: string;
};

type ListItem =
  | { type: 'date'; id: string; label: string }
  | { type: 'group'; id: string; group: MessageGroup };

// ─── Pure helpers ─────────────────────────────────────────────────────────────

function hashColor(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    h = (seed.charCodeAt(i) + ((h << 5) - h)) | 0;
  }
  return AVATAR_PALETTE[Math.abs(h) % AVATAR_PALETTE.length];
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('');
}

function getDayLabel(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const msgDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  if (msgDay.getTime() === today.getTime()) return 'Today';
  if (msgDay.getTime() === yesterday.getTime()) return 'Yesterday';
  return d.toLocaleDateString([], { month: 'long', day: 'numeric' });
}

function timeStr(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function groupMessages(msgs: CommunityMessage[]): MessageGroup[] {
  const groups: MessageGroup[] = [];
  for (const msg of msgs) {
    const last = groups[groups.length - 1];
    const lastMsg = last?.messages[last.messages.length - 1];
    const sameUser = last && last.senderId === msg.senderId;
    const sameDay = last && getDayLabel(lastMsg.createdAt) === getDayLabel(msg.createdAt);
    const within5min = last &&
      new Date(msg.createdAt).getTime() - new Date(lastMsg.createdAt).getTime() < 5 * 60 * 1000;
    if (sameUser && sameDay && within5min) {
      last.messages.push(msg);
    } else {
      groups.push({
        groupId: msg.id,
        senderId: msg.senderId,
        name: msg.name,
        color: hashColor(msg.senderId),
        messages: [msg],
        dayLabel: getDayLabel(msg.createdAt),
      });
    }
  }
  return groups;
}

function buildListItems(groups: MessageGroup[]): ListItem[] {
  const items: ListItem[] = [];
  let lastDay = '';
  for (const g of groups) {
    if (g.dayLabel !== lastDay) {
      items.push({ type: 'date', id: `date-${g.dayLabel}-${g.groupId}`, label: g.dayLabel });
      lastDay = g.dayLabel;
    }
    items.push({ type: 'group', id: g.groupId, group: g });
  }
  return items;
}

function getMentionQuery(text: string): string | null {
  const m = text.match(/@(\w*)$/);
  return m ? m[1] : null;
}

// ─── Sub-components ───────────────────────────────────────────────────────────

const AvatarCircle = memo(({ color, name, size = 36 }: { color: string; name: string; size?: number }) => (
  <View style={[
    styles.avatar,
    {
      width: size, height: size, borderRadius: size / 2,
      backgroundColor: color + '20',
      borderColor: color + '50',
    },
  ]}>
    <Text style={[styles.avatarText, { color, fontSize: size * 0.38 }]}>{initials(name)}</Text>
  </View>
));

function RenderMsgText({ text }: { text: string }) {
  const parts = text.split(/(@\w+)/g);
  return (
    <Text style={styles.msgText}>
      {parts.map((p, i) =>
        p.startsWith('@') ? (
          <Text key={i} style={styles.mention}>{p}</Text>
        ) : p
      )}
    </Text>
  );
}

const ReactionBar = memo(({
  reactions, myEmail, onReact,
}: {
  reactions: Record<string, string[]>;
  myEmail: string;
  onReact: (emoji: string) => void;
}) => {
  const entries = Object.entries(reactions).filter(([, v]) => v.length > 0);
  if (!entries.length) return null;
  return (
    <View style={styles.reactionRow}>
      {entries.map(([emoji, emails]) => (
        <TouchableOpacity
          key={emoji}
          activeOpacity={0.7}
          style={[styles.reactionPill, emails.includes(myEmail) && styles.reactionPillActive]}
          onPress={() => onReact(emoji)}
        >
          <Text style={styles.reactionEmoji}>{emoji}</Text>
          <Text style={[styles.reactionCount, emails.includes(myEmail) && styles.reactionCountActive]}>
            {emails.length}
          </Text>
        </TouchableOpacity>
      ))}
    </View>
  );
});

const MessageGroupItem = memo(({
  group, myEmail, onLongPress, onImagePress, onReact,
}: {
  group: MessageGroup;
  myEmail: string;
  onLongPress: (msg: CommunityMessage) => void;
  onImagePress: (uri: string) => void;
  onReact: (msgId: string, emoji: string) => void;
}) => {
  const isOwn = group.senderId === myEmail;
  return (
    <View style={[styles.group, isOwn && styles.groupOwn]}>
      <View style={styles.groupLeft}>
        <AvatarCircle color={group.color} name={group.name} />
      </View>
      <View style={styles.groupRight}>
        <View style={styles.groupHeader}>
          <Text style={[styles.displayName, { color: group.color }]}>
            {isOwn ? 'You' : group.name}
          </Text>
          <Text style={styles.timestamp}>{timeStr(group.messages[0].createdAt)}</Text>
        </View>
        {group.messages.map((msg) => (
          <Pressable
            key={msg.id}
            onLongPress={() => {
              Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
              onLongPress(msg);
            }}
            style={({ pressed }) => [
              styles.msgWrap,
              pressed && styles.msgWrapPressed,
              isOwn && styles.msgWrapOwn,
            ]}
          >
            {msg.text ? <RenderMsgText text={msg.text} /> : null}
            {msg.imageData ? (
              <TouchableOpacity
                activeOpacity={0.9}
                onPress={() => onImagePress(`data:image/jpeg;base64,${msg.imageData}`)}
              >
                <Image
                  source={{ uri: `data:image/jpeg;base64,${msg.imageData}` }}
                  style={styles.msgImage}
                  resizeMode="cover"
                />
              </TouchableOpacity>
            ) : null}
            <ReactionBar
              reactions={msg.reactions}
              myEmail={myEmail}
              onReact={(emoji) => onReact(msg.id, emoji)}
            />
            {msg.pending && (
              <Text style={styles.pendingLabel}>sending…</Text>
            )}
          </Pressable>
        ))}
      </View>
    </View>
  );
});

const DateDivider = memo(({ label }: { label: string }) => (
  <View style={styles.dateDivider}>
    <View style={styles.dateLine} />
    <Text style={styles.dateLabel}>{label}</Text>
    <View style={styles.dateLine} />
  </View>
));

// ─── Main screen ──────────────────────────────────────────────────────────────

export default function CommunityScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const myEmail = session?.email ?? '';

  const flatRef = useRef<FlatList>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastTsRef = useRef<string | null>(null);
  const inputRef = useRef<TextInput>(null);

  const [messages, setMessages] = useState<CommunityMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [inputText, setInputText] = useState('');
  const [sending, setSending] = useState(false);
  const [pendingImage, setPendingImage] = useState<string | null>(null);
  const [imageViewer, setImageViewer] = useState<string | null>(null);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [reactionTarget, setReactionTarget] = useState<CommunityMessage | null>(null);
  const [onlineCount, setOnlineCount] = useState(0);
  const [activeUsers, setActiveUsers] = useState<any[]>([]);
  const [showActiveUsers, setShowActiveUsers] = useState(false);
  const [mentionResults, setMentionResults] = useState<Participant[]>([]);
  const isOwner = session?.accessType?.toLowerCase() === 'owner';

  // ── Data loading ──────────────────────────────────────────────────────────

  const loadActiveUsers = useCallback(async () => {
    if (!myEmail || !isOwner) return;
    try {
      const data = await apiCall<any[]>(`/api/admin/sessions?email=${encodeURIComponent(myEmail)}`);
      if (Array.isArray(data)) {
        setActiveUsers(data);
        setOnlineCount(data.length);
      }
    } catch {
      // silently fail
    }
  }, [myEmail, isOwner]);

  const loadInitial = useCallback(async () => {
    try {
      const data = await fetchCommunityMessages({ limit: 50 });
      setMessages(data);
      if (data.length) lastTsRef.current = data[data.length - 1].createdAt;
    } catch (e) {
      console.warn('[Community] load error', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const pollNew = useCallback(async () => {
    if (!lastTsRef.current) return;
    try {
      const fresh = await fetchCommunityMessages({ since: lastTsRef.current, limit: 50 });
      if (fresh.length) {
        setMessages((prev) => {
          const existingIds = new Set(prev.map((m) => m.id));
          const newOnes = fresh.filter((m) => !existingIds.has(m.id));
          if (!newOnes.length) return prev;
          lastTsRef.current = fresh[fresh.length - 1].createdAt;
          return [...prev, ...newOnes];
        });
        setTimeout(() => flatRef.current?.scrollToEnd({ animated: true }), 80);
      }
    } catch {
      // silent — retry next tick
    }
  }, []);

  const loadParticipants = useCallback(async () => {
    try {
      const data = await fetchCommunityParticipants();
      setParticipants(data);
    } catch {
      // non-critical
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      loadInitial();
      loadParticipants();
      if (isOwner) loadActiveUsers();
      pollRef.current = setInterval(pollNew, POLL_MS);
      const t2 = isOwner ? setInterval(loadActiveUsers, 15000) : null;
      return () => {
        if (pollRef.current) clearInterval(pollRef.current);
        if (t2) clearInterval(t2);
      };
    }, [loadInitial, pollNew, loadParticipants, loadActiveUsers, isOwner]),
  );

  useEffect(() => {
    if (!loading && messages.length) {
      setTimeout(() => flatRef.current?.scrollToEnd({ animated: false }), 100);
    }
  }, [loading]);

  // ── Send ─────────────────────────────────────────────────────────────────

  const handleSend = useCallback(async () => {
    const text = inputText.trim();
    if (!text && !pendingImage) return;
    if (sending) return;

    const mentions: string[] = [];
    const hasEveryone = /@all\b/i.test(text);
    if (!hasEveryone) {
      const mentionMatches = text.match(/@\w+/g) || [];
      for (const tag of mentionMatches) {
        const name = tag.slice(1).toLowerCase();
        // Try username match from mention results first
        const fromSearch = mentionResults.find((p) => p.username?.toLowerCase() === name);
        if (fromSearch) {
          mentions.push(fromSearch.username || fromSearch.id);
          continue;
        }
        // Fallback to name / id prefix match
        const found = participants.find(
          (p) => p.name.toLowerCase().replace(/\s/g, '') === name ||
                 p.id.split('@')[0].toLowerCase() === name,
        );
        if (found) mentions.push(found.username || found.id);
      }
    }
    // @all: backend detects the text itself and broadcasts to all tokens

    const optimisticId = `pending-${Date.now()}`;
    const optimistic: CommunityMessage & { pending: boolean } = {
      id: optimisticId,
      senderId: myEmail,
      name: myEmail.split('@')[0].replace(/[._]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
      text,
      imageData: pendingImage,
      mentions,
      reactions: {},
      createdAt: new Date().toISOString(),
      pending: true,
    };

    setMessages((prev) => [...prev, optimistic]);
    setInputText('');
    setPendingImage(null);
    setMentionQuery(null);
    setSending(true);
    setTimeout(() => flatRef.current?.scrollToEnd({ animated: true }), 80);

    try {
      const real = await sendCommunityMessage({
        email: myEmail,
        text,
        imageData: pendingImage,
        mentions,
      });
      setMessages((prev) => {
        const filtered = prev.filter((m) => m.id !== optimisticId);
        lastTsRef.current = real.createdAt;
        return [...filtered, real];
      });
    } catch (err: any) {
      setMessages((prev) => prev.filter((m) => m.id !== optimisticId));
      Alert.alert('Failed to send', err?.message ?? 'Please try again.');
    } finally {
      setSending(false);
    }
  }, [inputText, pendingImage, sending, myEmail, participants, mentionResults]);

  // ── Image pick ────────────────────────────────────────────────────────────

  const handlePickImage = useCallback(async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert('Permission needed', 'Allow photo access to share images.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'] as any,
      allowsEditing: false,
      quality: 0.15,
      base64: true,
      exif: false,
    });
    if (result.canceled || !result.assets[0]?.base64) return;
    const b64 = result.assets[0].base64;
    if (b64.length > 3_500_000) {
      Alert.alert('Image too large', 'Please choose a smaller photo or screenshot.');
      return;
    }
    setPendingImage(b64);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  }, []);

  // ── React ─────────────────────────────────────────────────────────────────

  const handleReact = useCallback(async (msgId: string, emoji: string) => {
    setMessages((prev) =>
      prev.map((m) => {
        if (m.id !== msgId) return m;
        const r = { ...m.reactions };
        if (!r[emoji]) r[emoji] = [];
        if (r[emoji].includes(myEmail)) {
          r[emoji] = r[emoji].filter((e) => e !== myEmail);
          if (!r[emoji].length) delete r[emoji];
        } else {
          r[emoji] = [...r[emoji], myEmail];
        }
        return { ...m, reactions: r };
      }),
    );
    try {
      await reactToCommunityMessage(msgId, myEmail, emoji);
    } catch {
      // revert is handled naturally on next poll
    }
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  }, [myEmail]);

  // ── Long press ────────────────────────────────────────────────────────────

  const handleLongPress = useCallback((msg: CommunityMessage) => {
    setReactionTarget(msg);
  }, []);

  const handleDelete = useCallback((msg: CommunityMessage) => {
    setReactionTarget(null);
    if (Platform.OS === 'web') {
      if (!window.confirm('Delete this message?')) return;
      deleteCommunityMessage(msg.id, myEmail)
        .then(() => setMessages((prev) => prev.filter((m) => m.id !== msg.id)))
        .catch((e) => Alert.alert('Error', e?.message ?? 'Could not delete'));
    } else {
      Alert.alert('Delete message', 'This will permanently remove your message.', [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            try {
              await deleteCommunityMessage(msg.id, myEmail);
              setMessages((prev) => prev.filter((m) => m.id !== msg.id));
            } catch (e: any) {
              Alert.alert('Error', e?.message ?? 'Could not delete');
            }
          },
        },
      ]);
    }
  }, [myEmail]);

  // ── Text change / mention detection ──────────────────────────────────────

  // Mention search via API (usernames + display names)
  const mentionDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleTextChange = useCallback((text: string) => {
    setInputText(text);
    const query = getMentionQuery(text);
    setMentionQuery(query);
    if (query !== null) {
      if (mentionDebounce.current) clearTimeout(mentionDebounce.current);
      mentionDebounce.current = setTimeout(async () => {
        try {
          const res = await searchUsers(query);
          setMentionResults(
            res.map((u) => ({
              id: u.id,
              name: u.displayName || u.id.split('@')[0],
              username: u.username,
            }))
          );
        } catch {
          setMentionResults([]);
        }
      }, 280);
    } else {
      setMentionResults([]);
    }
  }, []);

  const handleMentionSelect = useCallback((participant: Participant) => {
    const tag = participant.username || participant.name.replace(/\s/g, '');
    const replaced = inputText.replace(/@(\w*)$/, `@${tag} `);
    setInputText(replaced);
    setMentionQuery(null);
    setMentionResults([]);
    inputRef.current?.focus();
  }, [inputText]);

  // ── List helpers ──────────────────────────────────────────────────────────

  const groups = groupMessages(messages);
  const listItems = buildListItems(groups);

  const filteredParticipants = mentionQuery !== null
    ? (mentionResults.length
        ? mentionResults
        : participants.filter((p) =>
            p.name.toLowerCase().includes(mentionQuery.toLowerCase()) ||
            p.id.toLowerCase().includes(mentionQuery.toLowerCase()),
          ).slice(0, 5))
    : [];

  const renderItem = useCallback(({ item }: { item: ListItem }) => {
    if (item.type === 'date') return <DateDivider label={item.label} />;
    return (
      <MessageGroupItem
        group={item.group}
        myEmail={myEmail}
        onLongPress={handleLongPress}
        onImagePress={(uri) => setImageViewer(uri)}
        onReact={handleReact}
      />
    );
  }, [myEmail, handleLongPress, handleReact]);

  // ─── Render ───────────────────────────────────────────────────────────────

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <StatusBar barStyle="light-content" />

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <Text style={styles.headerTitle}>Reverse Chat</Text>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <TouchableOpacity
            style={styles.onlineBadge}
            onPress={() => isOwner && setShowActiveUsers(true)}
            activeOpacity={isOwner ? 0.7 : 1}
            disabled={!isOwner}
          >
            <View style={styles.onlineDot} />
            <Text style={styles.onlineText}>{onlineCount} online</Text>
          </TouchableOpacity>
          <NotificationBell />
        </View>
      </View>
      <View style={styles.headerDivider} />

      {/* ── Messages ─────────────────────────────────────────────────────── */}
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        keyboardVerticalOffset={0}
      >
        {loading ? (
          <View style={styles.loadingWrap}>
            <ActivityIndicator color={Colors.primary} size="large" />
            <Text style={styles.loadingText}>Loading messages…</Text>
          </View>
        ) : messages.length === 0 ? (
          <TouchableWithoutFeedback onPress={Keyboard.dismiss}>
            <View style={styles.emptyWrap}>
              <Text style={styles.emptyIcon}>💬</Text>
              <Text style={styles.emptyTitle}>Welcome to Reverse Chat</Text>
              <Text style={styles.emptyBody}>
                Share picks, tag teammates, discuss matchups. Be the first to post.
              </Text>
            </View>
          </TouchableWithoutFeedback>
        ) : (
          <FlatList
            ref={flatRef}
            data={listItems}
            keyExtractor={(item) => item.id}
            renderItem={renderItem}
            contentContainerStyle={styles.listContent}
            showsVerticalScrollIndicator={false}
            keyboardDismissMode="interactive"
            keyboardShouldPersistTaps="handled"
            onContentSizeChange={() => {
              if (!loading) flatRef.current?.scrollToEnd({ animated: false });
            }}
          />
        )}

        {/* ── Mention picker ─────────────────────────────────────────────── */}
        {mentionQuery !== null && (filteredParticipants.length > 0 || 'all'.startsWith(mentionQuery.toLowerCase())) && (
          <View style={styles.mentionBox}>
            {'all'.startsWith(mentionQuery.toLowerCase()) && (
              <TouchableOpacity
                style={styles.mentionRow}
                activeOpacity={0.7}
                onPress={() => {
                  const replaced = inputText.replace(/@(\w*)$/, '@all ');
                  setInputText(replaced);
                  setMentionQuery(null);
                  inputRef.current?.focus();
                }}
              >
                <View style={{ width: 28, height: 28, borderRadius: 14, backgroundColor: Colors.primary, alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ color: '#000', fontSize: 13, fontWeight: '800' }}>@</Text>
                </View>
                <Text style={styles.mentionName}>Everyone</Text>
                <Text style={styles.mentionEmail}>@all · notify all members</Text>
              </TouchableOpacity>
            )}
            {filteredParticipants.map((p) => (
              <TouchableOpacity
                key={p.id}
                style={styles.mentionRow}
                activeOpacity={0.7}
                onPress={() => handleMentionSelect(p)}
              >
                <AvatarCircle color={hashColor(p.id)} name={p.name} size={28} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.mentionName}>
                    {p.username ? `@${p.username}` : p.name}
                  </Text>
                  {p.username && (
                    <Text style={[styles.mentionEmail, { fontSize: 11 }]}>{p.name}</Text>
                  )}
                </View>
                {p.username && <Text style={styles.mentionTag}>@{p.username}</Text>}
              </TouchableOpacity>
            ))}
          </View>
        )}

        {/* ── Pending image preview ──────────────────────────────────────── */}
        {pendingImage && (
          <View style={styles.pendingImgWrap}>
            <Image
              source={{ uri: `data:image/jpeg;base64,${pendingImage}` }}
              style={styles.pendingImg}
              resizeMode="cover"
            />
            <TouchableOpacity
              style={styles.pendingImgClose}
              onPress={() => setPendingImage(null)}
            >
              <Ionicons name="close-circle" size={22} color="#fff" />
            </TouchableOpacity>
          </View>
        )}

        {/* ── Input bar ─────────────────────────────────────────────────── */}
        <View style={[styles.inputBar, { paddingBottom: insets.bottom + 8 }]}>
          <TouchableOpacity
            style={styles.attachBtn}
            activeOpacity={0.7}
            onPress={handlePickImage}
          >
            <Ionicons
              name={pendingImage ? 'image' : 'image-outline'}
              size={22}
              color={pendingImage ? Colors.primary : Colors.textSecondary}
            />
          </TouchableOpacity>

          <TextInput
            ref={inputRef}
            style={[styles.input, Platform.OS === 'web' && ({ outlineWidth: 0 } as object)]}
            value={inputText}
            onChangeText={handleTextChange}
            placeholder="Message Reverse Chat…"
            placeholderTextColor={Colors.textTertiary}
            multiline
            maxLength={1200}
            returnKeyType="default"
            blurOnSubmit={false}
          />

          <TouchableOpacity
            style={[styles.sendBtn, (inputText.trim() || pendingImage) && styles.sendBtnActive]}
            activeOpacity={0.7}
            onPress={handleSend}
            disabled={sending || (!inputText.trim() && !pendingImage)}
          >
            {sending ? (
              <ActivityIndicator size="small" color={Colors.background} />
            ) : (
              <Ionicons
                name="arrow-up"
                size={18}
                color={(inputText.trim() || pendingImage) ? Colors.background : Colors.textTertiary}
              />
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>

      {/* ── Reaction / action sheet ───────────────────────────────────────── */}
      <Modal
        visible={!!reactionTarget}
        transparent
        animationType="fade"
        onRequestClose={() => setReactionTarget(null)}
      >
        <Pressable style={styles.overlay} onPress={() => setReactionTarget(null)}>
          <Pressable style={styles.actionSheet} onPress={(e) => e.stopPropagation()}>
            {reactionTarget && (
              <>
                <View style={styles.actionPreview}>
                  <Text style={styles.actionPreviewText} numberOfLines={2}>
                    {reactionTarget.text || '[image]'}
                  </Text>
                </View>
                <Text style={styles.actionSectionLabel}>React</Text>
                <View style={styles.emojiRow}>
                  {REACTION_EMOJIS.map((emoji) => (
                    <TouchableOpacity
                      key={emoji}
                      style={styles.emojiBtn}
                      activeOpacity={0.7}
                      onPress={() => {
                        if (reactionTarget) handleReact(reactionTarget.id, emoji);
                        setReactionTarget(null);
                      }}
                    >
                      <Text style={styles.emojiBtnText}>{emoji}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
                {reactionTarget.senderId === myEmail && (
                  <>
                    <View style={styles.actionDivider} />
                    <TouchableOpacity
                      style={styles.actionRow}
                      activeOpacity={0.7}
                      onPress={() => handleDelete(reactionTarget)}
                    >
                      <Ionicons name="trash-outline" size={18} color={Colors.error} />
                      <Text style={styles.actionRowDelete}>Delete message</Text>
                    </TouchableOpacity>
                  </>
                )}
                <TouchableOpacity
                  style={[styles.actionRow, { justifyContent: 'center', marginTop: 4 }]}
                  onPress={() => setReactionTarget(null)}
                >
                  <Text style={styles.actionRowCancel}>Cancel</Text>
                </TouchableOpacity>
              </>
            )}
          </Pressable>
        </Pressable>
      </Modal>

      {/* ── Full-screen image viewer ──────────────────────────────────────── */}
      <Modal
        visible={!!imageViewer}
        transparent
        animationType="fade"
        onRequestClose={() => setImageViewer(null)}
      >
        <Pressable style={styles.imgViewerOverlay} onPress={() => setImageViewer(null)}>
          {imageViewer && (
            <Image
              source={{ uri: imageViewer }}
              style={styles.imgViewerFull}
              resizeMode="contain"
            />
          )}
          <TouchableOpacity style={styles.imgViewerClose} onPress={() => setImageViewer(null)}>
            <Ionicons name="close" size={24} color="#fff" />
          </TouchableOpacity>
        </Pressable>
      </Modal>

      {/* ── Owner-only active users sheet ──────────────────────────────────── */}
      <Modal
        visible={showActiveUsers}
        transparent
        animationType="fade"
        onRequestClose={() => setShowActiveUsers(false)}
      >
        <TouchableOpacity
          style={styles.auOverlay}
          activeOpacity={1}
          onPress={() => setShowActiveUsers(false)}
        >
          <View style={styles.auSheet} onStartShouldSetResponder={() => true}>
            <Text style={styles.auTitle}>Active Users ({activeUsers.length})</Text>
            <FlatList
              data={activeUsers}
              keyExtractor={(item) => item.name}
              renderItem={({ item }) => (
                <View style={styles.auRow}>
                  <View style={styles.auAvatar}>
                    <Text style={styles.auAvatarText}>
                      {(item.name || '?')[0].toUpperCase()}
                    </Text>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.auName} numberOfLines={1}>
                      {item.name}
                    </Text>
                  </View>
                  <Text style={styles.auAccess}>{item.accessType}</Text>
                </View>
              )}
              ListEmptyComponent={(
                <Text style={{ textAlign: 'center', color: Colors.textSecondary, padding: 20 }}>
                  No active users in the last 7 days.
                </Text>
              )}
            />
          </View>
        </TouchableOpacity>
      </Modal>
    </View>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  flex: { flex: 1 },

  // Header
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 18,
    paddingVertical: 14,
  },
  headerLeft: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  headerTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: Colors.text,
    letterSpacing: -0.3,
  },
  headerDivider: {
    height: 0.5,
    backgroundColor: Colors.border,
    marginHorizontal: 0,
  },
  onlineBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    backgroundColor: Colors.primaryGlow,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 100,
    borderWidth: 0.5,
    borderColor: Colors.border,
  },
  onlineDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: Colors.primary,
  },
  onlineText: {
    fontSize: 11,
    fontWeight: '600',
    color: Colors.primary,
    letterSpacing: 0.2,
  },

  // List
  listContent: {
    paddingTop: 12,
    paddingBottom: 8,
  },

  // Loading / empty
  loadingWrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 14,
  },
  loadingText: {
    color: Colors.textTertiary,
    fontSize: 14,
    fontWeight: '500',
  },
  emptyWrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 40,
    gap: 10,
  },
  emptyIcon: {
    fontSize: 48,
    fontWeight: '800',
    color: Colors.textTertiary,
    lineHeight: 56,
  },
  emptyTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: Colors.text,
    textAlign: 'center',
  },
  emptyBody: {
    fontSize: 14,
    color: Colors.textSecondary,
    textAlign: 'center',
    lineHeight: 20,
  },

  // Date divider
  dateDivider: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 18,
    paddingVertical: 16,
    gap: 10,
  },
  dateLine: {
    flex: 1,
    height: 0.5,
    backgroundColor: Colors.borderSubtle,
  },
  dateLabel: {
    fontSize: 11,
    fontWeight: '600',
    color: Colors.textTertiary,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },

  // Message group
  group: {
    flexDirection: 'row',
    paddingHorizontal: 14,
    paddingVertical: 2,
    gap: 10,
  },
  groupOwn: {
    backgroundColor: 'rgba(57,255,20,0.025)',
  },
  groupLeft: { width: 36, paddingTop: 2 },
  groupRight: { flex: 1 },
  groupHeader: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 8,
    marginBottom: 3,
  },
  displayName: {
    fontSize: 14,
    fontWeight: '700',
    letterSpacing: -0.1,
  },
  timestamp: {
    fontSize: 10,
    color: Colors.textTertiary,
    fontWeight: '500',
  },

  // Avatar
  avatar: {
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
  },
  avatarText: {
    fontWeight: '800',
    letterSpacing: -0.5,
  },

  // Message bubble (no bubble — Discord style)
  msgWrap: {
    paddingVertical: 1,
    borderRadius: 6,
    paddingHorizontal: 4,
    marginLeft: -4,
    marginBottom: 2,
  },
  msgWrapPressed: {
    backgroundColor: 'rgba(255,255,255,0.04)',
  },
  msgWrapOwn: {
    // your messages: no extra styling — Discord keeps everything left-aligned
  },
  msgText: {
    fontSize: 15,
    color: Colors.text,
    lineHeight: 21,
    fontWeight: '400',
  },
  mention: {
    color: Colors.primary,
    fontWeight: '600',
    backgroundColor: 'rgba(57,255,20,0.12)',
    borderRadius: 3,
  },
  msgImage: {
    width: Math.min(SW - 80, 280),
    height: 180,
    borderRadius: 10,
    marginTop: 4,
    marginBottom: 2,
  },
  pendingLabel: {
    fontSize: 10,
    color: Colors.textTertiary,
    fontStyle: 'italic',
    marginTop: 2,
  },

  // Reactions
  reactionRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 5,
    marginTop: 4,
    marginBottom: 2,
  },
  reactionPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 100,
    backgroundColor: Colors.cardSecondary,
    borderWidth: 0.5,
    borderColor: Colors.borderSubtle,
  },
  reactionPillActive: {
    backgroundColor: Colors.primaryDim,
    borderColor: Colors.border,
  },
  reactionEmoji: { fontSize: 13 },
  reactionCount: {
    fontSize: 12,
    fontWeight: '600',
    color: Colors.textSecondary,
  },
  reactionCountActive: { color: Colors.primary },

  // Mention picker
  mentionBox: {
    marginHorizontal: 12,
    marginBottom: 6,
    backgroundColor: '#161616',
    borderRadius: 12,
    borderWidth: 0.5,
    borderColor: Colors.border,
    overflow: 'hidden',
  },
  mentionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderBottomWidth: 0.5,
    borderBottomColor: Colors.borderSubtle,
  },
  mentionName: {
    fontSize: 14,
    fontWeight: '700',
    color: Colors.text,
  },
  mentionEmail: {
    fontSize: 12,
    color: Colors.textTertiary,
    marginLeft: 2,
  },
  mentionTag: {
    fontSize: 13,
    fontWeight: '700',
    color: Colors.primary,
  },

  // Pending image
  pendingImgWrap: {
    marginHorizontal: 14,
    marginBottom: 8,
    alignSelf: 'flex-start',
    position: 'relative',
  },
  pendingImg: {
    width: 80,
    height: 80,
    borderRadius: 10,
    borderWidth: 1.5,
    borderColor: Colors.primary,
  },
  pendingImgClose: {
    position: 'absolute',
    top: -8,
    right: -8,
    backgroundColor: Colors.background,
    borderRadius: 12,
  },

  // Input bar
  inputBar: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    paddingHorizontal: 12,
    paddingTop: 10,
    gap: 8,
    backgroundColor: Colors.background,
    borderTopWidth: 0.5,
    borderTopColor: Colors.borderSubtle,
  },
  attachBtn: {
    width: 38,
    height: 38,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 10,
  },
  input: {
    flex: 1,
    minHeight: 38,
    maxHeight: 120,
    backgroundColor: '#1A1A1A',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingTop: Platform.OS === 'ios' ? 10 : 8,
    paddingBottom: Platform.OS === 'ios' ? 10 : 8,
    fontSize: 15,
    color: Colors.text,
    borderWidth: 0.5,
    borderColor: Colors.borderSubtle,
  },
  sendBtn: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: '#1A1A1A',
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendBtnActive: {
    backgroundColor: Colors.primary,
  },

  // Action sheet (long press)
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.75)',
    justifyContent: 'flex-end',
  },
  actionSheet: {
    backgroundColor: '#161616',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingTop: 20,
    paddingHorizontal: 20,
    paddingBottom: 36,
    borderTopWidth: 0.5,
    borderTopColor: Colors.borderSubtle,
  },
  actionPreview: {
    backgroundColor: Colors.card,
    borderRadius: 10,
    padding: 12,
    marginBottom: 18,
    borderWidth: 0.5,
    borderColor: Colors.borderSubtle,
  },
  actionPreviewText: {
    color: Colors.textSecondary,
    fontSize: 14,
    lineHeight: 20,
  },
  actionSectionLabel: {
    fontSize: 11,
    fontWeight: '700',
    color: Colors.textTertiary,
    letterSpacing: 0.8,
    textTransform: 'uppercase',
    marginBottom: 10,
  },
  emojiRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  emojiBtn: {
    width: 48,
    height: 48,
    borderRadius: 14,
    backgroundColor: Colors.card,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 0.5,
    borderColor: Colors.borderSubtle,
  },
  emojiBtnText: { fontSize: 22 },
  actionDivider: {
    height: 0.5,
    backgroundColor: Colors.borderSubtle,
    marginVertical: 14,
  },
  actionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 10,
  },
  actionRowDelete: {
    fontSize: 15,
    color: Colors.error,
    fontWeight: '600',
  },
  actionRowCancel: {
    fontSize: 15,
    color: Colors.textSecondary,
    fontWeight: '500',
  },

  // Image viewer
  imgViewerOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.96)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  imgViewerFull: {
    width: SW,
    height: SW,
  },
  imgViewerClose: {
    position: 'absolute',
    top: 56,
    right: 20,
    width: 40,
    height: 40,
    backgroundColor: 'rgba(255,255,255,0.1)',
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },

  // Active users modal
  auOverlay: {
    flex: 1, backgroundColor: Colors.overlay,
    justifyContent: 'flex-end',
  },
  auSheet: {
    backgroundColor: Colors.card,
    borderTopLeftRadius: 24, borderTopRightRadius: 24,
    padding: 24, paddingBottom: 40,
    maxHeight: '70%',
  },
  auTitle: {
    fontSize: 18, fontWeight: '800', color: Colors.text,
    marginBottom: 16,
  },
  auRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingVertical: 10, borderBottomWidth: 0.5,
    borderBottomColor: Colors.border,
  },
  auAvatar: {
    width: 36, height: 36, borderRadius: 18,
    backgroundColor: Colors.primaryDim,
    alignItems: 'center', justifyContent: 'center',
  },
  auAvatarText: { fontSize: 14, fontWeight: '700', color: Colors.primary },
  auName: { fontSize: 15, fontWeight: '600', color: Colors.text, flex: 1 },
  auEmail: { fontSize: 12, color: Colors.textTertiary },
  auAccess: {
    fontSize: 11, fontWeight: '700', color: Colors.primary,
    backgroundColor: Colors.primaryDim,
    paddingHorizontal: 8, paddingVertical: 2,
    borderRadius: 10,
  },
});
