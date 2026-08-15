import React, { useEffect, useMemo, useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';

export type AnalysisScenario = {
  line: number;
  projection: number | null;
  pOver: number | null;
  pUnder: number | null;
  recommendation: 'OVER' | 'UNDER' | 'PASS';
  edge: number | null;
  confidence: number | null;
  hasDynamicProbability: boolean;
  isBaseLine: boolean;
};

type AnalysisScenarioInput = {
  baseLine: number;
  line: number;
  projection: number | null;
  pOver: number | null;
  pUnder: number | null;
  posteriorStd: number | null;
  baseRecommendation?: string | null;
  baseConfidence?: number | null;
};

type Props = AnalysisScenarioInput & {
  propLabel: string;
  onLineChange: (line: number) => void;
  onReset: () => void;
};

function finiteNumber(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

// Abramowitz and Stegun approximation; enough precision for a user-facing
// scenario slider without adding a statistics dependency to the mobile app.
function erf(value: number) {
  const sign = value < 0 ? -1 : 1;
  const x = Math.abs(value);
  const t = 1 / (1 + 0.3275911 * x);
  const polynomial = ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t;
  return sign * (1 - polynomial * Math.exp(-x * x));
}

function normalCdf(value: number) {
  return 0.5 * (1 + erf(value / Math.sqrt(2)));
}

// Inverse normal CDF used only to infer a legacy saved pick's spread when its
// persisted posteriorStd is missing.
function inverseNormalCdf(probability: number) {
  const p = clamp(probability, 0.0001, 0.9999);
  const a = [
    -39.6968302866538,
    220.946098424521,
    -275.928510446969,
    138.357751867269,
    -30.6647980661472,
    2.50662827745924,
  ];
  const b = [
    -54.4760987982241,
    161.585836858041,
    -155.698979859887,
    66.8013118877197,
    -13.2806815528857,
  ];
  const c = [
    -0.00778489400243029,
    -0.32239645804114,
    -2.40075827716184,
    -2.54973253934373,
    4.37466414146497,
    2.93816398269878,
  ];
  const d = [
    0.00778469570904146,
    0.32246712907004,
    2.445134137143,
    3.75440866190742,
  ];
  const low = 0.02425;
  const high = 1 - low;
  if (p < low) {
    const q = Math.sqrt(-2 * Math.log(p));
    const numerator = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]);
    const denominator = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    return numerator / denominator;
  }
  if (p > high) {
    const q = Math.sqrt(-2 * Math.log(1 - p));
    const numerator = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]);
    const denominator = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    return -numerator / denominator;
  }
  const q = p - 0.5;
  const r = q * q;
  const numerator = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q;
  const denominator = (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
  return numerator / denominator;
}

