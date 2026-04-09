import React, { useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  TextInput, ActivityIndicator, Alert, Platform, Modal, Image,
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

const INPUT_STYLE = Platform.OS === 'web' ? { outlineWidth: 0 } as object : {};

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Attempts', shots: 'Shots', shots_on_target: 'Shots on Target',
  goals: 'Goals', assists: 'Assists', key_passes: 'Key Passes',
  tackles: 'Tackles', saves: 'Saves', dribbles: 'Dribbles', crosses: 'Crosses',
  interceptions: 'Interceptions', blocks: 'Blocks', fouls_drawn: 'Fouls Drawn',
  fouls_committed: 'Fouls', clearances: 'Clearances', yellow_cards: 'Yellow Cards',
  shots_assisted: 'Shot Assists', duels_won: 'Duels Won', passes: 'Passes',
};

type Mode = 'scan' | 'manual';
type Phase = 'idle' | 'scanning' | 'detected' | 'analyzing' | 'result' | 'saved';

export default function ScanScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>('scan');
  const [phase, setPhase] = useState<Phase>('idle');

  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scannedImageUri, setScannedImageUri] = useState<string | null>(null);
  const [prediction, setPrediction] = useState<PredictionResult | null>(null);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [manualError, setManualError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // User-controlled venue override
  const [venueOverride, setVenueOverride] = useState<'home' | 'away'>('home');

  // Manual mode fields
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
  };

  const processImage = async (base64: string, uri: string) => {
    setScannedImageUri(uri);
    setPhase('scanning');
    setAnalyzeError(null);
    try {
      const scanned = await scanProp(base64, 'soccer');
      if (scanned.error || !scanned.playerName) {
        setAnalyzeError(scanned.error || 'Could not read prop slip. Try a clearer screenshot.');
        setPhase('idle');
        return;
      }
      // Set venue override from scan result, defaulting to 'home'
      const detectedVenue = (scanned.venue || 'home').toLowerCase();
      setVenueOverride(detectedVenue === 'away' ? 'away' : 'home');
      setScanResult(scanned);
      setPhase('detected');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      setAnalyzeError(e instanceof Error ? e.message : 'Failed to scan image');
      setPhase('idle');
    }
  };

  const handleGallery = async () => {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Permission needed', 'Allow photo library access to scan prop slips.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.85,
      base64: true,
    });
    if (result.canceled || !result.assets[0].base64) return;
    await processImage(result.assets[0].base64, result.assets[0].uri);
  };

  const runPredict = async (data: ScanResult, inManual = false) => {
    setPhase('analyzing');
    setAnalyzeError(null);
    setManualError(null);
    try {
      const req = {
        playerName: data.playerName,
        playerId: data.playerId || 0,
        teamId: data.teamId || 0,
        teamName: data.teamName || data.playerTeam || '',
        opponentId: data.opponentId || 0,
        opponentName: data.opponentName || '',
        venue: venueOverride,
        leagueId: data.leagueId || leagueId,
        propType: data.propType || propType,
        line: data.line || 0,
      };
      const result = await predict(req);
      if (result.error) {
        if (inManual) setManualError(result.error); else setAnalyzeError(result.error);
        setPhase(inManual ? 'idle' : 'detected');
        return;
      }
      setPrediction(result);
      setPhase('result');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Analysis failed — try again';
      if (inManual) setManualError(msg); else setAnalyzeError(msg);
      setPhase(inManual ? 'idle' : 'detected');
    }
  };

  const handleManualAnalyze = async () => {
    if (!playerQuery.trim()) { setManualError('Enter a player name to analyze.'); return; }
    if (!line.trim() || isNaN(parseFloat(line))) { setManualError('Enter a valid line value (e.g. 2.5).'); return; }
    setManualError(null);
    const data: ScanResult = {
      playerName: playerQuery.trim(),
      propType,
      line: parseFloat(line),
      leagueId,
    };
    setScanResult(data);
    await runPredict(data, true);
  };

  const handleSavePick = async () => {
    if (!session || !prediction || !scanResult) return;
    setSaving(true);
    setSaveError(null);
    try {
      await savePick(session.email, session.token, {
        playerName: prediction.playerName || scanResult.playerName || playerQuery,
        teamName: prediction.teamName || scanResult.teamName || scanResult.playerTeam,
        opponentName: prediction.opponentName || scanResult.opponentName,
        propType: prediction.propType || scanResult.propType || propType,
        line: prediction.line ?? scanResult.line ?? parseFloat(line),
        projection: prediction.projection ?? prediction.bayesianProjection,
        recommendation: prediction.recommendation,
        confidence: prediction.confidence,
        sport: 'soccer',
        player: {
          id: prediction.playerId || 0,
          name: prediction.playerName || scanResult.playerName || playerQuery,
          team: prediction.teamName || scanResult.teamName || scanResult.playerTeam || '',
        },
        _request: {
          teamId: prediction.teamId || scanResult.teamId || 0,
          opponentId: prediction.opponentId || scanResult.opponentId || 0,
          leagueId: prediction.leagueId || scanResult.leagueId || leagueId || 0,
          venue: venueOverride || 'home',
        },
      });
      setPhase('saved');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      qc.invalidateQueries({ queryKey: ['picks'] });
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Save failed — try again');
    } finally {
      setSaving(false);
    }
  };

  const recColor = prediction?.recommendation === 'OVER' ? Colors.success
    : prediction?.recommendation === 'UNDER' ? Colors.error
    : Colors.textSecondary;

  const confPct = prediction?.confidence != null
    ? (prediction.confidence > 1 ? Math.round(prediction.confidence) : Math.round(prediction.confidence * 100))
    : null;

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      <View style={styles.header}>
        <View style={styles.logoRow}>
          <Image source={require('../../assets/logo.png')} style={styles.logoImg} resizeMode="contain" />
          <View>
            <Text style={styles.logoText}>ReversePicks</Text>
            <Text style={styles.tagline}>AI Soccer Prop Analytics</Text>
          </View>
        </View>
      </View>

      <View style={styles.modeRow}>
        {(['scan', 'manual'] as Mode[]).map(m => (
          <TouchableOpacity
            key={m}
            style={[styles.modeTab, mode === m && styles.modeTabActive]}
            onPress={() => { setMode(m); reset(); Haptics.selectionAsync(); }}
          >
            <Ionicons
              name={m === 'scan' ? 'scan-outline' : 'search-outline'}
              size={14}
              color={mode === m ? Colors.primary : Colors.textSecondary}
            />
            <Text style={[styles.modeTabText, mode === m && styles.modeTabTextActive]}>
              {m === 'scan' ? 'Scan Slip' : 'Manual Search'}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <ScrollView
        contentContainerStyle={styles.body}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {/* ─── SCAN MODE ─── */}
        {mode === 'scan' && (
          <>
            {/* Idle: upload box */}
            {phase === 'idle' && (
              <View style={styles.uploadBox}>
                <Ionicons name="image-outline" size={44} color={Colors.textTertiary} />
                <Text style={styles.uploadTitle}>Scan a Prop Slip</Text>
                <Text style={styles.uploadSub}>Upload a screenshot of any soccer prop slip</Text>
                <TouchableOpacity style={styles.galleryBtnBig} onPress={handleGallery} activeOpacity={0.8}>
                  <Ionicons name="images-outline" size={18} color="#000" />
                  <Text style={styles.galleryBtnBigText}>Choose from Photos</Text>
                </TouchableOpacity>
                {analyzeError && (
                  <View style={styles.inlineError}>
                    <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                    <Text style={styles.inlineErrorText}>{analyzeError}</Text>
                  </View>
                )}
              </View>
            )}

            {/* Scanning */}
            {phase === 'scanning' && (
              <View style={styles.loadingCard}>
                {scannedImageUri && (
                  <Image source={{ uri: scannedImageUri }} style={styles.scannedThumb} resizeMode="cover" />
                )}
                <View style={styles.loadingRow}>
                  <ActivityIndicator color={Colors.primary} size="small" />
                  <Text style={styles.loadingText}>Grok Vision reading prop slip…</Text>
                </View>
              </View>
            )}

            {/* Detected: player card + venue toggle + RUN PREDICTION */}
            {(phase === 'detected' || phase === 'analyzing') && scanResult && (
              <>
                {scannedImageUri && (
                  <Image source={{ uri: scannedImageUri }} style={styles.scannedPreview} resizeMode="cover" />
                )}

                <Text style={styles.sectionLabel}>PROPS DETECTED</Text>

                <View style={styles.detectedCard}>
                  <View style={styles.detectedTop}>
                    <View style={styles.playerAvatarWrap}>
                      <Ionicons name="person-outline" size={22} color={Colors.textSecondary} />
                    </View>
                    <View style={styles.detectedInfo}>
                      <Text style={styles.detectedName}>{scanResult.playerName}</Text>
                      <View style={styles.badgeRow}>
                        {(scanResult.teamName || scanResult.playerTeam) && (
                          <View style={styles.teamBadge}>
                            <Text style={styles.teamBadgeText}>
                              {scanResult.teamName || scanResult.playerTeam}
                            </Text>
                          </View>
                        )}
                        <View style={styles.matchedBadge}>
                          <Text style={styles.matchedText}>MATCHED</Text>
                        </View>
                      </View>
                      {scanResult.opponentName && (
                        <Text style={styles.vsText}>vs {scanResult.opponentName}</Text>
                      )}
                    </View>
                  </View>

                  {/* VENUE TOGGLE */}
                  <View style={styles.venueRow}>
                    <Text style={styles.venueLabel}>VENUE</Text>
                    <View style={styles.venueToggle}>
                      <TouchableOpacity
                        style={[styles.venueOption, venueOverride === 'home' && styles.venueOptionActive]}
                        onPress={() => { setVenueOverride('home'); Haptics.selectionAsync(); }}
                      >
                        <Ionicons
                          name="home-outline"
                          size={13}
                          color={venueOverride === 'home' ? Colors.primary : Colors.textSecondary}
                        />
                        <Text style={[styles.venueOptionText, venueOverride === 'home' && styles.venueOptionTextActive]}>
                          HOME
                        </Text>
                      </TouchableOpacity>
                      <TouchableOpacity
                        style={[styles.venueOption, venueOverride === 'away' && styles.venueOptionActive]}
                        onPress={() => { setVenueOverride('away'); Haptics.selectionAsync(); }}
                      >
                        <Ionicons
                          name="airplane-outline"
                          size={13}
                          color={venueOverride === 'away' ? Colors.primary : Colors.textSecondary}
                        />
                        <Text style={[styles.venueOptionText, venueOverride === 'away' && styles.venueOptionTextActive]}>
                          AWAY
                        </Text>
                      </TouchableOpacity>
                    </View>
                  </View>

                  <View style={styles.detectedStats}>
                    <View style={styles.detectedStat}>
                      <Text style={styles.detectedStatLabel}>PROP</Text>
                      <Text style={styles.detectedStatVal}>
                        {PROP_LABELS[scanResult.propType || ''] || scanResult.propType?.replace(/_/g, ' ') || '—'}
                      </Text>
                    </View>
                    <View style={styles.detectedStatDivider} />
                    <View style={styles.detectedStat}>
                      <Text style={styles.detectedStatLabel}>LINE</Text>
                      <Text style={styles.detectedStatVal}>{scanResult.line ?? '—'}</Text>
                    </View>
                    <View style={styles.detectedStatDivider} />
                    <View style={styles.detectedStat}>
                      <Text style={styles.detectedStatLabel}>LEAGUE</Text>
                      <Text style={styles.detectedStatVal} numberOfLines={1}>
                        {LEAGUES.find(l => l.id === scanResult.leagueId)?.name || 'Auto'}
                      </Text>
                    </View>
                  </View>
                </View>

                {/* Inline error */}
                {analyzeError && (
                  <View style={styles.inlineError}>
                    <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                    <Text style={styles.inlineErrorText}>{analyzeError}</Text>
                  </View>
                )}

                <TouchableOpacity
                  style={[styles.predictBtn, phase === 'analyzing' && styles.predictBtnLoading]}
                  onPress={() => runPredict(scanResult)}
                  disabled={phase === 'analyzing'}
                  activeOpacity={0.85}
                >
                  {phase === 'analyzing' ? (
                    <>
                      <ActivityIndicator color="#000" size="small" />
                      <Text style={styles.predictBtnText}>Analyzing…</Text>
                    </>
                  ) : (
                    <>
                      <Ionicons name="flash" size={16} color="#000" />
                      <Text style={styles.predictBtnText}>RUN PREDICTION</Text>
                    </>
                  )}
                </TouchableOpacity>

                <TouchableOpacity onPress={reset} style={styles.rescanBtn}>
                  <Ionicons name="refresh-outline" size={14} color={Colors.textSecondary} />
                  <Text style={styles.rescanText}>Scan Different Slip</Text>
                </TouchableOpacity>
              </>
            )}
          </>
        )}

        {/* ─── MANUAL MODE ─── */}
        {mode === 'manual' && phase !== 'result' && phase !== 'saved' && (
          <View style={styles.manualForm}>
            <Text style={styles.fieldLabel}>Player Name</Text>
            <TextInput
              style={[styles.textInput, INPUT_STYLE]}
              placeholder="e.g. Kevin De Bruyne"
              placeholderTextColor={Colors.textTertiary}
              value={playerQuery}
              onChangeText={setPlayerQuery}
              autoCorrect={false}
            />

            <Text style={styles.fieldLabel}>League</Text>
            <TouchableOpacity style={styles.pickerBtn} onPress={() => setShowLeaguePicker(true)}>
              <Text style={styles.pickerBtnText}>{LEAGUES.find(l => l.id === leagueId)?.name || 'Select'}</Text>
              <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
            </TouchableOpacity>

            <Text style={styles.fieldLabel}>Prop Type</Text>
            <TouchableOpacity style={styles.pickerBtn} onPress={() => setShowPropPicker(true)}>
              <Text style={styles.pickerBtnText}>{PROP_TYPES.find(p => p.value === propType)?.label || 'Select'}</Text>
              <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
            </TouchableOpacity>

            <Text style={styles.fieldLabel}>Line Value</Text>
            <TextInput
              style={[styles.textInput, INPUT_STYLE]}
              placeholder="e.g. 2.5"
              placeholderTextColor={Colors.textTertiary}
              value={line}
              onChangeText={setLine}
              keyboardType="decimal-pad"
            />

            <Text style={styles.fieldLabel}>Venue</Text>
            <View style={styles.venueToggle}>
              <TouchableOpacity
                style={[styles.venueOption, venueOverride === 'home' && styles.venueOptionActive]}
                onPress={() => { setVenueOverride('home'); Haptics.selectionAsync(); }}
              >
                <Ionicons name="home-outline" size={13} color={venueOverride === 'home' ? Colors.primary : Colors.textSecondary} />
                <Text style={[styles.venueOptionText, venueOverride === 'home' && styles.venueOptionTextActive]}>HOME</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.venueOption, venueOverride === 'away' && styles.venueOptionActive]}
                onPress={() => { setVenueOverride('away'); Haptics.selectionAsync(); }}
              >
                <Ionicons name="airplane-outline" size={13} color={venueOverride === 'away' ? Colors.primary : Colors.textSecondary} />
                <Text style={[styles.venueOptionText, venueOverride === 'away' && styles.venueOptionTextActive]}>AWAY</Text>
              </TouchableOpacity>
            </View>

            {manualError && (
              <View style={styles.inlineError}>
                <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                <Text style={styles.inlineErrorText}>{manualError}</Text>
              </View>
            )}

            <TouchableOpacity
              style={[styles.predictBtn, phase === 'analyzing' && styles.predictBtnLoading]}
              onPress={handleManualAnalyze}
              disabled={phase === 'analyzing'}
              activeOpacity={0.85}
            >
              {phase === 'analyzing' ? (
                <>
                  <ActivityIndicator color="#000" size="small" />
                  <Text style={styles.predictBtnText}>Analyzing…</Text>
                </>
              ) : (
                <>
                  <Ionicons name="analytics-outline" size={16} color="#000" />
                  <Text style={styles.predictBtnText}>Analyze</Text>
                </>
              )}
            </TouchableOpacity>
          </View>
        )}

        {/* ─── RESULT: Full Analysis ─── */}
        {phase === 'result' && prediction && (
          <>
            <View style={styles.analysisCard}>
              {/* Header */}
              <View style={styles.analysisHeader}>
                <View style={styles.analysisPlayerInfo}>
                  <Text style={styles.analysisPlayer} numberOfLines={1}>
                    {prediction.playerName}
                  </Text>
                  <Text style={styles.analysisTeam} numberOfLines={1}>
                    {[prediction.teamName, prediction.opponentName ? `vs ${prediction.opponentName}` : ''].filter(Boolean).join('  ·  ')}
                  </Text>
                  <Text style={styles.analysisVenue}>
                    {venueOverride.toUpperCase()} · {PROP_LABELS[prediction.propType || ''] || prediction.propType}
                  </Text>
                </View>
                {prediction.recommendation && (
                  <View style={[styles.recBadge, {
                    backgroundColor: prediction.recommendation === 'OVER' ? Colors.successDim : Colors.errorDim
                  }]}>
                    <Text style={[styles.recText, { color: recColor }]}>{prediction.recommendation}</Text>
                  </View>
                )}
              </View>

              <View style={styles.analysisDivider} />

              {/* Stats Row */}
              <View style={styles.analysisStats}>
                <View style={styles.analysisStat}>
                  <Text style={styles.analysisStatLabel}>Line</Text>
                  <Text style={styles.analysisStatVal}>{prediction.line ?? '—'}</Text>
                  <Text style={styles.analysisStatSub}>SET</Text>
                </View>
                <View style={styles.analysisStatDivider} />
                <View style={styles.analysisStat}>
                  <Text style={styles.analysisStatLabel}>Projection</Text>
                  <Text style={[styles.analysisStatVal, { color: Colors.primary }]}>
                    {prediction.projection?.toFixed(1) ?? prediction.bayesianProjection?.toFixed(1) ?? '—'}
                  </Text>
                  <Text style={styles.analysisStatSub}>REVERSE FORMULA</Text>
                </View>
                <View style={styles.analysisStatDivider} />
                <View style={styles.analysisStat}>
                  <Text style={styles.analysisStatLabel}>Confidence</Text>
                  <Text style={[styles.analysisStatVal, { color: recColor }]}>
                    {confPct != null ? `${confPct}%` : '—'}
                  </Text>
                  <Text style={styles.analysisStatSub}>{prediction.confidenceLevel?.toUpperCase() || 'SCORE'}</Text>
                </View>
              </View>

              {/* Bayesian breakdown */}
              {(prediction.priorMean != null || prediction.momentumEffect != null) && (
                <>
                  <View style={styles.analysisDivider} />
                  <View style={styles.bayesRow}>
                    {prediction.priorMean != null && (
                      <View style={styles.bayesStat}>
                        <Text style={styles.bayesLabel}>SEASON AVG</Text>
                        <Text style={styles.bayesVal}>{prediction.priorMean.toFixed(1)}</Text>
                      </View>
                    )}
                    {prediction.momentumEffect != null && (
                      <View style={styles.bayesStat}>
                        <Text style={styles.bayesLabel}>MOMENTUM</Text>
                        <Text style={[styles.bayesVal, {
                          color: prediction.momentumEffect > 0 ? Colors.success : prediction.momentumEffect < 0 ? Colors.error : Colors.text
                        }]}>
                          {prediction.momentumEffect > 0 ? '+' : ''}{prediction.momentumEffect.toFixed(1)}
                        </Text>
                      </View>
                    )}
                    {prediction.momentumLabel && (
                      <View style={styles.bayesStat}>
                        <Text style={styles.bayesLabel}>FORM</Text>
                        <Text style={[styles.bayesVal, {
                          color: prediction.momentumLabel === 'HOT' ? Colors.success
                            : prediction.momentumLabel === 'COLD' ? Colors.error
                            : Colors.textSecondary
                        }]}>{prediction.momentumLabel}</Text>
                      </View>
                    )}
                    {prediction.streakFlag && prediction.streakFlag !== 'NONE' && (
                      <View style={styles.bayesStat}>
                        <Text style={styles.bayesLabel}>STREAK</Text>
                        <Text style={[styles.bayesVal, { color: Colors.primary }]}>{prediction.streakFlag.replace('_', ' ')}</Text>
                      </View>
                    )}
                  </View>
                </>
              )}

              {/* Confidence Interval */}
              {prediction.confidenceInterval && (
                <>
                  <View style={styles.analysisDivider} />
                  <View style={styles.ciRow}>
                    <Text style={styles.ciLabel}>80% RANGE</Text>
                    <Text style={styles.ciVal}>
                      {prediction.confidenceInterval[0].toFixed(1)} — {prediction.confidenceInterval[1].toFixed(1)}
                    </Text>
                  </View>
                </>
              )}

              {/* AI Reasoning */}
              {prediction.reasoning && (
                <>
                  <View style={styles.analysisDivider} />
                  <View style={styles.reasoningBox}>
                    <View style={styles.reasoningHeader}>
                      <Ionicons name="bulb-outline" size={13} color={Colors.primary} />
                      <Text style={styles.reasoningLabel}>AI ANALYSIS</Text>
                    </View>
                    <Text style={styles.reasoningText}>{prediction.reasoning}</Text>
                  </View>
                </>
              )}
            </View>

            {/* ─── GAME LOG TILES ─── */}
            {prediction.gameLogs && prediction.gameLogs.length > 0 && (
              <View style={styles.gameLogsCard}>
                <View style={styles.gameLogsHeader}>
                  <Text style={styles.gameLogsTitle}>GAME LOG</Text>
                  {prediction.sampleSize != null && (
                    <View style={styles.gameLogsBadge}>
                      <Text style={styles.gameLogsBadgeText}>{prediction.sampleSize} games</Text>
                    </View>
                  )}
                  {prediction.hitRates && (
                    <Text style={styles.hitRateText}>
                      {prediction.hitRates.overPct}% OVER · {prediction.hitRates.underPct}% UNDER
                    </Text>
                  )}
                </View>
                <ScrollView
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  contentContainerStyle={styles.tilesRow}
                >
                  {prediction.gameLogs.map((g, i) => {
                    const isOver = g.value != null && prediction.line != null && g.value >= prediction.line;
                    const dateStr = g.date ? g.date.slice(5) : '??-??';
                    const oppRaw = g.opponent || '?';
                    const oppShort = oppRaw.length > 7 ? oppRaw.slice(0, 6) + '…' : oppRaw;
                    return (
                      <TouchableOpacity
                        key={i}
                        style={[styles.gameTile, isOver ? styles.gameTileOver : styles.gameTileUnder]}
                        onPress={() => Haptics.selectionAsync()}
                        activeOpacity={0.7}
                      >
                        <Text style={styles.gameTileVenue}>{g.venue === 'home' ? 'H' : 'A'}</Text>
                        <Text style={styles.gameTileDate}>{dateStr}</Text>
                        <Text style={styles.gameTileOpp} numberOfLines={1}>{oppShort}</Text>
                        <Text style={[styles.gameTileVal, { color: isOver ? Colors.success : Colors.error }]}>
                          {g.value != null ? String(g.value) : '—'}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                </ScrollView>
                {(prediction.homeAvg != null || prediction.awayAvg != null) && (
                  <View style={styles.avgRow}>
                    {prediction.homeAvg != null && (
                      <Text style={styles.avgText}>HOME AVG {prediction.homeAvg.toFixed(1)}</Text>
                    )}
                    {prediction.awayAvg != null && (
                      <Text style={styles.avgText}>AWAY AVG {prediction.awayAvg.toFixed(1)}</Text>
                    )}
                  </View>
                )}
              </View>
            )}

            {saveError && (
              <View style={styles.inlineError}>
                <Ionicons name="alert-circle-outline" size={14} color={Colors.error} />
                <Text style={styles.inlineErrorText}>{saveError}</Text>
              </View>
            )}

            <TouchableOpacity
              style={[styles.saveBtn, saving && { opacity: 0.6 }]}
              onPress={handleSavePick}
              disabled={saving}
              activeOpacity={0.85}
            >
              {saving
                ? <ActivityIndicator color="#000" size="small" />
                : <>
                    <Ionicons name="bookmark" size={16} color="#000" />
                    <Text style={styles.saveBtnText}>Save to My Picks</Text>
                  </>
              }
            </TouchableOpacity>

            <TouchableOpacity style={styles.newBtn} onPress={reset}>
              <Text style={styles.newBtnText}>Analyze Another</Text>
            </TouchableOpacity>
          </>
        )}

        {/* ─── SAVED ─── */}
        {phase === 'saved' && (
          <View style={styles.savedState}>
            <View style={styles.savedCheck}>
              <Ionicons name="checkmark" size={36} color="#000" />
            </View>
            <Text style={styles.savedTitle}>Pick Saved!</Text>
            <Text style={styles.savedSub}>
              {prediction?.recommendation} · {prediction?.playerName}{'\n'}
              {PROP_LABELS[prediction?.propType || ''] || prediction?.propType} · Line {prediction?.line}
            </Text>
            <TouchableOpacity style={styles.viewPicksBtn} onPress={() => router.push('/(tabs)/picks')} activeOpacity={0.85}>
              <Ionicons name="bookmark" size={16} color="#000" />
              <Text style={styles.viewPicksBtnText}>View in My Picks</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.newBtn} onPress={reset}>
              <Text style={styles.newBtnText}>Analyze Another</Text>
            </TouchableOpacity>
          </View>
        )}
      </ScrollView>

      {/* Prop Picker Modal */}
      <Modal visible={showPropPicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setShowPropPicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>Prop Type</Text>
            <ScrollView>
              {PROP_TYPES.map(p => (
                <TouchableOpacity
                  key={p.value}
                  style={[styles.modalItem, p.value === propType && styles.modalItemActive]}
                  onPress={() => { setPropType(p.value); setShowPropPicker(false); }}
                >
                  <Text style={[styles.modalItemText, p.value === propType && styles.modalItemTextActive]}>{p.label}</Text>
                  {p.value === propType && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* League Picker Modal */}
      <Modal visible={showLeaguePicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setShowLeaguePicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>League</Text>
            <ScrollView>
              {LEAGUES.map(l => (
                <TouchableOpacity
                  key={l.id}
                  style={[styles.modalItem, l.id === leagueId && styles.modalItemActive]}
                  onPress={() => { setLeagueId(l.id); setShowLeaguePicker(false); }}
                >
                  <Text style={[styles.modalItemText, l.id === leagueId && styles.modalItemTextActive]}>{l.name}</Text>
                  {l.id === leagueId && <Ionicons name="checkmark" size={16} color={Colors.primary} />}
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: { paddingHorizontal: 20, paddingBottom: 12 },
  logoRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  logoImg: { width: 36, height: 36 },
  logoText: { fontSize: 20, fontWeight: '800', color: Colors.text, letterSpacing: -0.3 },
  tagline: { fontSize: 11, color: Colors.primary, marginTop: 1, letterSpacing: 0.5, fontWeight: '600' },
  modeRow: {
    flexDirection: 'row',
    marginHorizontal: 20,
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    padding: 3,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  modeTab: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    paddingVertical: 9, gap: 6, borderRadius: 9,
  },
  modeTabActive: { backgroundColor: Colors.primaryDim },
  modeTabText: { fontSize: 13, color: Colors.textSecondary, fontWeight: '600' },
  modeTabTextActive: { color: Colors.primary },
  body: { paddingHorizontal: 20, paddingBottom: 40 },

  /* Upload box */
  uploadBox: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    padding: 32, alignItems: 'center', borderWidth: 1.5,
    borderColor: Colors.border, borderStyle: 'dashed', gap: 10,
  },
  uploadTitle: { fontSize: 18, fontWeight: '700', color: Colors.text },
  uploadSub: { fontSize: 13, color: Colors.textSecondary, textAlign: 'center' },
  galleryBtnBig: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: Colors.primary, paddingVertical: 13, paddingHorizontal: 28,
    borderRadius: Colors.radius, marginTop: 8,
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4, shadowRadius: 10, elevation: 6,
  },
  galleryBtnBigText: { color: '#000', fontWeight: '800', fontSize: 15 },

  /* Inline error */
  inlineError: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    backgroundColor: Colors.errorDim, borderRadius: Colors.radius,
    padding: 12, marginTop: 10, borderWidth: 1, borderColor: Colors.error + '40',
  },
  inlineErrorText: { color: Colors.error, fontSize: 13, flex: 1, lineHeight: 18 },

  /* Loading */
  loadingCard: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    overflow: 'hidden', borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  scannedThumb: { width: '100%', height: 160 },
  scannedPreview: { width: '100%', height: 180, borderRadius: Colors.radius, marginBottom: 16 },
  loadingRow: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 16 },
  loadingText: { color: Colors.textSecondary, fontSize: 14 },

  /* Detected card */
  sectionLabel: {
    fontSize: 11, fontWeight: '700', color: Colors.primary,
    letterSpacing: 1.5, marginBottom: 10,
  },
  detectedCard: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    borderWidth: 1, borderColor: Colors.borderSubtle, overflow: 'hidden', marginBottom: 14,
  },
  detectedTop: { flexDirection: 'row', alignItems: 'flex-start', padding: 16, gap: 12 },
  playerAvatarWrap: {
    width: 44, height: 44, borderRadius: 22, backgroundColor: Colors.cardSecondary,
    alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  detectedInfo: { flex: 1, gap: 6 },
  detectedName: { fontSize: 18, fontWeight: '800', color: Colors.text },
  badgeRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', alignItems: 'center' },
  teamBadge: { backgroundColor: Colors.cardSecondary, borderRadius: 6, paddingHorizontal: 10, paddingVertical: 4 },
  teamBadgeText: { fontSize: 12, color: Colors.textSecondary, fontWeight: '600' },
  matchedBadge: {
    backgroundColor: Colors.primaryDim, borderRadius: 6, paddingHorizontal: 10, paddingVertical: 4,
    borderWidth: 1, borderColor: Colors.border,
  },
  matchedText: { fontSize: 11, color: Colors.primary, fontWeight: '700', letterSpacing: 0.5 },
  vsText: { fontSize: 12, color: Colors.textTertiary },

  /* Venue toggle */
  venueRow: {
    flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16,
    paddingBottom: 14, gap: 12,
  },
  venueLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1 },
  venueToggle: {
    flexDirection: 'row', backgroundColor: Colors.cardSecondary,
    borderRadius: 10, padding: 3, gap: 2, flex: 1,
    borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  venueOption: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    paddingVertical: 8, gap: 5, borderRadius: 8,
  },
  venueOptionActive: { backgroundColor: Colors.primaryDim },
  venueOptionText: { fontSize: 12, color: Colors.textSecondary, fontWeight: '700', letterSpacing: 0.5 },
  venueOptionTextActive: { color: Colors.primary },

  detectedStats: { flexDirection: 'row', borderTopWidth: 1, borderTopColor: Colors.borderSubtle },
  detectedStat: { flex: 1, padding: 14, alignItems: 'center', gap: 4 },
  detectedStatLabel: { fontSize: 9, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1 },
  detectedStatVal: { fontSize: 14, fontWeight: '700', color: Colors.text, textAlign: 'center' },
  detectedStatDivider: { width: 1, backgroundColor: Colors.borderSubtle, marginVertical: 10 },

  /* Prediction button */
  predictBtn: {
    backgroundColor: Colors.primary, borderRadius: Colors.radius, height: 52,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.4, shadowRadius: 14, elevation: 8,
  },
  predictBtnLoading: { opacity: 0.8 },
  predictBtnText: { color: '#000', fontWeight: '800', fontSize: 16, letterSpacing: 0.5 },
  rescanBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    justifyContent: 'center', paddingVertical: 14,
  },
  rescanText: { color: Colors.textSecondary, fontSize: 13 },

  /* Manual form */
  manualForm: { gap: 8 },
  fieldLabel: {
    fontSize: 11, color: Colors.textSecondary, fontWeight: '700',
    letterSpacing: 0.8, marginBottom: 4, marginTop: 8, textTransform: 'uppercase',
  },
  textInput: {
    backgroundColor: Colors.card, borderRadius: Colors.radius, borderWidth: 1,
    borderColor: Colors.borderSubtle, color: Colors.text, fontSize: 15,
    paddingHorizontal: 14, height: 48,
  },
  pickerBtn: {
    backgroundColor: Colors.card, borderRadius: Colors.radius, borderWidth: 1,
    borderColor: Colors.borderSubtle, paddingHorizontal: 14, height: 48,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
  },
  pickerBtnText: { color: Colors.text, fontSize: 15 },

  /* Analysis card */
  analysisCard: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    borderWidth: 1, borderColor: Colors.borderSubtle, overflow: 'hidden', marginBottom: 14,
  },
  analysisHeader: {
    flexDirection: 'row', justifyContent: 'space-between',
    alignItems: 'flex-start', padding: 18,
  },
  analysisPlayerInfo: { flex: 1, marginRight: 12 },
  analysisPlayer: { fontSize: 20, fontWeight: '800', color: Colors.text },
  analysisTeam: { fontSize: 12, color: Colors.textSecondary, marginTop: 3 },
  analysisVenue: { fontSize: 11, color: Colors.textTertiary, marginTop: 3, letterSpacing: 0.5 },
  recBadge: { paddingHorizontal: 14, paddingVertical: 7, borderRadius: 10 },
  recText: { fontSize: 14, fontWeight: '800', letterSpacing: 0.5 },
  analysisDivider: { height: 1, backgroundColor: Colors.borderSubtle },
  analysisStats: { flexDirection: 'row', paddingVertical: 4 },
  analysisStat: { flex: 1, alignItems: 'center', padding: 16, gap: 4 },
  analysisStatLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '600' },
  analysisStatVal: { fontSize: 22, fontWeight: '800', color: Colors.text },
  analysisStatSub: { fontSize: 9, color: Colors.textTertiary, letterSpacing: 0.8 },
  analysisStatDivider: { width: 1, backgroundColor: Colors.borderSubtle, marginVertical: 14 },

  /* Bayesian breakdown */
  bayesRow: { flexDirection: 'row', paddingHorizontal: 16, paddingVertical: 12, gap: 0 },
  bayesStat: { flex: 1, alignItems: 'center', gap: 3 },
  bayesLabel: { fontSize: 9, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.8 },
  bayesVal: { fontSize: 13, fontWeight: '700', color: Colors.text },

  /* Confidence interval */
  ciRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 10,
  },
  ciLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.8 },
  ciVal: { fontSize: 13, color: Colors.textSecondary, fontWeight: '600' },

  /* Reasoning */
  reasoningBox: { padding: 16, gap: 8 },
  reasoningHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  reasoningLabel: { fontSize: 10, color: Colors.primary, fontWeight: '700', letterSpacing: 1.5 },
  reasoningText: { fontSize: 13, color: Colors.textSecondary, lineHeight: 20 },

  /* Game Log Tiles */
  gameLogsCard: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    padding: 16,
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    marginTop: 12,
    gap: 12,
  },
  gameLogsHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  gameLogsTitle: { fontSize: 10, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 1.5 },
  gameLogsBadge: {
    backgroundColor: Colors.cardSecondary,
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: 20,
  },
  gameLogsBadgeText: { fontSize: 10, color: Colors.textSecondary, fontWeight: '600' },
  hitRateText: { fontSize: 10, color: Colors.textSecondary, marginLeft: 'auto' as unknown as number },
  tilesRow: { gap: 6, paddingBottom: 2 },
  gameTile: {
    width: 70,
    borderRadius: 10,
    padding: 8,
    alignItems: 'center',
    gap: 3,
    borderWidth: 1,
  },
  gameTileOver: {
    backgroundColor: 'rgba(57,255,20,0.07)',
    borderColor: 'rgba(57,255,20,0.25)',
  },
  gameTileUnder: {
    backgroundColor: 'rgba(255,59,48,0.07)',
    borderColor: 'rgba(255,59,48,0.2)',
  },
  gameTileVenue: { fontSize: 8, fontWeight: '800', color: Colors.textTertiary, letterSpacing: 1 },
  gameTileDate: { fontSize: 9, color: Colors.textSecondary },
  gameTileOpp: { fontSize: 8, color: Colors.textSecondary, textAlign: 'center' },
  gameTileVal: { fontSize: 17, fontWeight: '800', lineHeight: 20 },
  avgRow: { flexDirection: 'row', gap: 16 },
  avgText: { fontSize: 10, color: Colors.textSecondary, fontWeight: '600', letterSpacing: 0.5 },

  /* Save/New buttons */
  saveBtn: {
    backgroundColor: Colors.primary, borderRadius: Colors.radius, height: 52,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    marginBottom: 10,
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3, shadowRadius: 10, elevation: 6,
  },
  saveBtnText: { color: '#000', fontWeight: '800', fontSize: 16 },
  newBtn: { alignItems: 'center', paddingVertical: 14 },
  newBtnText: { color: Colors.textSecondary, fontSize: 14, fontWeight: '600' },

  /* Saved state */
  savedState: { alignItems: 'center', paddingTop: 30, gap: 14 },
  savedCheck: {
    width: 72, height: 72, borderRadius: 36, backgroundColor: Colors.primary,
    alignItems: 'center', justifyContent: 'center',
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.4, shadowRadius: 16, elevation: 10,
  },
  savedTitle: { fontSize: 24, fontWeight: '800', color: Colors.text },
  savedSub: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center', lineHeight: 22 },
  viewPicksBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: Colors.primary, borderRadius: Colors.radius,
    paddingVertical: 14, paddingHorizontal: 32, marginTop: 8,
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3, shadowRadius: 10, elevation: 6,
  },
  viewPicksBtnText: { color: '#000', fontWeight: '800', fontSize: 15 },

  /* Modals */
  modalOverlay: { flex: 1, backgroundColor: Colors.overlay, justifyContent: 'flex-end' },
  modalSheet: {
    backgroundColor: Colors.card,
    borderTopLeftRadius: Colors.radiusLg, borderTopRightRadius: Colors.radiusLg,
    padding: 20, maxHeight: '70%',
  },
  modalTitle: { fontSize: 16, fontWeight: '700', color: Colors.text, marginBottom: 14 },
  modalItem: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: Colors.border,
  },
  modalItemActive: { backgroundColor: Colors.primaryDim, borderRadius: 8, paddingHorizontal: 10 },
  modalItemText: { fontSize: 15, color: Colors.text },
  modalItemTextActive: { color: Colors.primary, fontWeight: '600' },
});
