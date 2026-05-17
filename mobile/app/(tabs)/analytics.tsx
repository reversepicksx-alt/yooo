import React from 'react';
import {
  View, Text, StyleSheet, ScrollView,
  ActivityIndicator, RefreshControl, Platform,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useQuery } from '@tanstack/react-query';
import Colors from '@/constants/colors';
import { getOwnerAnalytics, AnalyticsBucket, ConfidenceTier } from '@/lib/api';

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Attempts',
  saves: 'Saves',
  shots: 'Shots',
  shots_on_target: 'SOT',
  goals: 'Goals',
  assists: 'Assists',
  key_passes: 'Key Passes',
  tackles: 'Tackles',
  dribbles: 'Dribbles',
  clearances: 'Clearances',
};

function winColor(pct: number): string {
  if (pct >= 68) return Colors.primary;
  if (pct >= 58) return '#FFCC00';
  return Colors.error;
}

function roiColor(roi: number): string {
  if (roi >= 5) return Colors.primary;
  if (roi >= 0) return '#FFCC00';
  return Colors.error;
}

function brierLabel(score: number): { text: string; color: string } {
  if (score <= 0.20) return { text: 'Excellent', color: Colors.primary };
  if (score <= 0.25) return { text: 'Well Calibrated', color: Colors.primary };
  if (score <= 0.30) return { text: 'Slightly Overconfident', color: '#FFCC00' };
  return { text: 'Needs Calibration', color: Colors.error };
}

function ConfidenceQualityCard({
  brierScore, brierN, tiers,
}: { brierScore: number | null; brierN: number; tiers: ConfidenceTier[] }) {
  return (
    <View style={styles.confCard}>
      <View style={styles.sectionHeader}>
        <Ionicons name="analytics-outline" size={14} color={Colors.primary} />
        <Text style={styles.sectionTitle}>Confidence Quality</Text>
      </View>

      {/* Brier Score */}
      {brierScore !== null && (
        <View style={styles.brierRow}>
          <View style={styles.brierLeft}>
            <Text style={styles.brierNum}>{brierScore.toFixed(3)}</Text>
            <Text style={styles.brierSub}>Brier Score</Text>
          </View>
          <View style={styles.brierRight}>
            <Text style={[styles.brierBadge, { color: brierLabel(brierScore).color }]}>
              {brierLabel(brierScore).text}
            </Text>
            <Text style={styles.brierHint}>
              Lower = better · perfect = 0 · random = 0.25 · n={brierN}
            </Text>
          </View>
        </View>
      )}

      {/* ROI by confidence tier */}
      {tiers.length > 0 && (
        <>
          <View style={styles.tierHeader}>
            <Text style={[styles.tierCol, { flex: 2 }]}>TIER</Text>
            <Text style={styles.tierCol}>PICKS</Text>
            <Text style={styles.tierCol}>HIT%</Text>
            <Text style={styles.tierCol}>EST ROI</Text>
          </View>
          {tiers.map((t) => (
            <View key={t.label} style={styles.tierRow}>
              <Text style={[styles.tierCell, { flex: 2, color: Colors.text }]} numberOfLines={1}>
                {t.label}
              </Text>
              <Text style={styles.tierCell}>{t.total}</Text>
              <Text style={[styles.tierCell, { color: winColor(t.winPct), fontWeight: '700' }]}>
                {t.winPct.toFixed(1)}%
              </Text>
              <Text style={[styles.tierCell, { color: roiColor(t.roi), fontWeight: '700' }]}>
                {t.roi >= 0 ? '+' : ''}{t.roi.toFixed(1)}%
              </Text>
            </View>
          ))}
          <Text style={styles.tierNote}>Est. ROI assumes −110 lines · post-Apr 30 picks only</Text>
        </>
      )}

      {brierScore === null && tiers.length === 0 && (
        <Text style={styles.tierNote}>Not enough post-calibration picks yet (need ≥10).</Text>
      )}
    </View>
  );
}