export function calculateAnalysisScenario(input: AnalysisScenarioInput): AnalysisScenario {
  const projection = finiteNumber(input.projection);
  const basePOver = finiteNumber(input.pOver);
  const basePUnder = finiteNumber(input.pUnder);
  const baseLine = input.baseLine;
  const line = input.line;

  let posteriorStd = finiteNumber(input.posteriorStd);
  if (projection != null && basePOver != null && basePOver > 0 && basePOver < 100) {
    const z = inverseNormalCdf(1 - basePOver / 100);
    const inferred = Math.abs(baseLine - projection) / Math.abs(z);
    // Some legacy responses expose covariate sigma (for example 0.28) in
    // posteriorStd even though it is not in the prop's stat units. When the
    // saved probability is available, infer the usable spread from that
    // exact base probability instead of turning every what-if into 99/1.
    if (Number.isFinite(inferred) && inferred > 0.5 && (!posteriorStd || posteriorStd < 1)) {
      posteriorStd = inferred;
    }
  }
  posteriorStd = posteriorStd && posteriorStd > 0 ? posteriorStd : 10;

  let pOver: number | null = null;
  let pUnder: number | null = null;
  let hasDynamicProbability = false;
  if (projection != null) {
    if (Math.abs(line - baseLine) < 0.001 && basePOver != null) {
      // Keep the backend's exact posted-line probability at the initial state.
      pOver = clamp(basePOver, 1, 99);
      pUnder = clamp(basePUnder ?? 100 - pOver, 1, 99);
    } else {
      pUnder = clamp(normalCdf((line - projection) / posteriorStd) * 100, 1, 99);
      pOver = clamp(100 - pUnder, 1, 99);
    }
    hasDynamicProbability = true;
  } else if (line === baseLine && basePOver != null) {
    pOver = clamp(basePOver, 1, 99);
    pUnder = clamp(basePUnder ?? 100 - pOver, 1, 99);
  }

  const recommendation = pOver == null || pUnder == null || Math.max(pOver, pUnder) < 55
    ? 'PASS'
    : pOver >= pUnder ? 'OVER' : 'UNDER';
  const edge = projection != null ? projection - line : null;
  const confidence = Math.max(pOver ?? 0, pUnder ?? 0) || finiteNumber(input.baseConfidence);

  return {
    line,
    projection,
    pOver,
    pUnder,
    recommendation,
    edge,
    confidence,
    hasDynamicProbability,
    isBaseLine: Math.abs(line - baseLine) < 0.001,
  };
}

function directionColor(direction: string) {
  return direction === 'OVER'
    ? Colors.success
    : direction === 'UNDER'
    ? Colors.error
    : Colors.textSecondary;
}

