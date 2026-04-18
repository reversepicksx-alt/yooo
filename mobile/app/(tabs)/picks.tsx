import React, { useState, useCallback } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity,
  ActivityIndicator, Alert, Platform, RefreshControl,
  Modal, ScrollView, Pressable,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import Colors from '@/constants/colors';
import { listPicks, deletePick, fetchPickAnalysis, Pick } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

type Tab = 'live' | 'history';

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Attempts', shots: 'Shots', shots_on_target: 'SOT',
  goals: 'Goals', assists: 'Assists', key_passes: 'Key Passes',
  tackles: 'Tackles', saves: 'Saves', dribbles: 'Dribbles', crosses: 'Crosses',
  interceptions: 'Interceptions', blocks: 'Blocks', fouls_drawn: 'Fouls Drawn',
  fouls_committed: 'Fouls', clearances: 'Clearances', duels_won: 'Duels Won',
  yellow_cards: 'Yellow Cards', shots_assisted: 'Shot Assists', passes: 'Passes',
};

function isLive(p: Pick) {
  return p.matchStatus === 'live' || p.status === 'live' || p.status === 'pending' || (!p.status && !['hit','miss','push','won','lost'].includes(p.result));
}
function isSettled(p: Pick) {
  return p.matchStatus === 'final' || p.status === 'settled' || ['hit','miss','push','won','lost'].includes(p.result);
}
function pickWon(p: Pick) {
  return p.result === 'hit' || p.result === 'won' || p.status === 'won';
}
function pickLost(p: Pick) {
  return p.result === 'miss' || p.result === 'lost' || p.status === 'lost';
}
function pickPush(p: Pick) {
  return p.result === 'push';
}