function BarRow({ item, maxTotal }: { item: AnalyticsBucket; maxTotal: number }) {
  const barWidth = maxTotal > 0 ? (item.total / maxTotal) * 100 : 0;
  const fillWidth = item.total > 0 ? (item.hits / item.total) * 100 : 0;
  const color = winColor(item.winPct);
  const label = PROP_LABELS[item.label] || item.label?.toUpperCase() || '—';

  return (
    <View style={styles.barRow}>
      <Text style={styles.barLabel}>{label}</Text>
      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width: `${fillWidth}%` as any, backgroundColor: color }]} />
        <View style={[styles.barEmpty, { width: `${100 - fillWidth}%` as any }]} />
      </View>
      <View style={styles.barStats}>
        <Text style={[styles.barPct, { color }]}>{item.winPct.toFixed(0)}%</Text>
        <Text style={styles.barDetail}>{item.hits}H · {item.misses}M · {item.total}</Text>
      </View>
    </View>
  );
}

function Section({
  title, icon, items,
}: { title: string; icon: keyof typeof Ionicons.glyphMap; items: AnalyticsBucket[] }) {
  if (!items || items.length === 0) return null;
  const maxTotal = Math.max(...items.map((i) => i.total));
  return (
    <View style={styles.section}>
      <View style={styles.sectionHeader}>
        <Ionicons name={icon} size={14} color={Colors.primary} />
        <Text style={styles.sectionTitle}>{title}</Text>
      </View>
      {items.map((item) => (
        <BarRow key={item.label} item={item} maxTotal={maxTotal} />
      ))}
    </View>
  );
}

