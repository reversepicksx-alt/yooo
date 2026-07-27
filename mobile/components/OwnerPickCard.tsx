import React, { useMemo } from 'react';
import {
  View, Text, Image, TouchableOpacity, StyleSheet, Share, Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { Pick } from '@/lib/api';

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'PASSES', passes: 'PASSES', shots: 'SHOTS', shots_on_target: 'SOT',
  tackles: 'TACKLES', key_passes: 'KEY PASSES', saves: 'SAVES', interceptions: 'INTS',
  blocks: 'BLOCKS', dribbles: 'DRIBBLES', crosses: 'CROSSES', clearances: 'CLEARANCES',
  goals: 'GOALS', assists: 'ASSISTS', fouls_drawn: 'FOULS WON', fouls_committed: 'FOULS',
  duels_won: 'DUELS', yellow_cards: 'YC', shots_assisted: 'SHOT ASSISTS',
};

function isLive(p: Pick) {
  return p.matchStatus === 'live' || p.status === 'live' || p.status === 'pending' || (!p.status && !['hit','miss','push','won','lost','dnp'].includes(p.result ?? ''));
}
function pickWon(p: Pick) { return p.result === 'hit' || p.result === 'won' || p.status === 'won'; }
function pickLost(p: Pick) { return p.result === 'miss' || p.result === 'lost' || p.status === 'lost'; }
function pickPush(p: Pick) { return p.result === 'push'; }
function pickDnp(p: Pick) { return p.result === 'dnp'; }

function getRecDir(p: Pick): 'OVER' | 'UNDER' | null {
  const rec = p.recommendation;
  if (rec === 'OVER' || rec === 'UNDER') return rec;
  const proj = p.projection ?? p.projectedValue;
  if (proj != null && p.line > 0) return proj < p.line ? 'OVER' : 'UNDER';
  return null;
}

function formatNumber(n: number | null | undefined): string {
  if (n == null) return '—';
  return Number(n).toFixed(n % 1 === 0 ? 0 : 1);
}

function buildShareText(pick: Pick): string {
  const dir = getRecDir(pick) ?? pick.recommendation ?? '';
  const prop = PROP_LABELS[pick.propType] || pick.propType?.replace(/_/g, ' ').toUpperCase() || 'PROP';
  const venue = pick.venue === 'away' ? 'AWAY' : 'HOME';
  const now = pick.actualValue ?? pick.currentValue ?? pick.projectedValue ?? null;
  const line = typeof pick.line === 'number' ? pick.line : null;
  const hit = pick.hitPct != null ? `${Math.round(pick.hitPct)}%` : null;
  let text = `${pick.playerName} ${dir} ${pick.line} ${prop} (${venue})`;
  if (now != null) text += ` — now ${formatNumber(now)}`;
  if (line != null) text += ` / line ${formatNumber(line)}`;
  if (hit) text += ` · ${hit} hit prob`;
  text += ` via Reverse Picks`;
  return text;
}

