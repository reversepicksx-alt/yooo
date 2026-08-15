import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';

export type AnalysisScenario = {
  line: number;
  projection: number | null;
  pOver: number | null;
  pUnder: number | null;
  landingBands: AnalysisScenarioLandingBand[];
  recommendation: 'OVER' | 'UNDER' | 'PASS';
  edge: number | null;
  confidence: number | null;
  hasDynamicProbability: boolean;
  isBaseLine: boolean;
};

export type AnalysisScenarioLandingBand = {
  label: string;
  lower?: number | null;
  upper?: number | null;
  probability: number;
};

type AnalysisScenarioInput = {
  baseLine: number;
  line: number;
  projection: number | null;
  pOver: number | null;
  pUnder: number | null;
  posteriorStd: number | null;
  baseLandingBands?: AnalysisScenarioLandingBand[] | null;
  baseRecommendation?: string | null;
  baseConfidence?: number | null;
};

type Props = AnalysisScenarioInput & {
  propLabel: string;
  onLineChange: (line: number) => void;
  onLineStep?: (delta: number) => void;
  onReset: () => void;
  embedded?: boolean;
  compact?: boolean;
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

function scenarioBandLabel(
  lower: number | null,
  upper: number | null,
  displayAsInteger: boolean,
) {
  if (displayAsInteger) {
    if (lower == null) return `≤${Math.ceil(upper ?? 0) - 1}`;
    if (upper == null) return `${Math.ceil(lower)}+`;
    return `${Math.ceil(lower)}–${Math.ceil(upper) - 1}`;
  }
  if (lower == null) return `<${Number(upper ?? 0).toFixed(1)}`;
  if (upper == null) return `≥${lower.toFixed(1)}`;
  return `${lower.toFixed(1)}–${upper.toFixed(1)}`;
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

  const baseLandingBands = Array.isArray(input.baseLandingBands)
    ? input.baseLandingBands
        .map((band) => ({
          label: String(band?.label || ''),
          lower: finiteNumber(band?.lower),
          upper: finiteNumber(band?.upper),
          probability: finiteNumber(band?.probability) ?? 0,
        }))
        .filter((band) => band.label || band.lower != null || band.upper != null)
    : [];
  const isBaseLine = Math.abs(line - baseLine) < 0.001;
  let landingBands: AnalysisScenarioLandingBand[] = baseLandingBands;
  if (projection != null && (!isBaseLine || landingBands.length === 0)) {
    const stableBoundaries = landingBands
      .map((band) => band.upper)
      .filter((value): value is number => value != null)
      .sort((a, b) => a - b)
      .slice(0, -1);
    const quantileBoundaries = stableBoundaries.length > 0
      ? stableBoundaries
      : [
          projection - 1.2815515655446004 * posteriorStd,
          projection - 0.8416212335729143 * posteriorStd,
        ];
    const breaks = Array.from(new Set(
      quantileBoundaries
        .filter((boundary) => Number.isFinite(boundary) && boundary < line)
        .map((boundary) => Number(boundary.toFixed(8))),
    )).sort((a, b) => a - b);
    const boundaries = [...breaks, line];
    const displayAsInteger = baseLandingBands.some((band) => /[≤≥+]/.test(band.label));
    const probabilityFor = (lower: number | null, upper: number | null) => {
      const upperProbability = upper == null ? 1 : normalCdf((upper - projection) / posteriorStd);
      const lowerProbability = lower == null ? 0 : normalCdf((lower - projection) / posteriorStd);
      return clamp((upperProbability - lowerProbability) * 100, 0, 100);
    };
    const generated: AnalysisScenarioLandingBand[] = [];
    let previous: number | null = null;
    for (const boundary of boundaries) {
      if (previous != null && boundary <= previous) continue;
      generated.push({
        label: scenarioBandLabel(previous, boundary, displayAsInteger),
        lower: previous,
        upper: boundary,
        probability: Number(probabilityFor(previous, boundary).toFixed(1)),
      });
      previous = boundary;
    }
    generated.push({
      label: scenarioBandLabel(previous, null, displayAsInteger),
      lower: previous,
      upper: null,
      probability: Number(probabilityFor(previous, null).toFixed(1)),
    });
    landingBands = generated;
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
    landingBands,
    recommendation,
    edge,
    confidence,
    hasDynamicProbability,
    isBaseLine,
  };
}

function directionColor(direction: string) {
  return direction === 'OVER'
    ? Colors.success
    : direction === 'UNDER'
    ? Colors.error
    : Colors.textSecondary;
}

export default React.memo(function AnalysisScenarioPanel({
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
  onLineStep,
  onReset,
  embedded = false,
  compact = false,
}: Props) {
  const [draftLine, setDraftLine] = useState(String(line));
  const draftLineRef = useRef(String(line));
  const suppressBlurCommit = useRef(false);
  const suppressBlurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
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
    const next = line.toFixed(1);
    draftLineRef.current = next;
    setDraftLine(next);
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
    const parsed = Number(draftLineRef.current.replace(',', '.'));
    if (!Number.isFinite(parsed)) {
      const fallback = line.toFixed(1);
      draftLineRef.current = fallback;
      setDraftLine(fallback);
      return;
    }
    const next = Math.max(0, Math.round(parsed * 2) / 2);
    draftLineRef.current = next.toFixed(1);
    setDraftLine(draftLineRef.current);
    onLineChange(next);
  };

  const changeLine = (delta: number) => {
    const current = finiteNumber(draftLineRef.current) ?? line;
    const next = Math.max(0, Math.round((current + delta) * 2) / 2);
    draftLineRef.current = next.toFixed(1);
    setDraftLine(draftLineRef.current);
    if (onLineStep) {
      onLineStep(delta);
    } else {
      onLineChange(next);
    }
  };

  const markStepperPress = () => {
    suppressBlurCommit.current = true;
    if (suppressBlurTimer.current) clearTimeout(suppressBlurTimer.current);
  };
  const releaseStepperPress = () => {
    suppressBlurTimer.current = setTimeout(() => {
      suppressBlurCommit.current = false;
      suppressBlurTimer.current = null;
    }, 250);
  };

  if (compact) {
    return (
      <View style={styles.compactLineControl}>
        <View style={styles.compactLineCopy}>
          <Text style={styles.compactLineLabel}>LINE</Text>
          <Text style={styles.compactLineState}>
            {scenario.isBaseLine ? 'SET' : 'WHAT-IF'}
          </Text>
        </View>
        <View style={styles.compactStepper}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Decrease line"
            onPressIn={markStepperPress}
            onPressOut={releaseStepperPress}
            onPress={() => changeLine(-step)}
            style={({ pressed }) => [styles.compactStepButton, pressed && styles.pressed]}
          >
            <Ionicons name="remove" size={13} color={Colors.text} />
          </Pressable>
          <TextInput
            value={draftLine}
            onChangeText={(value) => {
              draftLineRef.current = value;
              setDraftLine(value);
            }}
            onBlur={() => {
              if (!suppressBlurCommit.current) commitDraft();
            }}
            onSubmitEditing={commitDraft}
            keyboardType="decimal-pad"
            returnKeyType="done"
            selectTextOnFocus
            style={styles.compactLineInput}
            accessibilityLabel="Scenario line"
          />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Increase line"
            onPressIn={markStepperPress}
            onPressOut={releaseStepperPress}
            onPress={() => changeLine(step)}
            style={({ pressed }) => [styles.compactStepButton, pressed && styles.pressed]}
          >
            <Ionicons name="add" size={13} color={Colors.text} />
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View style={[embedded ? styles.embeddedCard : styles.card, !embedded && { borderColor: accent + '55' }]}>
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
            onPressIn={markStepperPress}
            onPressOut={releaseStepperPress}
            onPress={() => changeLine(-step)}
            style={({ pressed }) => [styles.stepButton, pressed && styles.pressed]}
          >
            <Ionicons name="remove" size={16} color={Colors.text} />
          </Pressable>
          <TextInput
            value={draftLine}
            onChangeText={(value) => {
              draftLineRef.current = value;
              setDraftLine(value);
            }}
            onBlur={() => {
              if (!suppressBlurCommit.current) commitDraft();
            }}
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
            onPressIn={markStepperPress}
            onPressOut={releaseStepperPress}
            onPress={() => changeLine(step)}
            style={({ pressed }) => [styles.stepButton, pressed && styles.pressed]}
          >
            <Ionicons name="add" size={16} color={Colors.text} />
          </Pressable>
        </View>
      </View>

      {embedded && (
        <View style={styles.embeddedSummary}>
          <View>
            <Text style={[styles.embeddedDirection, { color: accent }]}>{direction}</Text>
            <Text style={styles.embeddedSummaryText}>{probability} scenario probability</Text>
          </View>
          <View style={styles.embeddedSummaryRight}>
            <Text style={styles.metricLabel}>PROJECTION {scenario.projection != null ? scenario.projection.toFixed(1) : '—'}</Text>
            <Text style={[styles.embeddedEdge, { color: accent }]}>{edgeText}</Text>
          </View>
        </View>
      )}

      {!embedded && <View style={styles.hero}>
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
      </View>}

      {!embedded && <View style={styles.probabilityRow}>
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
      </View>}

      {!embedded && scenario.pOver != null && scenario.pUnder != null && (
        <View style={styles.distribution}>
          <View style={[styles.distributionOver, { flex: Math.max(scenario.pOver, 1) }]} />
          <View style={[styles.distributionUnder, { flex: Math.max(scenario.pUnder, 1) }]} />
        </View>
      )}
      {!embedded && <View style={styles.distributionLabels}>
        <Text style={styles.distributionLabel}>OVER</Text>
        <Text style={styles.distributionLabel}>UNDER</Text>
      </View>}

      {!embedded && <View style={styles.footer}>
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
      </View>}
    </View>
  );
});

const styles = {
  embeddedCard: {
    backgroundColor: 'transparent',
    borderRadius: 0,
    borderWidth: 0,
    padding: 0,
    marginBottom: 0,
    gap: 12,
  },
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
    paddingVertical: 8,
    borderTopWidth: 1,
    borderBottomWidth: 1,
    borderColor: Colors.borderSubtle,
    backgroundColor: 'transparent',
  },
  lineEditorCopy: { flex: 1 },
  compactLineControl: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    gap: 5,
  },
  compactLineCopy: {
    alignItems: 'flex-end' as const,
    minWidth: 28,
  },
  compactLineLabel: {
    fontSize: 7,
    color: Colors.textTertiary,
    fontWeight: '900' as const,
    letterSpacing: 0.7,
  },
  compactLineState: {
    marginTop: 1,
    fontSize: 6,
    color: Colors.primary,
    fontWeight: '900' as const,
    letterSpacing: 0.4,
  },
  compactStepper: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    borderRadius: 5,
    backgroundColor: 'rgba(255,255,255,0.025)',
    overflow: 'hidden' as const,
  },
  compactStepButton: {
    width: 24,
    height: 27,
    alignItems: 'center' as const,
    justifyContent: 'center' as const,
  },
  compactLineInput: {
    width: 39,
    height: 27,
    paddingHorizontal: 2,
    textAlign: 'center' as const,
    color: Colors.text,
    fontSize: 11,
    fontWeight: '900' as const,
    borderLeftWidth: 1,
    borderRightWidth: 1,
    borderColor: Colors.borderSubtle,
  },
  lineLabel: {
    fontSize: 8,
    color: Colors.text,
    fontWeight: '900' as const,
    letterSpacing: 0.7,
  },
  lineHint: { marginTop: 2, fontSize: 8, color: Colors.textTertiary },
  embeddedSummary: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    justifyContent: 'space-between' as const,
    paddingTop: 2,
  },
  embeddedDirection: {
    fontSize: 22,
    lineHeight: 25,
    fontWeight: '900' as const,
    letterSpacing: 1,
  },
  embeddedSummaryText: {
    marginTop: 2,
    color: Colors.textSecondary,
    fontSize: 10,
    fontWeight: '700' as const,
  },
  embeddedSummaryRight: { alignItems: 'flex-end' as const, gap: 2 },
  embeddedEdge: { fontSize: 10, fontWeight: '800' as const },
  stepper: {
    flexDirection: 'row' as const,
    alignItems: 'center' as const,
    borderRadius: 0,
    overflow: 'hidden' as const,
    borderBottomWidth: 1,
    borderColor: Colors.borderSubtle,
    backgroundColor: 'transparent',
  },
  stepButton: {
    width: 28,
    height: 30,
    alignItems: 'center' as const,
    justifyContent: 'center' as const,
  },
  pressed: { opacity: 0.55 },
  lineInput: {
    width: 50,
    height: 30,
    paddingHorizontal: 4,
    textAlign: 'center' as const,
    color: Colors.text,
    fontSize: 14,
    fontWeight: '900' as const,
    borderLeftWidth: 1,
    borderRightWidth: 1,
    borderColor: Colors.borderSubtle,
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