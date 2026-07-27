import React, { useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Modal,
  TouchableOpacity,
  ScrollView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { Pick } from '@/lib/api';

interface PlayerProfileCardProps {
  visible: boolean;
  onClose: () => void;
  playerName: string;
  picks: Pick[];
}

const formatProp = (prop: string) => {
  if (!prop) return 'Unknown';
  return prop
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
};

const isHit = (r: string) => ['hit', 'won'].includes(r.toLowerCase());
const isMiss = (r: string) => ['miss', 'lost'].includes(r.toLowerCase());
const isPush = (r: string) => ['push'].includes(r.toLowerCase());

function StatBox({
  label,
  value,
  subtext,
  highlightColor,
}: {
  label: string;
  value: string | number;
  subtext?: string;
  highlightColor?: string;
}) {
  return (
    <View style={styles.statBox}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text
        style={[styles.statValue, highlightColor ? { color: highlightColor } : null]}
        numberOfLines={1}
        adjustsFontSizeToFit
      >
        {value}
      </Text>
      {subtext ? (
        <Text style={styles.statSubtext} numberOfLines={1}>
          {subtext}
        </Text>
      ) : null}
    </View>
  );
}

function PickRow({ pick }: { pick: Pick }) {
  const result = pick.result || '';
  const isW = isHit(result);
  const isL = isMiss(result);
  const isP = isPush(result);
  const isPending = !isW && !isL && !isP;

  const resultColor = isW
    ? Colors.success
    : isL
    ? Colors.error
    : isP
    ? Colors.push
    : Colors.textTertiary;
  const resultText = isW ? 'W' : isL ? 'L' : isP ? 'P' : '-';

  const date = pick.createdAt
    ? new Date(pick.createdAt).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
      })
    : 'Unknown';
  const propFormatted = formatProp(pick.propType || '');
  const rec = pick.recommendation?.toUpperCase() || '';
  const line = pick.line;

  return (
    <View style={styles.pickRow}>
      <View style={styles.pickRowLeft}>
        <Text style={styles.pickDate}>{date}</Text>
        <Text style={styles.pickProp} numberOfLines={1}>
          {propFormatted}
        </Text>
        <Text style={styles.pickRec}>
          <Text
            style={{
              color:
                rec === 'OVER'
                  ? Colors.success
                  : rec === 'UNDER'
                  ? Colors.error
                  : Colors.text,
            }}
          >
            {rec}{' '}
          </Text>
          {line}
        </Text>
      </View>
      <View style={styles.pickRowRight}>
        {pick.actualValue != null && (
          <Text style={styles.pickActual}>Actual: {pick.actualValue}</Text>
        )}
        <View
          style={[
            styles.resultBadge,
            {
              borderColor: resultColor,
              backgroundColor: isPending ? 'transparent' : resultColor + '1A',
            },
          ]}
        >
          <Text style={[styles.resultText, { color: resultColor }]}>
            {isPending ? 'PENDING' : resultText}
          </Text>
        </View>
      </View>
    </View>
  );
}