function PickCard({ pick, onDelete }: { pick: Pick; onDelete: () => void }) {
  const won = pickWon(pick);
  const lost = pickLost(pick);
  const live = isLive(pick);

  const isOver = pick.recommendation === 'OVER';
  const isUnder = pick.recommendation === 'UNDER';

  const recColor = isOver ? Colors.primary : isUnder ? Colors.error : Colors.textSecondary;
  const push = pickPush(pick);
  const statusColor = won ? Colors.success : lost ? Colors.error : push ? Colors.textSecondary : Colors.textTertiary;

  const propLabel = PROP_LABELS[pick.propType] || pick.propType?.replace(/_/g, ' ') || '—';
  const venueStr = pick.venue ? pick.venue.toUpperCase() : '';
  const posLabel = [pick.position, pick.role].filter(Boolean).join(' · ');

  const settled = won || lost || push;
  const nowValue = settled
    ? (pick.actualValue ?? (pick as { currentValue?: number | null }).currentValue ?? null)
    : ((pick as { currentValue?: number | null }).currentValue ?? pick.actualValue ?? null);
  const projValue = pick.projection ?? (pick as { projectedValue?: number | null }).projectedValue ?? null;
  const livePace = (pick as { pace?: number | null }).pace;
  const matchStatus = (pick as { matchStatus?: string }).matchStatus;
  const hasLiveData = matchStatus === 'live' || (livePace != null && livePace > 0) || nowValue != null;
  const paceValue = settled ? projValue : (livePace != null && livePace > 0 ? livePace : projValue);
  const hitPct = (pick as { hitPct?: number | null }).hitPct ?? (pick as { hitRate?: number | null }).hitRate ?? (pick as { winRate?: number | null }).winRate;
  const lineValue = typeof pick.line === 'number' ? pick.line : null;

  const trackValue = nowValue ?? paceValue ?? null;
  const trackDistance = lineValue != null && lineValue > 0 && trackValue != null
    ? Math.max(0, Math.min(2, trackValue / lineValue))
    : null;
  const trackFillPct = trackDistance != null
    ? Math.max(6, Math.min(94, (trackDistance / 2) * 100))
    : null;
  const trackMarkerPct = 50;
  const progressFillPct = lineValue != null && trackValue != null
    ? Math.max(0, Math.min(100, (trackValue / Math.max(lineValue * 2, 1)) * 100))
    : null;
  const trackColor = won
    ? Colors.success
    : lost
    ? Colors.error
    : trackValue != null && lineValue != null
    ? ((isOver && trackValue > lineValue) || (isUnder && trackValue < lineValue) ? Colors.success : Colors.error)
    : Colors.textSecondary;
  const paceColor = trackValue != null ? Colors.primary : Colors.textSecondary;

  const nowLabel = (won || lost || push) ? 'FINAL' : (hasLiveData ? 'NOW' : null);
  const paceLabel = settled ? 'PROJ' : (livePace != null && livePace > 0 ? 'PACE' : 'PROJ');

  return (
    <View style={[styles.card, won && styles.cardWon, lost && styles.cardLost]}>
      {/* Row 1: player name left | badge right */}
      <View style={styles.cardTopRow}>
        <Text style={styles.cardPlayer} numberOfLines={1}>{pick.playerName}</Text>
        <View style={styles.cardRight}>
          {live && !won && !lost && hasLiveData && (
            <View style={styles.liveBadge}>
              <View style={styles.liveDot} />
              <Text style={styles.liveText}>LIVE</Text>
            </View>
          )}
          {live && !won && !lost && !hasLiveData && (
            <View style={styles.pendingBadge}>
              <Ionicons name="time-outline" size={9} color={Colors.textSecondary} />
              <Text style={styles.pendingText}>PENDING</Text>
            </View>
          )}
          {won && (
            <View style={styles.wonBadge}>
              <Ionicons name="checkmark" size={9} color="#000" />
              <Text style={styles.wonText}>HIT</Text>
            </View>
          )}
          {lost && (
            <View style={styles.lostBadge}>
              <Ionicons name="close" size={9} color={Colors.error} />
              <Text style={styles.lostText}>MISS</Text>
            </View>
          )}
          {push && !won && !lost && (
            <View style={styles.pendingBadge}>
              <Ionicons name="remove-outline" size={9} color={Colors.textSecondary} />
              <Text style={styles.pendingText}>PUSH</Text>
            </View>
          )}
        </View>
      </View>

      {/* Row 2: meta + pick pill left | inline stats right */}
      <View style={styles.cardRow2}>
        <View style={styles.cardRow2Left}>
          <Text style={styles.cardMeta} numberOfLines={1}>
            {[pick.teamName, posLabel || null, pick.opponentName ? `vs ${pick.opponentName}` : null]
              .filter(Boolean).join(' · ')}
          </Text>
          {pick.recommendation && (
            <View style={styles.pickRow}>
              <View style={[styles.recPill, { backgroundColor: isOver ? Colors.successDim : isUnder ? Colors.errorDim : Colors.cardSecondary }]}>
                <Text style={[styles.recPillText, { color: recColor }]}>{pick.recommendation}</Text>
              </View>
              <Text style={styles.pickDetail} numberOfLines={1}>
                {propLabel} · {pick.line}
              </Text>
              {pick.coinFlip && (
                <View style={styles.coinFlipBadge}>
                  <Text style={styles.coinFlipText}>~</Text>
                </View>
              )}
            </View>
          )}
        </View>
        {/* Inline stats: NOW (if live/settled) and PROJ */}
        <View style={styles.inlineStats}>
          {nowLabel && nowValue != null && (
            <View style={styles.inlineStat}>
              <Text style={[styles.inlineVal, { color: trackColor }]}>{Number(nowValue).toFixed(0)}</Text>
              <Text style={styles.inlineLbl}>{nowLabel}</Text>
            </View>
          )}
          {paceValue != null && (
            <View style={styles.inlineStat}>
              <Text style={[styles.inlineVal, { color: Colors.primary }]}>{Number(paceValue).toFixed(0)}</Text>
              <Text style={styles.inlineLbl}>{paceLabel}</Text>
            </View>
          )}
          {hitPct != null && (
            <View style={styles.inlineStat}>
              <Text style={[styles.inlineVal, { color: Colors.textSecondary }]}>{Math.round(hitPct)}%</Text>
              <Text style={styles.inlineLbl}>HIT</Text>
            </View>
          )}
        </View>
      </View>

      {/* Thin progress bar — only visible when live/settled with real data */}
      {lineValue != null && nowValue != null && (() => {
        const fillPct = Math.max(0, Math.min(100, (nowValue / Math.max(lineValue * 2, 1)) * 100));
        return (
          <View style={styles.trackBarOuter}>
            <View style={[styles.trackBarFill, { width: `${fillPct}%`, backgroundColor: trackColor }]} />
            <View style={[styles.trackBarMarker, { left: `${trackMarkerPct}%` }]} />
          </View>
        );
      })()}

      <TouchableOpacity style={styles.trashBtn} onPress={onDelete} hitSlop={{ top: 14, bottom: 14, left: 14, right: 14 }} activeOpacity={0.5}>
        <Ionicons name="trash-outline" size={12} color="rgba(255,255,255,0.3)" />
      </TouchableOpacity>

      {live && !won && !lost && (
        <View style={styles.tapHint}>
          <Ionicons name="analytics-outline" size={9} color={Colors.primary} />
          <Text style={styles.tapHintText}>Tap for analysis</Text>
        </View>
      )}
    </View>
  );
}

