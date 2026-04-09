import React, { useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  TextInput, ActivityIndicator, Alert, Platform, Modal, Image, Dimensions,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import Colors from '@/constants/colors';
import { useQueryClient } from '@tanstack/react-query';
import { scanProp, predict, savePick, PROP_TYPES, LEAGUES, PredictionResult, ScanResult } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

const SCREEN_W = Dimensions.get('window').width;
const INPUT_STYLE = Platform.OS === 'web' ? { outlineWidth: 0 } as object : {};

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Attempts', shots: 'Shots', shots_on_target: 'Shots on Target',
  goals: 'Goals', assists: 'Assists', key_passes: 'Key Passes',
  tackles: 'Tackles', saves: 'Saves', dribbles: 'Dribbles', crosses: 'Crosses',
  interceptions: 'Interceptions', blocks: 'Blocks', fouls_drawn: 'Fouls Drawn',
  fouls_committed: 'Fouls', clearances: 'Clearances', yellow_cards: 'Yellow Cards',
  shots_assisted: 'Shot Assists', duels_won: 'Duels Won', passes: 'Passes',
};

const statLabel = (prop?: string) => PROP_LABELS[prop || ''] || (prop || '').replace(/_/g, ' ');

export default function ScanScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const qc = useQueryClient();
  const [mode, setMode] = useState<'scan' | 'manual'>('scan');
  const [phase, setPhase] = useState<'idle' | 'scanning' | 'detected' | 'analyzing' | 'result' | 'saved'>('idle');
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scannedImageUri, setScannedImageUri] = useState<string | null>(null);
  const [prediction, setPrediction] = useState<PredictionResult | null>(null);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [manualError, setManualError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [venueOverride, setVenueOverride] = useState<'home' | 'away'>('home');
  const [gameLogFilter, setGameLogFilter] = useState<'all' | 'home' | 'away'>('all');
  const [playerQuery, setPlayerQuery] = useState('');
  const [propType, setPropType] = useState(PROP_TYPES[0].value);
  const [line, setLine] = useState('');
  const [leagueId, setLeagueId] = useState(39);
  const [showPropPicker, setShowPropPicker] = useState(false);
  const [showLeaguePicker, setShowLeaguePicker] = useState(false);

  const topPad = Platform.OS === 'web' ? 67 : insets.top;

  const reset = () => {
    setPhase('idle');
    setScanResult(null);
    setScannedImageUri(null);
    setPrediction(null);
    setAnalyzeError(null);
    setManualError(null);
    setSaveError(null);
    setPlayerQuery('');
    setLine('');
    setVenueOverride('home');
    setGameLogFilter('all');
  };

  const handleAnalysisBadge = (label: string, value: string | number | null | undefined) => (
    <View style={styles.analysisStat}>
      <Text style={styles.analysisStatLabel}>{label}</Text>
      <Text style={styles.analysisStatVal}>{value == null ? '—' : value}</Text>
      <Text style={styles.analysisStatSub}>CONTEXT</Text>
    </View>
  );

  const renderAnalysisSummary = () => {
    if (!prediction?.analysisSummary) return null;
    const s = prediction.analysisSummary;
    return (
      <View style={styles.summaryCard}>
        <View style={styles.summaryHeader}>
          <Ionicons name="analytics-outline" size={13} color={Colors.primary} />
          <Text style={styles.summaryTitle}>ANALYSIS BREAKDOWN</Text>
        </View>
        <View style={styles.summaryGrid}>
          <View style={styles.summaryItem}>
            <Text style={styles.summaryLabel}>{s.statLabel || 'STAT'}</Text>
            <Text style={styles.summaryValue}>{s.venueAverage != null ? s.venueAverage.toFixed(1) : '—'}</Text>
            <Text style={styles.summarySub}>{(s.venue || 'venue').toUpperCase()} AVG</Text>
          </View>
          <View style={styles.summaryItem}>
            <Text style={styles.summaryLabel}>Venue Samples</Text>
            <Text style={styles.summaryValue}>{s.venueSampleSize ?? '—'}</Text>
            <Text style={styles.summarySub}>{(s.venue || 'venue').toUpperCase()}</Text>
          </View>
          <View style={styles.summaryItem}>
            <Text style={styles.summaryLabel}>Opponent Allows</Text>
            <Text style={styles.summaryValue}>{s.opponentAllowedAverage != null ? s.opponentAllowedAverage.toFixed(1) : '—'}</Text>
            <Text style={styles.summarySub}>OPP AVG</Text>
          </View>
          {prediction.propType === 'saves' && (
            <>
              <View style={styles.summaryItem}>
                <Text style={styles.summaryLabel}>GK Save Rate</Text>
                <Text style={styles.summaryValue}>{s.goalkeeperSaveRate != null ? `${s.goalkeeperSaveRate.toFixed(1)}%` : '—'}</Text>
                <Text style={styles.summarySub}>{s.goalkeeperSaveSample ?? 0} GAMES</Text>
              </View>
              <View style={styles.summaryItem}>
                <Text style={styles.summaryLabel}>Opponent SoT</Text>
                <Text style={styles.summaryValue}>{s.opponentShotsOnTarget != null ? s.opponentShotsOnTarget.toFixed(1) : '—'}</Text>
                <Text style={styles.summarySub}>AGAINST</Text>
              </View>
            </>
          )}
        </View>
      </View>
    );
  };

  const handleManualAnalyze = async () => {};
  const handleSavePick = async () => {};

  return (
    <View style={{ flex: 1 }} />
  );
}