export default function PlayerProfileCard({
  visible,
  onClose,
  playerName,
  picks,
}: PlayerProfileCardProps) {
  const {
    playerPicks,
    hitRate,
    hits,
    misses,
    bestProp,
    worstProp,
    recentForm,
    pastPicks,
  } = useMemo(() => {
    const pPicks = picks.filter(
      (p) => p.playerName?.trim().toLowerCase() === playerName.trim().toLowerCase()
    );

    const settled = pPicks.filter(
      (p) =>
        isHit(p.result || '') ||
        isMiss(p.result || '') ||
        isPush(p.result || '')
    );

    const hitsCount = settled.filter((p) => isHit(p.result || '')).length;
    const missesCount = settled.filter((p) => isMiss(p.result || '')).length;
    const decidedCount = hitsCount + missesCount;
    const rate = decidedCount > 0 ? Math.round((hitsCount / decidedCount) * 100) : 0;

    const propStats: Record<
      string,
      { hits: number; misses: number; total: number }
    > = {};
    settled.forEach((p) => {
      if (isPush(p.result || '')) return;
      const prop = p.propType || 'Unknown';
      if (!propStats[prop]) propStats[prop] = { hits: 0, misses: 0, total: 0 };
      propStats[prop].total++;
      if (isHit(p.result || '')) propStats[prop].hits++;
      if (isMiss(p.result || '')) propStats[prop].misses++;
    });

    const propsList = Object.entries(propStats).map(([prop, stats]) => ({
      prop,
      rate: Math.round((stats.hits / stats.total) * 100),
      total: stats.total,
    }));

    let best = null;
    let worst = null;

    if (propsList.length > 0) {
      const reliableProps = propsList.filter((p) => p.total >= 3);
      const targetList = reliableProps.length > 0 ? reliableProps : propsList;

      best = [...targetList].sort(
        (a, b) => b.rate - a.rate || b.total - a.total
      )[0];
      worst = [...targetList].sort(
        (a, b) => a.rate - b.rate || b.total - a.total
      )[0];
    }

    const form = settled
      .sort(
        (a, b) =>
          new Date(b.createdAt || 0).getTime() -
          new Date(a.createdAt || 0).getTime()
      )
      .slice(0, 5)
      .map((p) => {
        if (isHit(p.result || '')) return 'W';
        if (isMiss(p.result || '')) return 'L';
        return 'P';
      })
      .reverse();

    const sortedPicks = [...pPicks].sort(
      (a, b) =>
        new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime()
    );

    return {
      playerPicks: pPicks,
      hitRate: rate,
      hits: hitsCount,
      misses: missesCount,
      bestProp: best,
      worstProp: worst,
      recentForm: form,
      pastPicks: sortedPicks,
    };
  }, [picks, playerName]);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.modalOverlay}>
        <TouchableOpacity
          style={StyleSheet.absoluteFill}
          activeOpacity={1}
          onPress={onClose}
        />
        <View style={styles.modalContent}>
          <View style={styles.header}>
            <Text style={styles.playerName}>{playerName}</Text>
            <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
              <Ionicons name="close" size={24} color={Colors.textSecondary} />
            </TouchableOpacity>
          </View>

          <ScrollView
            style={styles.scrollArea}
            contentContainerStyle={styles.scrollContent}
            showsVerticalScrollIndicator={false}
          >
            {/* Stats Grid */}
            <View style={styles.statsRow}>
              <StatBox label="Total Picks" value={playerPicks.length.toString()} />
              <StatBox
                label="Hit Rate"
                value={`${hitRate}%`}
                highlightColor={hitRate >= 55 ? Colors.success : Colors.text}
                subtext={`${hits}W - ${misses}L`}
              />
            </View>
            <View style={styles.statsRow}>
              <StatBox
                label="Best Prop"
                value={bestProp ? formatProp(bestProp.prop) : 'N/A'}
                subtext={bestProp ? `${bestProp.rate}% (${bestProp.total} picks)` : ''}
                highlightColor={Colors.primary}
              />
              <StatBox
                label="Worst Prop"
                value={worstProp ? formatProp(worstProp.prop) : 'N/A'}
                subtext={
                  worstProp ? `${worstProp.rate}% (${worstProp.total} picks)` : ''
                }
                highlightColor={Colors.error}
              />
            </View>

            {/* Recent Form */}
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Recent Form</Text>
              {recentForm.length > 0 ? (
                <View style={styles.formContainer}>
                  {recentForm.map((res, i) => (
                    <View
                      key={i}
                      style={[
                        styles.formBadge,
                        res === 'W'
                          ? styles.formW
                          : res === 'L'
                          ? styles.formL
                          : styles.formP,
                      ]}
                    >
                      <Text style={styles.formBadgeText}>{res}</Text>
                    </View>
                  ))}
                </View>
              ) : (
                <Text style={styles.emptyStateText}>No recent settled picks</Text>
              )}
            </View>

            {/* Past Picks */}
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Past Picks</Text>
              {pastPicks.length > 0 ? (
                pastPicks.map((p, idx) => (
                  <PickRow
                    pick={p}
                    key={p.id || p._id || p.pickId || idx.toString()}
                  />
                ))
              ) : (
                <View style={styles.emptyState}>
                  <Text style={styles.emptyStateText}>
                    No past picks found for this player.
                  </Text>
                </View>
              )}
            </View>
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalOverlay: {
    flex: 1,
    backgroundColor: Colors.overlay,
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: Colors.cardSecondary,
    borderTopLeftRadius: Colors.radiusLg,
    borderTopRightRadius: Colors.radiusLg,
    maxHeight: '90%',
    paddingBottom: 40, // safe area padding
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
  },
  playerName: {
    fontSize: 22,
    fontWeight: '700',
    color: Colors.text,
  },
  closeBtn: {
    padding: 4,
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusFull,
  },
  scrollArea: {
    flexShrink: 1,
  },
  scrollContent: {
    padding: 20,
    gap: 20,
  },
  statsRow: {
    flexDirection: 'row',
    gap: 12,
  },
  statBox: {
    flex: 1,
    backgroundColor: Colors.card,
    padding: 16,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  statLabel: {
    fontSize: 12,
    color: Colors.textSecondary,
    marginBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    fontWeight: '600',
  },
  statValue: {
    fontSize: 20,
    fontWeight: '700',
    color: Colors.text,
  },
  statSubtext: {
    fontSize: 13,
    color: Colors.textTertiary,
    marginTop: 4,
  },
  section: {
    marginTop: 8,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: Colors.text,
    marginBottom: 12,
  },
  formContainer: {
    flexDirection: 'row',
    gap: 8,
  },
  formBadge: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  formW: {
    backgroundColor: Colors.successDim,
    borderWidth: 1,
    borderColor: Colors.success,
  },
  formL: {
    backgroundColor: Colors.errorDim,
    borderWidth: 1,
    borderColor: Colors.error,
  },
  formP: {
    backgroundColor: Colors.pushDim,
    borderWidth: 1,
    borderColor: Colors.push,
  },
  formBadgeText: {
    fontSize: 14,
    fontWeight: '700',
    color: Colors.text,
  },
  pickRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: Colors.card,
    padding: 16,
    borderRadius: Colors.radius,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  pickRowLeft: {
    flex: 1,
    gap: 4,
  },
  pickDate: {
    fontSize: 12,
    color: Colors.textTertiary,
  },
  pickProp: {
    fontSize: 15,
    fontWeight: '600',
    color: Colors.text,
  },
  pickRec: {
    fontSize: 14,
    color: Colors.textSecondary,
    fontWeight: '500',
  },
  pickRowRight: {
    alignItems: 'flex-end',
    gap: 8,
  },
  pickActual: {
    fontSize: 12,
    color: Colors.textSecondary,
  },
  resultBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    borderWidth: 1,
    minWidth: 40,
    alignItems: 'center',
  },
  resultText: {
    fontSize: 12,
    fontWeight: '800',
  },
  emptyState: {
    padding: 20,
    alignItems: 'center',
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  emptyStateText: {
    color: Colors.textSecondary,
    fontSize: 14,
  },
});