export default function OwnerPickCard({
  pick,
  onShare,
  onPress,
}: {
  pick: Pick;
  onShare?: (pick: Pick) => void;
  onPress?: () => void;
}) {
  const won = pickWon(pick);
  const lost = pickLost(pick);
  const push = pickPush(pick);
  const dnp = pickDnp(pick);
  const live = isLive(pick);
  const settled = won || lost || push || dnp;

  const dir = getRecDir(pick);
  const isOver = dir === 'OVER';
  const recColor = isOver ? Colors.success : Colors.error;

  const nowValue = settled
    ? (pick.actualValue ?? pick.currentValue ?? null)
    : (pick.currentValue ?? pick.actualValue ?? null);
  const paceValue = pick.pace ?? pick.projectedValue ?? pick.projection ?? null;
  const hitPct = pick.hitPct ?? null;
  const lineValue = typeof pick.line === 'number' ? pick.line : null;

  const progress = useMemo(() => {
    if (lineValue == null || nowValue == null || lineValue <= 0) return null;
    return Math.max(0, Math.min(100, (nowValue / (lineValue * 2)) * 100));
  }, [lineValue, nowValue]);

  const statusBadge = useMemo(() => {
    if (live && !won && !lost) {
      return (
        <View style={styles.badgeLive}>
          <View style={styles.pulseDot} />
          <Text style={styles.badgeLiveText}>LIVE</Text>
        </View>
      );
    }
    if (won) return <View style={[styles.badge, { backgroundColor: Colors.success }]}><Text style={styles.badgeText}>HIT</Text></View>;
    if (lost) return <View style={[styles.badge, { backgroundColor: Colors.error }]}><Text style={styles.badgeText}>MISS</Text></View>;
    if (push) return <View style={[styles.badge, { backgroundColor: Colors.push }]}><Text style={styles.badgeText}>PUSH</Text></View>;
    if (dnp) return <View style={[styles.badge, { backgroundColor: Colors.dnp }]}><Text style={styles.badgeText}>DNP</Text></View>;
    return null;
  }, [live, won, lost, push, dnp]);

  const handleShare = async () => {
    onShare?.(pick);
    const text = buildShareText(pick);
    try {
      if (Platform.OS === 'web' && typeof navigator !== 'undefined' && navigator.share) {
        await navigator.share({ title: `${pick.playerName} prop pick`, text });
      } else {
        await Share.share({ message: text, title: `${pick.playerName} prop pick` });
      }
    } catch {
      // user cancelled or unsupported
    }
  };

  const venueText = pick.venue === 'away' ? `AWAY` : 'HOME';
  const teamVenue = `${pick.teamName || 'Team'} · ${venueText}`;
  const propLabel = PROP_LABELS[pick.propType] || pick.propType?.replace(/_/g, ' ').toUpperCase() || 'PROP';
  const elapsed = pick.elapsed ?? (pick as any).matchMinute ?? null;

  return (
    <TouchableOpacity
      activeOpacity={onPress ? 0.85 : 1}
      onPress={onPress}
      style={[styles.card, won && styles.cardWon, lost && styles.cardLost]}
    >
      {/* Top row: photo + name/team + share + badge */}
      <View style={styles.topRow}>
        <View style={styles.identity}>
          {pick.ownerPlayerPhoto ? (
            <Image source={{ uri: pick.ownerPlayerPhoto }} style={styles.photo} />
          ) : (
            <View style={styles.photoPlaceholder}>
              <Text style={styles.photoInitial}>{pick.playerName?.charAt(0) || '?'}</Text>
            </View>
          )}
          <View style={styles.nameBlock}>
            <Text style={styles.playerName} numberOfLines={1}>{pick.playerName}</Text>
            <View style={styles.teamRow}>
              {pick.ownerTeamLogo ? (
                <Image source={{ uri: pick.ownerTeamLogo }} style={styles.teamLogo} />
              ) : null}
              <Text style={styles.teamVenue} numberOfLines={1}>{teamVenue}</Text>
            </View>
          </View>
        </View>
        <View style={styles.actions}>
          <TouchableOpacity onPress={handleShare} style={styles.shareBtn} activeOpacity={0.7}>
            <Ionicons name="share-outline" size={16} color={Colors.primary} />
          </TouchableOpacity>
          {statusBadge}
        </View>
      </View>

      {/* Stats row */}
      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>NOW</Text>
          <Text style={[styles.statValue, { color: nowValue != null && lineValue != null ? (
            (isOver && nowValue > lineValue) || (!isOver && nowValue < lineValue) ? Colors.success : Colors.error
          ) : Colors.text }]}>
            {formatNumber(nowValue)}
          </Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>LINE</Text>
          <Text style={styles.statValue}>{formatNumber(lineValue)}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>PACE</Text>
          <Text style={[styles.statValue, { color: Colors.primary }]}>{formatNumber(paceValue)}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>HIT%</Text>
          <Text style={styles.statValue}>{hitPct != null ? `${Math.round(hitPct)}%` : '—'}</Text>
        </View>
      </View>

      {/* Progress bar */}
      {progress != null && (
        <View style={styles.trackBarOuter}>
          <View style={[styles.trackBarFill, { width: `${progress}%`, backgroundColor: recColor }]} />
          <View style={[styles.trackBarMarker, { left: '50%' }]} />
        </View>
      )}

      {/* Bottom row */}
      <View style={styles.bottomRow}>
        <View style={styles.bottomItem}>
          <Ionicons name="time-outline" size={11} color={Colors.textTertiary} />
          <Text style={styles.bottomText}>{elapsed != null ? `${elapsed}'` : (live ? 'LIVE' : '—')}</Text>
        </View>
        <Text style={styles.bottomText}>{propLabel}</Text>
      </View>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#0F0F0F',
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.18)',
    padding: 12,
    marginBottom: 10,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 3 },
    shadowOpacity: 0.35,
    shadowRadius: 6,
    elevation: 5,
  },
  cardWon: { borderColor: 'rgba(57,255,20,0.45)' },
  cardLost: { borderColor: 'rgba(255,59,48,0.35)' },
  topRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  identity: { flexDirection: 'row', alignItems: 'center', flex: 1, marginRight: 10 },
  photo: {
    width: 44,
    height: 44,
    borderRadius: 22,
    borderWidth: 1.5,
    borderColor: 'rgba(57,255,20,0.35)',
    backgroundColor: '#1A1A1A',
  },
  photoPlaceholder: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#1A1A1A',
    borderWidth: 1.5,
    borderColor: 'rgba(57,255,20,0.35)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  photoInitial: { color: Colors.primary, fontSize: 18, fontWeight: '800' },
  nameBlock: { marginLeft: 10, flex: 1 },
  playerName: { color: '#fff', fontSize: 16, fontWeight: '800', letterSpacing: -0.3 },
  teamRow: { flexDirection: 'row', alignItems: 'center', marginTop: 3 },
  teamLogo: { width: 14, height: 14, marginRight: 5 },
  teamVenue: { color: Colors.textSecondary, fontSize: 11.5, fontWeight: '600' },
  actions: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  shareBtn: {
    width: 28,
    height: 28,
    borderRadius: 8,
    backgroundColor: 'rgba(57,255,20,0.10)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 6,
  },
  badgeText: { color: '#000', fontSize: 9, fontWeight: '900' },
  badgeLive: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255,59,48,0.16)',
    borderWidth: 1,
    borderColor: 'rgba(255,59,48,0.45)',
    paddingHorizontal: 6,
    paddingVertical: 2.5,
    borderRadius: 6,
  },
  pulseDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: Colors.error,
    marginRight: 4,
  },
  badgeLiveText: { color: Colors.error, fontSize: 9, fontWeight: '900' },
  statsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  stat: { alignItems: 'center', flex: 1 },
  statLabel: { color: Colors.textTertiary, fontSize: 9, fontWeight: '700', letterSpacing: 0.8, marginBottom: 3 },
  statValue: { color: '#fff', fontSize: 19, fontWeight: '800' },
  trackBarOuter: {
    height: 5,
    borderRadius: 3,
    backgroundColor: 'rgba(255,255,255,0.08)',
    overflow: 'hidden',
    marginBottom: 10,
  },
  trackBarFill: { height: '100%', borderRadius: 3 },
  trackBarMarker: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    width: 1.5,
    backgroundColor: 'rgba(255,255,255,0.45)',
  },
  bottomRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  bottomItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  bottomText: { color: Colors.textSecondary, fontSize: 10.5, fontWeight: '700' },
});