export default function AnalyticsTab() {
  const insets = useSafeAreaInsets();

  const { data, isLoading, refetch, isRefetching } = useQuery({
    queryKey: ['ownerAnalytics'],
    queryFn: getOwnerAnalytics,
    staleTime: 60_000,
  });

  const overall = data?.overall;
  const streak = data?.streak;
  const form = data?.recentForm ?? [];

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={isRefetching}
            onRefresh={refetch}
            tintColor={Colors.primary}
          />
        }
      >
        {/* Header */}
        <View style={styles.header}>
          <View style={styles.headerLeft}>
            <Ionicons name="lock-closed" size={13} color={Colors.primary} style={{ marginRight: 6 }} />
            <Text style={styles.headerSub}>PRIVATE</Text>
          </View>
          <Text style={styles.headerTitle}>Data Analysis</Text>
        </View>

        {isLoading ? (
          <ActivityIndicator color={Colors.primary} style={{ marginTop: 60 }} />
        ) : !data ? (
          <Text style={styles.empty}>No data available.</Text>
        ) : (
          <>
            {/* Overall win card */}
            <View style={styles.overallCard}>
              <View style={styles.overallMain}>
                <Text style={styles.overallPct}>{overall?.winPct?.toFixed(1)}%</Text>
                <Text style={styles.overallLabel}>Win Rate</Text>
              </View>
              <View style={styles.overallDivider} />
              <View style={styles.overallStat}>
                <Text style={[styles.overallStatNum, { color: Colors.primary }]}>{overall?.hits}</Text>
                <Text style={styles.overallStatLbl}>Hits</Text>
              </View>
              <View style={styles.overallStat}>
                <Text style={[styles.overallStatNum, { color: Colors.error }]}>{overall?.misses}</Text>
                <Text style={styles.overallStatLbl}>Miss</Text>
              </View>
              <View style={styles.overallStat}>
                <Text style={styles.overallStatNum}>{overall?.total}</Text>
                <Text style={styles.overallStatLbl}>Total</Text>
              </View>
            </View>

            {/* Current streak + recent form */}
            <View style={styles.formCard}>
              <View style={styles.formLeft}>
                <Text style={styles.formLabel}>STREAK</Text>
                <View style={styles.streakRow}>
                  <Text style={[
                    styles.streakNum,
                    { color: streak?.type === 'hit' ? Colors.primary : Colors.error }
                  ]}>
                    {streak?.count}
                  </Text>
                  <Text style={[
                    styles.streakType,
                    { color: streak?.type === 'hit' ? Colors.primary : Colors.error }
                  ]}>
                    {streak?.type === 'hit' ? 'W' : 'L'}
                  </Text>
                </View>
              </View>
              <View style={styles.formDivider} />
              <View style={styles.formRight}>
                <Text style={styles.formLabel}>LAST 10</Text>
                <View style={styles.dotRow}>
                  {form.slice(-10).map((p, i) => (
                    <View
                      key={i}
                      style={[
                        styles.dot,
                        { backgroundColor: p.result === 'hit' ? Colors.primary : Colors.error }
                      ]}
                    />
                  ))}
                </View>
              </View>
            </View>

            {/* Confidence Quality — Brier Score + ROI by tier */}
            <ConfidenceQualityCard
              brierScore={data.brierScore ?? null}
              brierN={data.brierN ?? 0}
              tiers={data.confidenceTiers ?? []}
            />

            {/* Key insight callout */}
            <View style={styles.insightCard}>
              <Ionicons name="bulb-outline" size={15} color="#FFCC00" style={{ marginRight: 8 }} />
              <Text style={styles.insightText}>
                <Text style={{ color: Colors.primary, fontWeight: '700' }}>UNDER </Text>
                picks hit at{' '}
                <Text style={{ color: Colors.primary, fontWeight: '700' }}>
                  {data.byDirection.find((d) => d.label?.toLowerCase() === 'under')?.winPct?.toFixed(1) ?? '—'}%
                </Text>
                {' '}— your bot's clearest edge. OVER sits at{' '}
                <Text style={{ color: Colors.error, fontWeight: '700' }}>
                  {data.byDirection.find((d) => d.label?.toLowerCase() === 'over')?.winPct?.toFixed(1) ?? '—'}%
                </Text>
                .
              </Text>
            </View>

            <Section
              title="By Direction"
              icon="swap-vertical"
              items={data.byDirection}
            />

            <Section
              title="By Venue"
              icon="location"
              items={data.byVenue}
            />

            <Section
              title="By Position"
              icon="people"
              items={data.byPosition}
            />

            <Section
              title="By Prop Type"
              icon="bar-chart"
              items={data.byPropType}
            />

            <Section
              title="By League"
              icon="trophy"
              items={data.byLeague ?? []}
            />

            <Text style={styles.footer}>
              Based on {overall?.total} settled picks · Refreshes on pull
            </Text>
          </>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  scroll: { flex: 1 },
  content: {
    paddingHorizontal: 16,
    paddingBottom: 40,
  },

  // Header
  header: {
    paddingTop: 18,
    paddingBottom: 12,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 4,
  },
  headerSub: {
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1.5,
    color: Colors.primary,
    opacity: 0.7,
  },
  headerTitle: {
    fontSize: 26,
    fontWeight: '700',
    color: Colors.text,
    letterSpacing: -0.3,
  },

  empty: {
    color: Colors.textSecondary,
    textAlign: 'center',
    marginTop: 80,
  },

  // Overall card
  overallCard: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.border,
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 18,
    paddingHorizontal: 20,
    marginBottom: 10,
  },
  overallMain: {
    flex: 1.2,
    alignItems: 'center',
  },
  overallPct: {
    fontSize: 36,
    fontWeight: '800',
    color: Colors.primary,
    letterSpacing: -1,
  },
  overallLabel: {
    fontSize: 10,
    fontWeight: '600',
    color: Colors.textSecondary,
    letterSpacing: 1,
    marginTop: 2,
  },
  overallDivider: {
    width: 1,
    height: 44,
    backgroundColor: Colors.borderSubtle,
    marginHorizontal: 16,
  },
  overallStat: {
    flex: 1,
    alignItems: 'center',
  },
  overallStatNum: {
    fontSize: 22,
    fontWeight: '700',
    color: Colors.text,
  },
  overallStatLbl: {
    fontSize: 10,
    fontWeight: '600',
    color: Colors.textSecondary,
    letterSpacing: 0.8,
    marginTop: 2,
  },

  // Form card
  formCard: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 14,
    paddingHorizontal: 20,
    marginBottom: 10,
  },
  formLeft: {
    alignItems: 'center',
    paddingRight: 20,
  },
  formDivider: {
    width: 1,
    height: 36,
    backgroundColor: Colors.borderSubtle,
    marginRight: 20,
  },
  formRight: {
    flex: 1,
  },
  formLabel: {
    fontSize: 9,
    fontWeight: '700',
    color: Colors.textTertiary,
    letterSpacing: 1.2,
    marginBottom: 6,
  },
  streakRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
  },
  streakNum: {
    fontSize: 28,
    fontWeight: '800',
    letterSpacing: -1,
  },
  streakType: {
    fontSize: 16,
    fontWeight: '700',
    marginLeft: 3,
  },
  dotRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 5,
  },
  dot: {
    width: 9,
    height: 9,
    borderRadius: 5,
  },

  // Insight
  insightCard: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    backgroundColor: 'rgba(255,204,0,0.06)',
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: 'rgba(255,204,0,0.2)',
    padding: 12,
    marginBottom: 16,
  },
  insightText: {
    flex: 1,
    fontSize: 13,
    lineHeight: 18,
    color: Colors.textSecondary,
  },

  // Sections
  section: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    paddingVertical: 14,
    paddingHorizontal: 16,
    marginBottom: 10,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
    gap: 6,
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: '700',
    color: Colors.textSecondary,
    letterSpacing: 1.2,
    textTransform: 'uppercase',
  },

  // Bar rows
  barRow: {
    marginBottom: 12,
  },
  barLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: Colors.text,
    marginBottom: 5,
  },
  barTrack: {
    height: 6,
    borderRadius: 3,
    flexDirection: 'row',
    overflow: 'hidden',
    backgroundColor: Colors.borderSubtle,
    marginBottom: 5,
  },
  barFill: {
    height: 6,
    borderRadius: 3,
  },
  barEmpty: {
    height: 6,
  },
  barStats: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  barPct: {
    fontSize: 13,
    fontWeight: '700',
  },
  barDetail: {
    fontSize: 11,
    color: Colors.textTertiary,
  },

  footer: {
    textAlign: 'center',
    fontSize: 11,
    color: Colors.textTertiary,
    marginTop: 8,
  },

  // Confidence Quality card
  confCard: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    paddingVertical: 14,
    paddingHorizontal: 16,
    marginBottom: 10,
  },
  brierRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(57,255,20,0.05)',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.15)',
    padding: 12,
    marginBottom: 14,
    gap: 14,
  },
  brierLeft: {
    alignItems: 'center',
    minWidth: 58,
  },
  brierNum: {
    fontSize: 26,
    fontWeight: '800',
    color: Colors.text,
    letterSpacing: -0.5,
  },
  brierSub: {
    fontSize: 9,
    fontWeight: '700',
    color: Colors.textTertiary,
    letterSpacing: 1,
    marginTop: 2,
    textTransform: 'uppercase',
  },
  brierRight: {
    flex: 1,
  },
  brierBadge: {
    fontSize: 14,
    fontWeight: '700',
    marginBottom: 3,
  },
  brierHint: {
    fontSize: 10,
    color: Colors.textTertiary,
    lineHeight: 14,
  },
  tierHeader: {
    flexDirection: 'row',
    paddingBottom: 6,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderSubtle,
    marginBottom: 4,
  },
  tierCol: {
    flex: 1,
    fontSize: 9,
    fontWeight: '700',
    color: Colors.textTertiary,
    letterSpacing: 1,
    textTransform: 'uppercase',
  },
  tierRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 7,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.04)',
  },
  tierCell: {
    flex: 1,
    fontSize: 13,
    color: Colors.textSecondary,
  },
  tierNote: {
    fontSize: 10,
    color: Colors.textTertiary,
    marginTop: 8,
    textAlign: 'center',
  },
});