function RecordBar({ picks }: { picks: Pick[] }) {
  const hits = picks.filter(pickWon).length;
  const misses = picks.filter(pickLost).length;
  const pending = picks.filter(isLive).length;
  const settled = hits + misses;
  const winPct = settled > 0 ? Math.round((hits / settled) * 100) : null;

  let streak = 0;
  const sorted = [...picks].sort((a, b) =>
    new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime()
  );
  for (const p of sorted) {
    if (pickWon(p)) streak++;
    else if (pickLost(p)) break;
  }

  return (
    <View style={styles.recordBar}>
      <Text style={styles.recordLabel}>YOUR RECORD</Text>
      <View style={styles.recordStats}>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.success }]}>{hits}</Text>
          <Text style={styles.recordKey}>HITS</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.error }]}>{misses}</Text>
          <Text style={styles.recordKey}>MISS</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.textSecondary }]}>{pending}</Text>
          <Text style={styles.recordKey}>LIVE</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.primary }]}>
            {winPct != null ? `${winPct}%` : '—'}
          </Text>
          <Text style={styles.recordKey}>WIN%</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.accent }]}>
            {streak > 0 ? `${streak}W` : '—'}
          </Text>
          <Text style={styles.recordKey}>STRK</Text>
        </View>
      </View>
      <View style={styles.progressWrap}>
        <View style={styles.progressTrack}>
          <View
            style={[
              styles.progressFill,
              { width: `${picks.length > 0 ? Math.max(8, Math.min(100, (settled / picks.length) * 100)) : 0}%` },
            ]}
          />
        </View>
        <Text style={styles.progressText}>
          {settled}/{picks.length} settled
        </Text>
      </View>
    </View>
  );
}

function renderAnalysisBlocks(text: string, rec: string) {
  const isOver = rec === 'OVER';
  const isUnder = rec === 'UNDER';
  const recColor = isOver ? Colors.success : isUnder ? Colors.error : Colors.textSecondary;
  const paragraphs = text.split(/\n\n+/).filter(p => p.trim());
  const blocks: React.ReactElement[] = [];
  for (let i = 0; i < paragraphs.length; i++) {
    const para = paragraphs[i];
    const m = para.match(/^\*\*([^*]+)\*\*\s*([\s\S]*)/);
    if (m) {
      const section = m[1].trim();
      const body = m[2].trim().replace(/\*\*/g, '');
      if (section === 'Analysis') continue;
      if (section === 'Verdict') {
        blocks.push(
          <View key={i} style={mStyles.aiVerdictBlock}>
            <View style={[mStyles.aiVerdictPill, { backgroundColor: isOver ? 'rgba(57,255,20,0.12)' : 'rgba(255,59,48,0.12)' }]}>
              <Text style={[mStyles.aiVerdictLabel, { color: recColor }]}>VERDICT</Text>
            </View>
            <Text style={mStyles.aiVerdictText}>{body}</Text>
          </View>
        );
        continue;
      }
      if (section === 'TL;DR') {
        blocks.push(
          <View key={i} style={mStyles.aiTldrBlock}>
            <Text style={mStyles.aiTldrText}>{body}</Text>
          </View>
        );
        continue;
      }
      blocks.push(
        <View key={i} style={mStyles.aiSection}>
          <Text style={mStyles.aiSectionTitle}>{section.toUpperCase()}</Text>
          {body ? <Text style={mStyles.aiSectionBody}>{body}</Text> : null}
        </View>
      );
    } else {
      const plain = para.replace(/\*\*/g, '').trim();
      if (plain) blocks.push(<Text key={i} style={mStyles.aiSectionBody}>{plain}</Text>);
    }
  }
  return blocks;
}