export default function AnalysisScenarioPanel({
  baseLine,
  line,
  projection,
  pOver,
  pUnder,
  posteriorStd,
  baseRecommendation,
  baseConfidence,
  propLabel,
  onLineChange,
  onReset,
}: Props) {
  const [draftLine, setDraftLine] = useState(String(line));
  const scenario = useMemo(() => calculateAnalysisScenario({
    baseLine,
    line,
    projection,
    pOver,
    pUnder,
    posteriorStd,
    baseRecommendation,
    baseConfidence,
  }), [baseConfidence, baseLine, baseRecommendation, line, pOver, pUnder, posteriorStd, projection]);

  useEffect(() => {
    setDraftLine(line.toFixed(1));
  }, [line]);

  const direction = scenario.recommendation;
  const accent = directionColor(direction);
  const step = baseLine % 1 !== 0 ? 0.5 : 0.5;
  const probability = scenario.pOver != null && scenario.pUnder != null
    ? `${Math.round(Math.max(scenario.pOver, scenario.pUnder))}%`
    : '—';
  const edgeText = scenario.edge == null
    ? 'Edge unavailable'
    : `${scenario.edge >= 0 ? '+' : ''}${scenario.edge.toFixed(1)} vs line`;

  const commitDraft = () => {
    const parsed = Number(draftLine.replace(',', '.'));
    if (!Number.isFinite(parsed)) {
      setDraftLine(line.toFixed(1));
      return;
    }
    const next = Math.max(0, Math.round(parsed * 2) / 2);
    setDraftLine(next.toFixed(1));
    onLineChange(next);
  };

  const changeLine = (delta: number) => {
    const next = Math.max(0, Math.round((line + delta) * 2) / 2);
    setDraftLine(next.toFixed(1));
    onLineChange(next);
  };

  return (
    <View style={[styles.card, { borderColor: accent + '55' }]}>
      <View style={styles.header}>
        <View style={styles.headerTitleRow}>
          <View style={[styles.icon, { backgroundColor: accent + '18' }]}>
            <Ionicons name="options-outline" size={14} color={accent} />
          </View>
          <View>
            <Text style={styles.eyebrow}>MODEL SCENARIO</Text>
            <Text style={styles.title}>{propLabel}</Text>
          </View>
        </View>
        <View style={styles.scenarioPill}>
          <Text style={styles.scenarioPillText}>{scenario.isBaseLine ? 'BASE LINE' : 'WHAT-IF'}</Text>
        </View>
      </View>

      <View style={styles.lineEditor}>
        <View style={styles.lineEditorCopy}>
          <Text style={styles.lineLabel}>EDIT THE LINE</Text>
          <Text style={styles.lineHint}>Probabilities and edge update instantly</Text>
        </View>
        <View style={styles.stepper}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Decrease line"
            onPress={() => changeLine(-step)}
            style={({ pressed }) => [styles.stepButton, pressed && styles.pressed]}
          >
            <Ionicons name="remove" size={16} color={Colors.text} />
          </Pressable>
          <TextInput
            value={draftLine}
            onChangeText={setDraftLine}
            onBlur={commitDraft}
            onSubmitEditing={commitDraft}
            keyboardType="decimal-pad"
            returnKeyType="done"
            selectTextOnFocus
            style={styles.lineInput}
            accessibilityLabel="Scenario line"
          />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Increase line"
            onPress={() => changeLine(step)}
            style={({ pressed }) => [styles.stepButton, pressed && styles.pressed]}
          >
            <Ionicons name="add" size={16} color={Colors.text} />
          </Pressable>
        </View>
      </View>

      <View style={styles.hero}>
        <View style={styles.heroMain}>
          <Text style={[styles.recommendation, { color: accent }]}>{direction}</Text>
          <Text style={styles.heroSub}>{probability} scenario probability</Text>
        </View>
        <View style={styles.heroProjection}>
          <Text style={styles.metricLabel}>PROJECTION</Text>
          <Text style={[styles.projection, { color: accent }]}>
            {scenario.projection != null ? scenario.projection.toFixed(1) : '—'}
          </Text>
          <Text style={[styles.edge, { color: accent }]}>{edgeText}</Text>
        </View>
      </View>

      <View style={styles.probabilityRow}>
        <View style={styles.probabilityCard}>
          <Text style={styles.metricLabel}>OVER PROBABILITY</Text>
          <Text style={[styles.probabilityValue, { color: Colors.success }]}>
            {scenario.pOver != null ? `${scenario.pOver.toFixed(1)}%` : '—'}
          </Text>
        </View>
        <View style={styles.probabilityCard}>
          <Text style={styles.metricLabel}>UNDER PROBABILITY</Text>
          <Text style={[styles.probabilityValue, { color: '#60A5FA' }]}>
            {scenario.pUnder != null ? `${scenario.pUnder.toFixed(1)}%` : '—'}
          </Text>
        </View>
      </View>

      {scenario.pOver != null && scenario.pUnder != null && (
        <View style={styles.distribution}>
          <View style={[styles.distributionOver, { flex: Math.max(scenario.pOver, 1) }]} />
          <View style={[styles.distributionUnder, { flex: Math.max(scenario.pUnder, 1) }]} />
        </View>
      )}
      <View style={styles.distributionLabels}>
        <Text style={styles.distributionLabel}>OVER</Text>
        <Text style={styles.distributionLabel}>UNDER</Text>
      </View>

      <View style={styles.footer}>
        <Text style={styles.footerText}>
          {scenario.isBaseLine
            ? 'Base model line · projection comes from the saved prediction'
            : `Scenario only · base line was ${baseLine.toFixed(1)}`}
        </Text>
        {!scenario.isBaseLine && (
          <Pressable onPress={onReset} accessibilityRole="button" style={styles.resetButton}>
            <Text style={styles.resetText}>Reset</Text>
          </Pressable>
        )}
      </View>
    </View>
  );
}

