import React, { useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Modal,
  TouchableOpacity,
  ScrollView,
  Dimensions,
  SafeAreaView,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '../constants/colors';

export type PickStatus = 'won' | 'lost' | 'pending' | 'push';
export type PickType = 'over' | 'under' | 'spread' | 'moneyline';

export interface PickObj {
  id: string | number;
  status: PickStatus;
  sport?: string;
  type?: PickType;
  date: string | number | Date;
}

interface StreaksAchievementsProps {
  visible: boolean;
  onClose: () => void;
  picks: PickObj[];
}

interface Badge {
  id: string;
  name: string;
  description: string;
  icon: keyof typeof Ionicons.glyphMap;
  color: string;
  earned: boolean;
}

const { width } = Dimensions.get('window');

const StatCard = ({
  title,
  value,
  icon,
  iconColor,
  subtitle,
  valueColor,
}: {
  title: string;
  value: string | number;
  icon: keyof typeof Ionicons.glyphMap;
  iconColor: string;
  subtitle?: string;
  valueColor?: string;
}) => (
  <View style={styles.statCard}>
    <View style={styles.statHeader}>
      <View style={[styles.iconBox, { backgroundColor: iconColor + '15' }]}>
        <Ionicons name={icon} size={16} color={iconColor} />
      </View>
      <Text style={styles.statTitle}>{title}</Text>
    </View>
    <View style={styles.statValueContainer}>
      <Text style={[styles.statValue, valueColor ? { color: valueColor } : null]}>
        {value}
      </Text>
      {subtitle && <Text style={styles.statSubtitle}>{subtitle}</Text>}
    </View>
  </View>
);

const BadgeItem = ({ badge }: { badge: Badge }) => {
  const opacity = badge.earned ? 1 : 0.5;
  return (
    <View style={[styles.badgeCard, { opacity }]}>
      <View
        style={[
          styles.badgeIconContainer,
          { backgroundColor: badge.earned ? badge.color + '20' : Colors.cardSecondary },
        ]}
      >
        <Ionicons
          name={badge.icon}
          size={32}
          color={badge.earned ? badge.color : Colors.textTertiary}
        />
        {badge.earned && (
          <View style={styles.badgeCheck}>
            <Ionicons name="checkmark-circle" size={16} color={Colors.text} />
          </View>
        )}
      </View>
      <Text style={styles.badgeName} numberOfLines={1}>
        {badge.name}
      </Text>
      <Text style={styles.badgeDesc} numberOfLines={2}>
        {badge.description}
      </Text>
      {!badge.earned && <Text style={styles.unearnedText}>Locked</Text>}
    </View>
  );
};

export default function StreaksAchievements({
  visible,
  onClose,
  picks,
}: StreaksAchievementsProps) {
  const stats = useMemo(() => {
    const resolvedPicks = [...picks].filter((p) =>
      ['won', 'lost', 'push'].includes(p.status)
    );

    const chronologicalPicks = [...resolvedPicks].sort(
      (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
    );

    let currentStreakCount = 0;
    let currentStreakType: 'W' | 'L' | null = null;

    for (let i = chronologicalPicks.length - 1; i >= 0; i--) {
      const p = chronologicalPicks[i];
      if (p.status === 'push') continue;

      if (currentStreakType === null) {
        if (p.status === 'won') currentStreakType = 'W';
        else if (p.status === 'lost') currentStreakType = 'L';
        currentStreakCount = 1;
      } else {
        if (p.status === 'won' && currentStreakType === 'W') {
          currentStreakCount++;
        } else if (p.status === 'lost' && currentStreakType === 'L') {
          currentStreakCount++;
        } else {
          break;
        }
      }
    }

    let maxWinStreak = 0;
    let tempMaxWin = 0;
    for (const p of chronologicalPicks) {
      if (p.status === 'push') continue;
      if (p.status === 'won') {
        tempMaxWin++;
        if (tempMaxWin > maxWinStreak) maxWinStreak = tempMaxWin;
      } else if (p.status === 'lost') {
        tempMaxWin = 0;
      }
    }

    const now = Date.now();
    const dayMs = 24 * 60 * 60 * 1000;

    const getRecord = (subset: PickObj[]) => {
      let w = 0,
        l = 0,
        p = 0;
      for (const pick of subset) {
        if (pick.status === 'won') w++;
        else if (pick.status === 'lost') l++;
        else if (pick.status === 'push') p++;
      }
      return `${w}-${l}${p > 0 ? `-${p}` : ''}`;
    };

    const last7Days = resolvedPicks.filter(
      (p) => now - new Date(p.date).getTime() <= 7 * dayMs
    );
    const last30Days = resolvedPicks.filter(
      (p) => now - new Date(p.date).getTime() <= 30 * dayMs
    );

    const totalWins = resolvedPicks.filter((p) => p.status === 'won').length;
    const totalResolvedWithoutPushes = resolvedPicks.filter(
      (p) => p.status === 'won' || p.status === 'lost'
    ).length;

    // Badges computation
    const computedBadges: Badge[] = [];

    // 7-Pick Heater
    computedBadges.push({
      id: 'heater',
      name: '7-Pick Heater',
      description: 'Win 7 picks in a row',
      icon: 'flame',
      color: '#FF9500',
      earned: maxWinStreak >= 7,
    });

    // Sharp Shooter
    computedBadges.push({
      id: 'sharp-shooter',
      name: 'Sharp Shooter',
      description: 'Win 65%+ of picks (min 10)',
      icon: 'disc',
      color: Colors.success,
      earned:
        totalResolvedWithoutPushes >= 10 &&
        totalWins / totalResolvedWithoutPushes >= 0.65,
    });

    // Over Crusher
    const overPicks = resolvedPicks.filter((p) => p.type?.toLowerCase() === 'over');
    const overWins = overPicks.filter((p) => p.status === 'won').length;
    const overResolved = overPicks.filter((p) => p.status === 'won' || p.status === 'lost').length;
    computedBadges.push({
      id: 'over-crusher',
      name: 'Over Crusher',
      description: 'Hit 5+ Overs at 60%+ rate',
      icon: 'trending-up',
      color: Colors.push, // Blueish
      earned: overResolved >= 5 && overWins / overResolved >= 0.6,
    });

    // Under King
    const underPicks = resolvedPicks.filter((p) => p.type?.toLowerCase() === 'under');
    const underWins = underPicks.filter((p) => p.status === 'won').length;
    const underResolved = underPicks.filter(
      (p) => p.status === 'won' || p.status === 'lost'
    ).length;
    computedBadges.push({
      id: 'under-king',
      name: 'Under King',
      description: 'Hit 5+ Unders at 60%+ rate',
      icon: 'trending-down',
      color: '#AF52DE', // Purple
      earned: underResolved >= 5 && underWins / underResolved >= 0.6,
    });

    // Soccer Specialist
    const soccerPicks = resolvedPicks.filter(
      (p) =>
        p.sport?.toLowerCase() === 'soccer' || p.sport?.toLowerCase() === 'football'
    );
    const soccerWins = soccerPicks.filter((p) => p.status === 'won').length;
    const soccerResolved = soccerPicks.filter(
      (p) => p.status === 'won' || p.status === 'lost'
    ).length;
    computedBadges.push({
      id: 'soccer-specialist',
      name: 'Soccer Specialist',
      description: 'Hit 5+ Soccer picks at 60%+ rate',
      icon: 'football',
      color: '#FF2D55', // Pink/Red
      earned: soccerResolved >= 5 && soccerWins / soccerResolved >= 0.6,
    });

    // Consistency King: 30+ picks without dropping below 50%
    computedBadges.push({
      id: 'consistency-king',
      name: 'Consistency King',
      description: 'Maintain 55%+ win rate over 30+ picks',
      icon: 'diamond',
      color: '#00C7BE', // Teal
      earned:
        totalResolvedWithoutPushes >= 30 &&
        totalWins / totalResolvedWithoutPushes >= 0.55,
    });

    return {
      currentStreakType: currentStreakType || 'W',
      currentStreakCount,
      maxWinStreak,
      record7: getRecord(last7Days),
      record30: getRecord(last30Days),
      badges: computedBadges,
    };
  }, [picks]);

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={onClose}
    >
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.header}>
          <View style={styles.headerTitleContainer}>
            <Ionicons name="medal" size={24} color={Colors.primary} />
            <Text style={styles.title}>Streaks & Badges</Text>
          </View>
          <TouchableOpacity onPress={onClose} style={styles.closeButton}>
            <Ionicons name="close" size={24} color={Colors.textSecondary} />
          </TouchableOpacity>
        </View>

        <ScrollView
          contentContainerStyle={styles.content}
          showsVerticalScrollIndicator={false}
        >
          <Text style={styles.sectionTitle}>Overview</Text>

          <View style={styles.row}>
            <StatCard
              title="Current Streak"
              value={`${stats.currentStreakType}${stats.currentStreakCount}`}
              valueColor={
                stats.currentStreakCount === 0
                  ? Colors.text
                  : stats.currentStreakType === 'W'
                  ? Colors.success
                  : Colors.error
              }
              icon={stats.currentStreakType === 'W' ? 'flame' : 'snow'}
              iconColor={
                stats.currentStreakType === 'W' ? '#FF9500' : Colors.textTertiary
              }
              subtitle="Active run"
            />
            <StatCard
              title="Best Streak"
              value={`W${stats.maxWinStreak}`}
              valueColor={Colors.primary}
              icon="trophy"
              iconColor={Colors.primary}
              subtitle="All-time high"
            />
          </View>

          <View style={styles.row}>
            <StatCard
              title="Last 7 Days"
              value={stats.record7}
              icon="calendar"
              iconColor={Colors.push}
              subtitle="Recent form"
            />
            <StatCard
              title="Last 30 Days"
              value={stats.record30}
              icon="calendar-outline"
              iconColor={Colors.push}
              subtitle="Monthly form"
            />
          </View>

          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitleWithoutMargin}>Achievements</Text>
            <Text style={styles.earnedCount}>
              {stats.badges.filter((b) => b.earned).length} / {stats.badges.length}
            </Text>
          </View>

          <View style={styles.badgesContainer}>
            {stats.badges.map((badge) => (
              <BadgeItem key={badge.id} badge={badge} />
            ))}
          </View>
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 16,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
    backgroundColor: Colors.background,
  },
  headerTitleContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  title: {
    fontSize: 20,
    fontWeight: '700',
    color: Colors.text,
  },
  closeButton: {
    padding: 4,
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusFull,
  },
  content: {
    padding: 16,
    paddingBottom: 40,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    marginTop: 24,
    marginBottom: 16,
    paddingHorizontal: 4,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: Colors.text,
    marginTop: 8,
    marginBottom: 16,
    paddingHorizontal: 4,
  },
  sectionTitleWithoutMargin: {
    fontSize: 18,
    fontWeight: '700',
    color: Colors.text,
  },
  earnedCount: {
    fontSize: 14,
    color: Colors.textSecondary,
    fontWeight: '600',
    marginBottom: 2,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 12,
    gap: 12,
  },
  statCard: {
    flex: 1,
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    padding: 16,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  statHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 12,
  },
  iconBox: {
    width: 28,
    height: 28,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  statTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: Colors.textSecondary,
  },
  statValueContainer: {
    alignItems: 'flex-start',
  },
  statValue: {
    fontSize: 28,
    fontWeight: '800',
    color: Colors.text,
    letterSpacing: -0.5,
  },
  statSubtitle: {
    fontSize: 12,
    color: Colors.textTertiary,
    marginTop: 2,
    fontWeight: '500',
  },
  badgesContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
  },
  badgeCard: {
    width: (width - 32 - 12) / 2,
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    padding: 16,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    position: 'relative',
  },
  badgeIconContainer: {
    width: 64,
    height: 64,
    borderRadius: 32,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 12,
  },
  badgeCheck: {
    position: 'absolute',
    bottom: -4,
    right: -4,
    backgroundColor: Colors.card,
    borderRadius: 12,
    padding: 2,
  },
  badgeName: {
    fontSize: 15,
    fontWeight: '700',
    color: Colors.text,
    marginBottom: 4,
    textAlign: 'center',
  },
  badgeDesc: {
    fontSize: 12,
    color: Colors.textSecondary,
    textAlign: 'center',
    lineHeight: 16,
  },
  unearnedText: {
    position: 'absolute',
    top: 12,
    right: 12,
    fontSize: 10,
    fontWeight: '700',
    color: Colors.textTertiary,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
});