export default function PicksScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const qc = useQueryClient();
  const topPad = Platform.OS === 'web' ? 67 : insets.top;
  const [activeTab, setActiveTab] = useState<Tab>('live');
  const [analysisModal, setAnalysisModal] = useState<{ pick: Pick; data: Record<string, unknown> | null; loading: boolean } | null>(null);

  const { data: picks = [], isLoading, refetch, isRefetching, error } = useQuery({
    queryKey: ['picks', session?.email],
    queryFn: async () => {
      if (!session) return [];
      try {
        return await listPicks(session.email, session.token);
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        // If auth failed, don't silently return [] — re-throw so error state is set
        if (msg.includes('Invalid session') || msg.includes('401') || msg.includes('Unauthorized')) {
          throw new Error('SESSION_INVALID');
        }
        throw e;
      }
    },
    enabled: !!session,
    refetchInterval: 15000,
    refetchIntervalInBackground: true,
    retry: 2,
    retryDelay: 2000,
  });

  useFocusEffect(
    useCallback(() => {
      refetch();
      const timer = setInterval(() => refetch(), 15000);
      return () => clearInterval(timer);
    }, [refetch])
  );

  const deleteMutation = useMutation({
    mutationFn: (pickId: string) => {
      if (!session) throw new Error('Not authenticated');
      return deletePick(session.email, session.token, pickId);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['picks'] });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    },
    onError: (e: Error) => Alert.alert('Delete failed', e.message),
  });

  const handleDelete = useCallback((pick: Pick) => {
    const id = pick.pickId || pick._id || pick.id;
    if (!id) return;
    if (Platform.OS === 'web') {
      if (window.confirm(`Remove ${pick.playerName}?`)) {
        deleteMutation.mutate(id);
      }
    } else {
      Alert.alert('Delete Pick', `Remove ${pick.playerName}?`, [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Delete', style: 'destructive', onPress: () => deleteMutation.mutate(id) },
      ]);
    }
  }, [deleteMutation]);

  const handlePickPress = useCallback(async (pick: Pick) => {
    const id = pick.pickId || pick._id || pick.id;
    if (!id || !session) return;
    setAnalysisModal({ pick, data: null, loading: true });
    try {
      const result = await fetchPickAnalysis(session.email, session.token, id);
      setAnalysisModal({ pick, data: result.found ? (result.analysis ?? null) : null, loading: false });
    } catch {
      setAnalysisModal({ pick, data: null, loading: false });
    }
  }, [session]);

  const live = picks.filter(isLive);
  const history = picks.filter(isSettled);
  const displayed = activeTab === 'live' ? live : history;

  const modalRec = ((analysisModal?.data?.recommendation ?? analysisModal?.pick?.recommendation) as string | undefined)?.toUpperCase() ?? '';
  const modalIsOver = modalRec === 'OVER';
  const modalIsUnder = modalRec === 'UNDER';
  const modalRecColor = modalIsOver ? Colors.success : modalIsUnder ? Colors.error : Colors.textSecondary;
  const modalText = (analysisModal?.data?.reasoning ?? analysisModal?.data?.tacticalBreakdown ?? analysisModal?.data?.explanation) as string | undefined;

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.headerTitle}>My Picks</Text>
        <View style={styles.tabToggle}>
          {(['live', 'history'] as Tab[]).map(t => (
            <TouchableOpacity
              key={t}
              style={[styles.toggle, activeTab === t && styles.toggleActive]}
              onPress={() => { setActiveTab(t); Haptics.selectionAsync(); }}
            >
              {t === 'live' && live.length > 0 && activeTab !== 'live' && (
                <View style={styles.tabDot} />
              )}
              <Text style={[styles.toggleText, activeTab === t && styles.toggleTextActive]}>
                {t === 'live' ? `Live${live.length > 0 ? ` (${live.length})` : ''}` : `History${history.length > 0 ? ` (${history.length})` : ''}`}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      {/* Record bar — always show if any picks exist */}
      {picks.length > 0 && <RecordBar picks={picks} />}

      {error && (error as Error).message === 'SESSION_INVALID' ? (
        <View style={styles.center}>
          <Ionicons name="lock-closed-outline" size={44} color={Colors.textTertiary} />
          <Text style={[styles.emptyTitle, { marginTop: 12 }]}>Session expired</Text>
          <Text style={[styles.emptySub, { textAlign: 'center', marginTop: 6 }]}>
            Your session timed out. Go to Account and tap Verify Access to restore your picks.
          </Text>
          <TouchableOpacity onPress={() => refetch()} style={{ marginTop: 16 }}>
            <Text style={{ color: Colors.primary, fontWeight: '700' }}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.primary} size="large" />
        </View>
      ) : displayed.length === 0 ? (
        <View style={styles.empty}>
          <Ionicons
            name={activeTab === 'live' ? 'timer-outline' : 'archive-outline'}
            size={52}
            color={Colors.textTertiary}
          />
          <Text style={styles.emptyTitle}>
            {activeTab === 'live' ? 'No live picks' : 'No settled picks yet'}
          </Text>
          <Text style={styles.emptySub}>
            {activeTab === 'live'
              ? 'Scan a prop slip and save a prediction to track it here.'
              : 'Picks move here once their game is finished and results are confirmed.'}
          </Text>
        </View>
      ) : (
        <FlatList
          data={displayed}
          keyExtractor={(item, i) => item.pickId || item._id || item.id || String(i)}
          renderItem={({ item }) => {
            const tappable = isLive(item) && !pickWon(item) && !pickLost(item);
            if (tappable) {
              return (
                <Pressable
                  onPress={() => {
                    try { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); } catch {}
                    handlePickPress(item);
                  }}
                  style={({ pressed }) => [{ opacity: pressed ? 0.88 : 1 }]}
                >
                  <PickCard pick={item} onDelete={() => handleDelete(item)} />
                </Pressable>
              );
            }
            return <PickCard pick={item} onDelete={() => handleDelete(item)} />;
          }}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={isRefetching}
              onRefresh={refetch}
              tintColor={Colors.primary}
            />
          }
          showsVerticalScrollIndicator={false}
        />
      )}

      {/* ── Analysis Modal ── */}
      <Modal
        visible={analysisModal !== null}
        animationType="slide"
        transparent
        onRequestClose={() => setAnalysisModal(null)}
      >
        <View style={mStyles.modalContainer}>
          <Pressable style={mStyles.modalBackdrop} onPress={() => setAnalysisModal(null)} />
          <View style={mStyles.modalSheet}>
          {/* Handle */}
          <View style={mStyles.modalHandle} />

          {/* Header */}
          <View style={mStyles.modalHeader}>
            <View style={mStyles.modalPlayerInfo}>
              <Text style={mStyles.modalPlayer} numberOfLines={1}>{analysisModal?.pick.playerName}</Text>
              <Text style={mStyles.modalMeta} numberOfLines={1}>
                {[analysisModal?.pick.teamName, analysisModal?.pick.opponentName ? `vs ${analysisModal?.pick.opponentName}` : null].filter(Boolean).join(' · ')}
              </Text>
            </View>
            <View style={mStyles.modalRight}>
              {modalRec ? (
                <View style={[mStyles.modalRecBadge, { backgroundColor: modalIsOver ? Colors.successDim : modalIsUnder ? Colors.errorDim : Colors.cardSecondary }]}>
                  <Text style={[mStyles.modalRecText, { color: modalRecColor }]}>{modalRec}</Text>
                </View>
              ) : null}
              <TouchableOpacity onPress={() => setAnalysisModal(null)} style={mStyles.modalClose}>
                <Ionicons name="close" size={18} color={Colors.textSecondary} />
              </TouchableOpacity>
            </View>
          </View>

          {/* Prop row */}
          <View style={mStyles.modalPropRow}>
            <Text style={mStyles.modalPropText}>
              {PROP_LABELS[analysisModal?.pick.propType ?? ''] ?? analysisModal?.pick.propType} · Line {analysisModal?.pick.line}
            </Text>
            {analysisModal?.data?.projectedValue != null && (
              <Text style={[mStyles.modalProjText, { color: modalRecColor }]}>
                Proj {(analysisModal.data.projectedValue as number).toFixed(1)}
              </Text>
            )}
          </View>

          <View style={mStyles.modalDivider} />

          {/* Body */}
          <ScrollView style={mStyles.modalScroll} contentContainerStyle={mStyles.modalScrollContent} showsVerticalScrollIndicator={false}>
            {analysisModal?.loading ? (
              <View style={mStyles.modalLoading}>
                <ActivityIndicator color={Colors.primary} />
                <Text style={mStyles.modalLoadingText}>Loading analysis…</Text>
              </View>
            ) : !modalText ? (
              <View style={mStyles.modalLoading}>
                <Ionicons name="analytics-outline" size={32} color={Colors.textTertiary} />
                <Text style={mStyles.modalLoadingText}>No analysis found for this pick yet.</Text>
              </View>
            ) : (
              <View style={mStyles.aiBlocks}>
                {renderAnalysisBlocks(modalText, modalRec)}
              </View>
            )}
          </ScrollView>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const mStyles = StyleSheet.create({
  modalContainer: { flex: 1, justifyContent: 'flex-end' },
  modalBackdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(0,0,0,0.6)' },
  modalSheet: {
    backgroundColor: Colors.card,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    maxHeight: '82%',
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    borderBottomWidth: 0,
  },
  modalHandle: {
    width: 36, height: 4, borderRadius: 2,
    backgroundColor: Colors.border, alignSelf: 'center', marginTop: 10,
  },
  modalHeader: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    paddingHorizontal: 18, paddingTop: 14, paddingBottom: 10, gap: 10,
  },
  modalPlayerInfo: { flex: 1 },
  modalPlayer: { fontSize: 18, fontWeight: '800', color: Colors.text },
  modalMeta: { fontSize: 12, color: Colors.textSecondary, marginTop: 2 },
  modalRight: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  modalRecBadge: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 8 },
  modalRecText: { fontSize: 13, fontWeight: '800', letterSpacing: 0.5 },
  modalClose: { padding: 4 },
  modalPropRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 18, paddingBottom: 12,
  },
  modalPropText: { fontSize: 13, color: Colors.textSecondary },
  modalProjText: { fontSize: 14, fontWeight: '700' },
  modalDivider: { height: 1, backgroundColor: Colors.borderSubtle },
  modalScroll: { flex: 0 },
  modalScrollContent: { padding: 18, paddingBottom: 40 },
  modalLoading: { alignItems: 'center', paddingVertical: 40, gap: 14 },
  modalLoadingText: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center' },
  aiBlocks: { gap: 16 },
  aiVerdictBlock: {
    backgroundColor: 'rgba(57,255,20,0.06)',
    borderLeftWidth: 3, borderLeftColor: Colors.primary,
    borderRadius: 8, padding: 12, gap: 6,
  },
  aiVerdictPill: { alignSelf: 'flex-start', borderRadius: 4, paddingHorizontal: 7, paddingVertical: 2 },
  aiVerdictLabel: { fontSize: 9, fontWeight: '800', letterSpacing: 1.5 },
  aiVerdictText: { fontSize: 14, fontWeight: '600', lineHeight: 21, color: Colors.text },
  aiTldrBlock: { backgroundColor: Colors.cardSecondary, borderRadius: 8, padding: 12 },
  aiTldrText: { fontSize: 12, color: Colors.textSecondary, lineHeight: 18, fontStyle: 'italic' },
  aiSection: { gap: 5 },
  aiSectionTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.2 },
  aiSectionBody: { fontSize: 13, color: Colors.textSecondary, lineHeight: 20 },
});

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: {
    paddingHorizontal: 20, paddingBottom: 12,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
  },
  headerTitle: { fontSize: 28, fontWeight: '800', color: Colors.text },
  tabToggle: {
    flexDirection: 'row', backgroundColor: Colors.card,
    borderRadius: 10, borderWidth: 1, borderColor: Colors.border, padding: 3,
  },
  toggle: { paddingVertical: 7, paddingHorizontal: 14, borderRadius: 8, flexDirection: 'row', alignItems: 'center', gap: 5 },
  toggleActive: { backgroundColor: Colors.primary },
  toggleText: { fontSize: 13, fontWeight: '600', color: Colors.textSecondary },
  toggleTextActive: { color: '#000' },
  tabDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: Colors.primary },
  progressWrap: { gap: 6, marginTop: 2 },
  progressTrack: {
    height: 6,
    borderRadius: 999,
    backgroundColor: Colors.cardSecondary,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%' as unknown as number,
    borderRadius: 999,
    backgroundColor: Colors.primary,
  },
  progressText: { fontSize: 10, color: Colors.textTertiary, fontWeight: '600', textAlign: 'right' },

  recordBar: {
    marginHorizontal: 20, marginBottom: 12, backgroundColor: Colors.card,
    borderRadius: Colors.radius, borderWidth: 1, borderColor: Colors.border, padding: 14, gap: 8,
  },
  recordLabel: { fontSize: 10, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 1.5 },
  recordStats: { flexDirection: 'row', justifyContent: 'space-between' },
  recordStat: { alignItems: 'center', flex: 1 },
  recordVal: { fontSize: 18, fontWeight: '800', color: Colors.text },
  recordKey: { fontSize: 9, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.5, marginTop: 2 },

  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12, paddingHorizontal: 40 },
  emptyTitle: { fontSize: 18, fontWeight: '700', color: Colors.text },
  emptySub: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center', lineHeight: 21 },
  list: { paddingHorizontal: 16, paddingBottom: 40, gap: 6 },

  card: {
    backgroundColor: Colors.card, borderRadius: 12,
    paddingHorizontal: 12, paddingVertical: 9,
    borderWidth: 1, borderColor: Colors.borderSubtle, gap: 4,
  },
  cardWon: { borderColor: 'rgba(57,255,20,0.3)' },
  cardLost: { borderColor: 'rgba(255,59,48,0.25)' },

  cardTopRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  cardRight: { flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 0 },
  cardPlayer: { fontSize: 14, fontWeight: '700', color: Colors.text, flex: 1 },
  cardMeta: { fontSize: 10, color: Colors.textTertiary, letterSpacing: 0.2, marginBottom: 3 },

  cardRow2: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  cardRow2Left: { flex: 1, gap: 2 },
  inlineStats: { flexDirection: 'row', alignItems: 'center', gap: 10, flexShrink: 0 },
  inlineStat: { alignItems: 'center', gap: 1 },
  inlineVal: { fontSize: 14, fontWeight: '700', color: Colors.text },
  inlineLbl: { fontSize: 8, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.8 },

  liveBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    backgroundColor: 'rgba(255,255,255,0.06)', borderRadius: 5,
    paddingHorizontal: 6, paddingVertical: 2,
  },
  liveDot: { width: 4, height: 4, borderRadius: 2, backgroundColor: Colors.primary },
  liveText: { fontSize: 8, color: Colors.primary, fontWeight: '700', letterSpacing: 0.5 },
  pendingBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    backgroundColor: 'rgba(255,255,255,0.04)', borderRadius: 5,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
    paddingHorizontal: 6, paddingVertical: 2,
  },
  pendingText: { fontSize: 8, color: Colors.textSecondary, fontWeight: '600', letterSpacing: 0.5 },
  wonBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    backgroundColor: Colors.primary, borderRadius: 5,
    paddingHorizontal: 6, paddingVertical: 2,
  },
  wonText: { fontSize: 8, color: '#000', fontWeight: '800', letterSpacing: 0.5 },
  lostBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    backgroundColor: Colors.errorDim, borderRadius: 5,
    paddingHorizontal: 6, paddingVertical: 2,
    borderWidth: 1, borderColor: 'rgba(255,59,48,0.3)',
  },
  lostText: { fontSize: 8, color: Colors.error, fontWeight: '800', letterSpacing: 0.5 },

  pickRow: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  recPill: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4 },
  recPillText: { fontSize: 9, fontWeight: '800', letterSpacing: 0.5 },
  pickDetail: { fontSize: 11, color: Colors.textSecondary },
  coinFlipBadge: { backgroundColor: Colors.cardSecondary, paddingHorizontal: 4, paddingVertical: 1, borderRadius: 4 },
  coinFlipText: { fontSize: 8, fontWeight: '800', color: Colors.textTertiary },

  trackBarOuter: {
    height: 2,
    backgroundColor: Colors.cardSecondary,
    borderRadius: 1,
    overflow: 'hidden',
    position: 'relative',
    marginTop: 2,
  },
  trackBarFill: {
    position: 'absolute',
    left: 0,
    top: 0,
    height: '100%' as unknown as number,
    borderRadius: 1,
  },
  trackBarMarker: {
    position: 'absolute',
    left: '50%' as unknown as number,
    top: 0,
    width: 1,
    height: '100%' as unknown as number,
    backgroundColor: 'rgba(255,255,255,0.4)',
    transform: [{ translateX: -0.5 }],
  },

  trashBtn: { position: 'absolute', right: 6, bottom: 6, padding: 6 },
  tapHint: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    paddingTop: 4, borderTopWidth: 1, borderTopColor: Colors.borderSubtle,
  },
  tapHintText: { fontSize: 9, color: Colors.primary, fontWeight: '600', letterSpacing: 0.3 },
});