const styles = {
  card: {
    backgroundColor: Colors.card,
    borderRadius: 14,
    borderWidth: 1,
    padding: 14,
    marginBottom: 16,
    gap: 12,
  },
  header: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    justifyContent: 'space-between' as const,
    gap: 10,
  },
  headerTitleRow: { flexDirection: 'row' as const, alignItems: 'center' as const, gap: 9 },
  icon: {
    width: 30,
    height: 30,
    borderRadius: 9,
    alignItems: 'center' as const,
    justifyContent: 'center' as const,
  },
  eyebrow: {
    fontSize: 8,
    color: Colors.textTertiary,
    fontWeight: '900' as const,
    letterSpacing: 1.2,
  },
  title: { marginTop: 2, fontSize: 14, color: Colors.text, fontWeight: '800' as const },
  scenarioPill: {
    backgroundColor: Colors.cardSecondary,
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 5,
  },
  scenarioPillText: {
    fontSize: 8,
    color: Colors.textSecondary,
    fontWeight: '900' as const,
    letterSpacing: 0.7,
  },
  lineEditor: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    justifyContent: 'space-between' as const,
    gap: 10,
    padding: 10,
    borderRadius: 10,
    backgroundColor: Colors.cardSecondary,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  lineEditorCopy: { flex: 1 },
  lineLabel: {
    fontSize: 9,
    color: Colors.text,
    fontWeight: '900' as const,
    letterSpacing: 0.7,
  },
  lineHint: { marginTop: 3, fontSize: 10, color: Colors.textTertiary },
  stepper: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    borderRadius: 8,
    overflow: 'hidden' as const,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.card,
  },
  stepButton: {
    width: 32,
    height: 34,
    alignItems: 'center' as const,
    justifyContent: 'center' as const,
  },
  pressed: { opacity: 0.55 },
  lineInput: {
    width: 58,
    height: 34,
    paddingHorizontal: 4,
    textAlign: 'center' as const,
    color: Colors.text,
    fontSize: 16,
    fontWeight: '900' as const,
    borderLeftWidth: 1,
    borderRightWidth: 1,
    borderColor: Colors.border,
  },
  hero: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    justifyContent: 'space-between' as const,
    paddingTop: 2,
  },
  heroMain: { flex: 1 },
  recommendation: {
    fontSize: 28,
    lineHeight: 32,
    fontWeight: '900' as const,
    letterSpacing: 1.2,
  },
  heroSub: { marginTop: 3, fontSize: 10, color: Colors.textSecondary, fontWeight: '700' as const },
  heroProjection: { alignItems: 'flex-end' as const },
  metricLabel: {
    fontSize: 8,
    color: Colors.textTertiary,
    fontWeight: '900' as const,
    letterSpacing: 0.65,
  },
  projection: { marginTop: 1, fontSize: 26, fontWeight: '900' as const },
  edge: { marginTop: 1, fontSize: 10, fontWeight: '800' as const },
  probabilityRow: { flexDirection: 'row' as const, gap: 8 },
  probabilityCard: {
    flex: 1,
    padding: 10,
    borderRadius: 9,
    backgroundColor: Colors.cardSecondary,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  probabilityValue: { marginTop: 4, fontSize: 21, fontWeight: '900' as const },
  distribution: {
    height: 8,
    flexDirection: 'row' as const,
    overflow: 'hidden' as const,
    borderRadius: 99,
    backgroundColor: Colors.borderSubtle,
  },
  distributionOver: { backgroundColor: Colors.success },
  distributionUnder: { backgroundColor: '#60A5FA' },
  distributionLabels: {
    flexDirection: 'row' as const,
    justifyContent: 'space-between' as const,
    marginTop: -8,
  },
  distributionLabel: {
    fontSize: 7,
    color: Colors.textTertiary,
    fontWeight: '900' as const,
    letterSpacing: 0.7,
  },
  footer: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    justifyContent: 'space-between' as const,
    gap: 8,
  },
  footerText: { flex: 1, fontSize: 9, color: Colors.textTertiary, lineHeight: 13 },
  resetButton: { paddingHorizontal: 8, paddingVertical: 3 },
  resetText: { fontSize: 10, color: Colors.primary, fontWeight: '800' as const },
};