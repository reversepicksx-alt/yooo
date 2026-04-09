import React, { useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import Colors from '@/constants/colors';
import type { PredictionResult } from '@/lib/api';

interface Props {
  result: PredictionResult;
  onSave?: () => void;
  saving?: boolean;
}

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Attempts',
  shots: 'Shots',
  shots_on_target: 'Shots on Target',
  goals: 'Goals',
  assists: 'Assists',
  key_passes: 'Key Passes',
  tackles: 'Tackles',
  saves: 'Saves',
  dribbles: 'Dribbles',
  crosses: 'Crosses',
};

export default function PredictionCard({ result, onSave, saving }: Props) {
  const [showReasoning, setShowReasoning] = useState(false);

  const isOver = result.recommendation === 'OVER';
  const isUnder = result.recommendation === 'UNDER';
  const recColor = isOver ? Colors.success : isUnder ? Colors.error : Colors.textSecondary;
  const recBg = isOver ? Colors.successDim : isUnder ? Colors.errorDim : Colors.primaryDim;

  const edge = result.edgeScore != null ? Math.abs(result.edgeScore).toFixed(1) : null;
  const confPct = result.confidence != null ? Math.round(result.confidence * 100) : null;

  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <View style={styles.playerInfo}>
          <Text style={styles.playerName} numberOfLines={1}>{result.playerName || '—'}</Text>
          {result.teamName && (
            <Text style={styles.teamName} numberOfLines={1}>
              {result.teamName}{result.opponentName ? ` vs ${result.opponentName}` : ''}
            </Text>
          )}
        </View>
        {result.recommendation && result.recommendation !== 'PASS' && (
          <View style={[styles.recBadge, { backgroundColor: recBg }]}>
            <Text style={[styles.recText, { color: recColor }]}>{result.recommendation}</Text>
          </View>
        )}
      </View>

      <View style={styles.divider} />

      <View style={styles.statsRow}>
        <View style={styles.statItem}>
          <Text style={styles.statLabel}>{PROP_LABELS[result.propType || ''] || result.propType || 'Prop'}</Text>
          <Text style={styles.statValue}>{result.line ?? '—'}</Text>
          <Text style={styles.statSub}>Line</Text>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.statItem}>
          <Text style={styles.statLabel}>Projection</Text>
          <Text style={[styles.statValue, { color: Colors.primary }]}>
            {result.projection?.toFixed(1) ?? result.bayesianProjection?.toFixed(1) ?? '—'}
          </Text>
          <Text style={styles.statSub}>Reverse Formula</Text>
        </View>
        <View style={styles.statDivider} />
        <View style={styles.statItem}>
          <Text style={styles.statLabel}>Confidence</Text>
          <Text style={[styles.statValue, { color: Colors.accent }]}>
            {confPct != null ? `${confPct}%` : '—'}
          </Text>
          <Text style={styles.statSub}>{edge ? `Edge ${edge}` : 'Score'}</Text>
        </View>
      </View>

      {result.reasoning && (
        <>
          <TouchableOpacity
            style={styles.reasoningToggle}
            onPress={() => { setShowReasoning(!showReasoning); Haptics.selectionAsync(); }}
          >
            <Ionicons name={showReasoning ? 'chevron-up' : 'chevron-down'} size={14} color={Colors.textSecondary} />
            <Text style={styles.reasoningToggleText}>
              {showReasoning ? 'Hide' : 'Show'} AI Reasoning
            </Text>
          </TouchableOpacity>
          {showReasoning && (
            <Text style={styles.reasoningText}>{result.reasoning}</Text>
          )}
        </>
      )}

      {onSave && (
        <TouchableOpacity
          style={styles.saveBtn}
          onPress={() => { onSave(); Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); }}
          disabled={saving}
          activeOpacity={0.8}
        >
          {saving
            ? <ActivityIndicator color="#000" size="small" />
            : <>
                <Ionicons name="bookmark" size={16} color="#000" />
                <Text style={styles.saveBtnText}>Save Pick</Text>
              </>
          }
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    padding: 20,
    borderWidth: 1,
    borderColor: Colors.border,
    marginBottom: 12,
  },
  header: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 },
  playerInfo: { flex: 1 },
  playerName: { fontSize: 18, fontWeight: '700', color: Colors.text },
  teamName: { fontSize: 13, color: Colors.textSecondary, marginTop: 2 },
  recBadge: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 20 },
  recText: { fontSize: 13, fontWeight: '800', letterSpacing: 1 },
  divider: { height: 1, backgroundColor: Colors.border, marginVertical: 16 },
  statsRow: { flexDirection: 'row', justifyContent: 'space-around' },
  statItem: { alignItems: 'center', flex: 1 },
  statLabel: { fontSize: 11, color: Colors.textSecondary, fontWeight: '600', letterSpacing: 0.5, marginBottom: 4 },
  statValue: { fontSize: 26, fontWeight: '800', color: Colors.text },
  statSub: { fontSize: 10, color: Colors.textTertiary, marginTop: 2 },
  statDivider: { width: 1, backgroundColor: Colors.border },
  reasoningToggle: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 14, marginBottom: 6 },
  reasoningToggleText: { fontSize: 12, color: Colors.textSecondary, fontWeight: '600' },
  reasoningText: { fontSize: 13, color: Colors.textSecondary, lineHeight: 20, marginTop: 6 },
  saveBtn: {
    marginTop: 16,
    backgroundColor: Colors.primary,
    borderRadius: Colors.radius,
    height: 44,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  saveBtnText: { color: '#000', fontWeight: '700', fontSize: 15 },
});
