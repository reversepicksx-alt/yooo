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
  const [gameLogFilter, setGameLogFilter] = useState<'all' | 'home' | 'away'>('all');

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
    setGameLogFilter('all');
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
        confidenceLevel: prediction.confidenceLevel,
        position: prediction.playerPosition || undefined,
        role: prediction.playerRole || undefined,
        sport: 'soccer',
        player: {
          id: prediction.playerId || 0,
          name: prediction.playerName || scanResult.playerName || playerQuery,
          team: prediction.teamName || scanResult.teamName || scanResult.playerTeam || '',
          position: prediction.playerPosition || undefined,
          role: prediction.playerRole || undefined,
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
        <Image source={require('../../assets/logo.png')} style={styles.logoImg} resizeMode="contain" />
        <Text style={styles.logoText}>ReversePicks</Text>
        <Text style={styles.tagline}>AI Soccer Prop Analytics</Text>
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
                    {prediction.playerPosition ? `  ·  ${prediction.playerPosition}` : ''}
                    {prediction.playerRole ? ` (${prediction.playerRole})` : ''}
                  </Text>
                  {prediction.matchContext && (prediction.matchContext.league || prediction.matchContext.round) && (
                    <Text style={styles.matchContextText} numberOfLines={1}>
                      {[prediction.matchContext.league, prediction.matchContext.round, prediction.matchContext.date].filter(Boolean).join('  ·  ')}
                    </Text>
                  )}
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

              {/* Data Quality Warning */}
              {prediction.dataQuality && prediction.dataQuality.level !== 'good' && prediction.dataQuality.message && (
                <View style={styles.dataQualityBanner}>
                  <Ionicons name="warning-outline" size={12} color="#F59E0B" />
                  <Text style={styles.dataQualityText}>{prediction.dataQuality.message}</Text>
                </View>
              )}

              {/* Confidence Interval */}
              {prediction.confidenceInterval && prediction.confidenceInterval[1] > prediction.confidenceInterval[0] && (
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

              {/* Moneyline & Game Type */}
              {(prediction.moneyline || prediction.expectedGameType) && (
                <>
                  <View style={styles.analysisDivider} />
                  <View style={styles.matchOddsRow}>
                    {prediction.moneyline && (() => {
                      const formatOdds = (val: string) => {
                        if (!val || val === 'N/A') return '';
                        const n = parseFloat(val);
                        if (isNaN(n)) return val;
                        if (n > 1 && n < 50) {
                          if (n >= 2) return `+${Math.round((n - 1) * 100)}`;
                          return `${Math.round(-100 / (n - 1))}`;
                        }
                        return n > 0 ? `+${Math.round(n)}` : `${Math.round(n)}`;
                      };
                      const h = formatOdds(prediction.moneyline.home);
                      const d = formatOdds(prediction.moneyline.draw);
                      const a = formatOdds(prediction.moneyline.away);
                      if (!h && !d && !a) return null;
                      const playerTeamShort = (prediction.teamName || 'HOME').split(' ').pop()?.slice(0, 5).toUpperCase() || 'HOME';
                      const oppTeamShort = (prediction.opponentName || 'AWAY').split(' ').pop()?.slice(0, 5).toUpperCase() || 'AWAY';
                      const isPlayerHome = venueOverride === 'home';
                      const team1 = isPlayerHome ? playerTeamShort : oppTeamShort;
                      const team2 = isPlayerHome ? oppTeamShort : playerTeamShort;
                      const odds1 = h;
                      const odds2 = a;
                      return (
                        <View style={styles.moneylineWrap}>
                          <View style={styles.moneylineHeader}>
                            <Ionicons name="cash-outline" size={12} color={Colors.textSecondary} />
                            <Text style={styles.moneylineLabel}>MONEYLINE</Text>
                          </View>
                          <View style={styles.moneylinePills}>
                            <View style={styles.mlPill}>
                              <Text style={styles.mlPillTeam}>{team1}</Text>
                              <Text style={styles.mlPillOdds}>{odds1}</Text>
                            </View>
                            <View style={styles.mlPill}>
                              <Text style={styles.mlPillTeam}>DRAW</Text>
                              <Text style={styles.mlPillOdds}>{d}</Text>
                            </View>
                            <View style={styles.mlPill}>
                              <Text style={styles.mlPillTeam}>{team2}</Text>
                              <Text style={styles.mlPillOdds}>{odds2}</Text>
                            </View>
                          </View>
                          <Text style={styles.mlDisclaimer}>Indicative · verify with your sportsbook</Text>
                        </View>
                      );
                    })()}
                    {prediction.expectedGameType && (
                      <View style={styles.gameTypeWrap}>
                        <Text style={styles.gameTypeLabel}>GAME TYPE</Text>
                        <Text style={styles.gameTypeValue}>{
                          (['open','cagey','one-sided','high-tempo'].includes(prediction.expectedGameType?.toLowerCase())
                            ? prediction.expectedGameType.toUpperCase()
                            : 'OPEN')
                        }</Text>
                        {prediction.keyMatchupFactor && (
                          <Text style={styles.gameTypeSub}>{prediction.keyMatchupFactor}</Text>
                        )}
                      </View>
                    )}
                  </View>
                </>
              )}

              {/* Expected Possession */}
              {prediction.expectedPossession
                && Number.isFinite(prediction.expectedPossession.home)
                && Number.isFinite(prediction.expectedPossession.away)
                && (() => {
                const homePoss = prediction.expectedPossession!.home;
                const awayPoss = prediction.expectedPossession!.away;
                const isPlayerHome = venueOverride === 'home';
                const playerTeamName = prediction.teamName || '';
                const opponentTeamName = prediction.opponentName || '';
                const homeShort = (prediction.homeTeam || (isPlayerHome ? playerTeamName : opponentTeamName) || 'HOME')
                  .split(' ').pop()?.slice(0, 6).toUpperCase() || 'HOME';
                const awayShort = (prediction.awayTeam || (isPlayerHome ? opponentTeamName : playerTeamName) || 'AWAY')
                  .split(' ').pop()?.slice(0, 6).toUpperCase() || 'AWAY';
                return (
                  <>
                    <View style={styles.analysisDivider} />
                    <View style={styles.possRow}>
                      <View style={styles.possHeader}>
                        <Ionicons name="football-outline" size={12} color={Colors.textSecondary} />
                        <Text style={styles.possLabel}>EXPECTED POSSESSION</Text>
                      </View>
                      <View style={styles.possBarWrap}>
                        <View style={[styles.possBarHome, { flex: homePoss }]} />
                        <View style={[styles.possBarAway, { flex: awayPoss }]} />
                      </View>
                      <View style={styles.possNumbers}>
                        <Text style={styles.possHomeText}>{homeShort}  {Math.round(homePoss)}%</Text>
                        <Text style={styles.possAwayText}>{Math.round(awayPoss)}%  {awayShort}</Text>
                      </View>
                      {(prediction.possessionTeamAvg != null || prediction.possessionOppAvg != null) && (
                        <Text style={styles.possSub}>
                          Season avg: {prediction.possessionTeamAvg ?? '—'}% vs {prediction.possessionOppAvg ?? '—'}%
                        </Text>
                      )}
                    </View>
                  </>
                );
              })()}

              {/* ─── PRESSURE DYNAMICS ─── */}
              {prediction.expectedPossession
                && Number.isFinite(prediction.expectedPossession.home)
                && Number.isFinite(prediction.expectedPossession.away)
                && (() => {
                const homePoss = prediction.expectedPossession!.home;
                const awayPoss = prediction.expectedPossession!.away;
                const isPlayerHome = venueOverride === 'home';
                const playerTeamName = prediction.teamName || '';
                const opponentTeamName = prediction.opponentName || '';
                const homeName = prediction.homeTeam || (isPlayerHome ? playerTeamName : opponentTeamName) || 'Home';
                const awayName = prediction.awayTeam || (isPlayerHome ? opponentTeamName : playerTeamName) || 'Away';

                const homeIsAggressor = homePoss >= awayPoss;
                const aggressorName = homeIsAggressor ? homeName : awayName;
                const defenderName = homeIsAggressor ? awayName : homeName;
                const aggressorPoss = homeIsAggressor ? homePoss : awayPoss;
                const defenderPoss = homeIsAggressor ? awayPoss : homePoss;
                const gap = Math.round(aggressorPoss - defenderPoss);

                const pressureText = gap >= 15
                  ? `${aggressorName} are projected to dominate possession by ${gap} percentage points — a significant tactical edge. Expect ${aggressorName} to dictate the tempo, pin ${defenderName} deep, and create chances through sustained pressure. ${defenderName} will likely look to absorb and counter.`
                  : gap >= 8
                  ? `${aggressorName} hold a meaningful possession edge (~${gap}pp). They set the pace and play in the opponent's half more often. ${defenderName} will be reactive, defending in a mid-block and waiting for chances to transition.`
                  : `Possession is projected to be closely contested. Both teams are expected to have spells of control — game flow will depend on which midfield wins the second balls and sets the tempo.`;

                return (
                  <>
                    <View style={styles.analysisDivider} />
                    <View style={styles.pressureCard}>
                      <View style={styles.pressureHeaderRow}>
                        <Ionicons name="shield-half-outline" size={12} color={Colors.primary} />
                        <Text style={styles.pressureTitle}>PRESSURE DYNAMICS</Text>
                      </View>
                      <Text style={styles.pressureBody}>{pressureText}</Text>
                      <View style={styles.pressureTeamsRow}>
                        <View style={styles.pressureTeamBlock}>
                          <Text style={styles.pressureTeamName} numberOfLines={1}>{aggressorName}</Text>
                          <View style={[styles.pressureLabel, styles.pressureLabelAggressor]}>
                            <Text style={styles.pressureLabelText}>⚔ THE AGGRESSORS</Text>
                          </View>
                          <Text style={styles.pressurePossText}>{Math.round(aggressorPoss)}% poss</Text>
                        </View>
                        <View style={styles.pressureVsDivider} />
                        <View style={styles.pressureTeamBlock}>
                          <Text style={styles.pressureTeamName} numberOfLines={1}>{defenderName}</Text>
                          <View style={[styles.pressureLabel, styles.pressureLabelDefender]}>
                            <Text style={styles.pressureLabelText}>🛡 THE DEFENDERS</Text>
                          </View>
                          <Text style={styles.pressurePossText}>{Math.round(defenderPoss)}% poss</Text>
                        </View>
                      </View>
                    </View>
                  </>
                );
              })()}

              {/* AI Reasoning */}
              {prediction.reasoning && (() => {
                const isOver = prediction.recommendation === 'OVER';
                const isUnder = prediction.recommendation === 'UNDER';
                const recColor = isOver ? Colors.success : isUnder ? Colors.error : Colors.textSecondary;

                const paragraphs = prediction.reasoning.split(/\n\n+/).filter(p => p.trim());
                const blocks: React.ReactElement[] = [];

                for (let i = 0; i < paragraphs.length; i++) {
                  const para = paragraphs[i];
                  const headerMatch = para.match(/^\*\*([^*]+)\*\*\s*([\s\S]*)/);
                  if (headerMatch) {
                    const section = headerMatch[1].trim();
                    const body = headerMatch[2].trim().replace(/\*\*/g, '');

                    // Skip Analysis — raw averages already shown in tiles
                    if (section === 'Analysis') continue;

                    if (section === 'Verdict') {
                      blocks.push(
                        <View key={i} style={styles.aiVerdictBlock}>
                          <View style={styles.aiVerdictPill}>
                            <Text style={[styles.aiVerdictLabel, { color: recColor }]}>VERDICT</Text>
                          </View>
                          <Text style={[styles.aiVerdictText, { color: Colors.text }]}>{body}</Text>
                        </View>
                      );
                      continue;
                    }

                    if (section === 'TL;DR') {
                      blocks.push(
                        <View key={i} style={styles.aiTldrBlock}>
                          <Text style={styles.aiTldrText}>{body}</Text>
                        </View>
                      );
                      continue;
                    }

                    const sectionIcons: Record<string, string> = {
                      Matchup: 'git-compare-outline',
                      Situation: 'flag-outline',
                      Scenarios: 'layers-outline',
                      Risk: 'warning-outline',
                      'Risk Radar': 'warning-outline',
                      'Game Flow': 'trending-up-outline',
                    };
                    const iconName = (sectionIcons[section] || 'chevron-forward-outline') as keyof typeof Ionicons.glyphMap;

                    blocks.push(
                      <View key={i} style={styles.aiSection}>
                        <View style={styles.aiSectionHeader}>
                          <Ionicons name={iconName} size={11} color={Colors.primary} />
                          <Text style={styles.aiSectionTitle}>{section.toUpperCase()}</Text>
                        </View>
                        {body ? <Text style={styles.aiSectionBody}>{body}</Text> : null}
                      </View>
                    );
                  } else {
                    const plainText = para.replace(/\*\*/g, '').trim();
                    if (plainText) {
                      blocks.push(<Text key={i} style={styles.aiBodyText}>{plainText}</Text>);
                    }
                  }
                }

                if (blocks.length === 0) return null;
                return (
                  <>
                    <View style={styles.analysisDivider} />
                    <View style={styles.aiAnalysisBox}>
                      <View style={styles.reasoningHeader}>
                        <Ionicons name="bulb-outline" size={13} color={Colors.primary} />
                        <Text style={styles.reasoningLabel}>AI ANALYSIS</Text>
                      </View>
                      <View style={styles.aiBlocks}>{blocks}</View>
                    </View>
                  </>
                );
              })()}

              {/* Analysis Summary */}
              {prediction.analysisSummary && (() => {
                const s = prediction.analysisSummary!;
                return (
                  <>
                    <View style={styles.analysisDivider} />
                    <View style={styles.summarySection}>
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
                          <Text style={styles.summaryLabel}>Opponent Profile</Text>
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
                  </>
                );
              })()}
            </View>

            {/* ─── REVERSE FORMULA CARD ─── */}
            {prediction.priorSamples != null && prediction.priorSamples >= 3 && (
              <View style={styles.rfCard}>
                <View style={styles.rfHeader}>
                  <View style={styles.rfTitleRow}>
                    <Ionicons name="pulse" size={13} color={Colors.primary} />
                    <Text style={styles.rfTitle}>REVERSE FORMULA</Text>
                  </View>
                  <Text style={styles.rfGamesAnalyzed}>{prediction.priorSamples} GAMES ANALYZED</Text>
                </View>

                {/* SEASON row */}
                <View style={styles.rfRow}>
                  <Text style={styles.rfRowLabel}>SEASON</Text>
                  <View style={styles.rfBarTrack}>
                    <View style={[styles.rfBarFill, { width: '45%', backgroundColor: '#4DA6FF' }]} />
                  </View>
                  <Text style={[styles.rfPct, { color: '#4DA6FF' }]}>45%</Text>
                  <Text style={[styles.rfVal, { color: '#4DA6FF' }]}>
                    {prediction.priorMean != null ? prediction.priorMean.toFixed(1) : '—'}
                  </Text>
                </View>

                {/* MOMENTUM row */}
                <View style={styles.rfRow}>
                  <Text style={styles.rfRowLabel}>MOMENTUM</Text>
                  <View style={styles.rfBarTrack}>
                    <View style={[styles.rfBarFill, { width: '30%', backgroundColor: '#FF8C42' }]} />
                  </View>
                  <Text style={[styles.rfPct, { color: '#FF8C42' }]}>30%</Text>
                  <Text style={[styles.rfVal, { color: '#FF8C42' }]}>
                    {prediction.momentumMean != null ? prediction.momentumMean.toFixed(1) : '—'}
                  </Text>
                </View>

                {/* CONTEXT row */}
                <View style={styles.rfRow}>
                  <Text style={styles.rfRowLabel}>CONTEXT</Text>
                  <View style={styles.rfBarTrack}>
                    <View style={[styles.rfBarFill, { width: '25%', backgroundColor: '#A084E8' }]} />
                  </View>
                  <Text style={[styles.rfPct, { color: '#A084E8' }]}>25%</Text>
                  <Text style={[styles.rfVal, { color: '#A084E8' }]}>
                    {prediction.covariateAdjustment != null
                      ? (prediction.covariateAdjustment >= 0 ? '+' : '') + prediction.covariateAdjustment.toFixed(2)
                      : '—'}
                  </Text>
                </View>

                {/* Badges */}
                <View style={styles.rfBadgeRow}>
                  {prediction.momentumLabel && (
                    <View style={[styles.rfBadge, styles.rfBadgeMomentum]}>
                      <Text style={styles.rfBadgeMomentumText}>
                        {prediction.momentumLabel === 'HOT' ? '🔥' : prediction.momentumLabel === 'COLD' ? '🧊' : '〜'}{' '}
                        {prediction.momentumLabel}
                        {prediction.momentumEffect != null && prediction.momentumEffect !== 0
                          ? ` ${prediction.momentumEffect > 0 ? '+' : ''}${prediction.momentumEffect.toFixed(2)}`
                          : ''}
                      </Text>
                    </View>
                  )}
                  {prediction.recommendation && prediction.recommendation !== 'PASS' && (
                    <View style={[styles.rfBadge, {
                      backgroundColor: prediction.recommendation === 'OVER' ? 'rgba(57,255,20,0.15)' : 'rgba(255,59,48,0.15)',
                      borderColor: prediction.recommendation === 'OVER' ? Colors.success : Colors.error,
                    }]}>
                      <Text style={[styles.rfBadgeText, {
                        color: prediction.recommendation === 'OVER' ? Colors.success : Colors.error,
                      }]}>
                        {prediction.recommendation} {prediction.line}
                      </Text>
                    </View>
                  )}
                  {prediction.volatility && (
                    <View style={[styles.rfBadge, styles.rfBadgeVol]}>
                      <Text style={styles.rfBadgeVolText}>{prediction.volatility} VOL</Text>
                    </View>
                  )}
                </View>

                {/* Math Projection */}
                <View style={styles.rfProjectionRow}>
                  <Text style={styles.rfProjectionLabel}>Math Projection</Text>
                  <View style={styles.rfProjectionRight}>
                    <Text style={styles.rfProjectionVal}>
                      {(prediction.projection ?? prediction.bayesianProjection)?.toFixed(1) ?? '—'}
                    </Text>
                    {(prediction.pOver != null || prediction.pUnder != null) && (
                      <Text style={styles.rfProjectionProb}>
                        {prediction.recommendation === 'UNDER'
                          ? `P(UNDER) ${prediction.pUnder?.toFixed(1)}%`
                          : `P(OVER) ${prediction.pOver?.toFixed(1)}%`}
                      </Text>
                    )}
                  </View>
                </View>
              </View>
            )}

            {/* ─── GAME LOG GRID ─── */}
            {prediction.gameLogs && prediction.gameLogs.length > 0 && (() => {
              const overCount = prediction.gameLogs.filter(g => g.value != null && prediction.line != null && g.value >= prediction.line).length;
              const filteredLogs = prediction.gameLogs.filter(g =>
                gameLogFilter === 'all' ? true : g.venue === gameLogFilter
              );
              const tileW = (SCREEN_W - 40 - 32 - 18) / 4;
              return (
                <View style={styles.gameLogsCard}>
                  {/* Header */}
                  <View style={styles.gameLogsHeader}>
                    <View style={styles.glHeaderLeft}>
                      <Ionicons name="pulse" size={11} color={Colors.textTertiary} />
                      <Text style={styles.gameLogsTitle}>
                        RECENT FORM ({prediction.gameLogs.length} GAMES)
                      </Text>
                    </View>
                    {prediction.hitRates != null && (
                      <View style={styles.hitRateBadge}>
                        <Text style={styles.hitRateBadgeText}>
                          {overCount} / {prediction.gameLogs.length} HIT RATE
                        </Text>
                      </View>
                    )}
                  </View>

                  {/* ALL / HOME / AWAY tabs */}
                  <View style={styles.glTabRow}>
                    {(['all', 'home', 'away'] as const).map(f => (
                      <TouchableOpacity
                        key={f}
                        style={[styles.glTab, gameLogFilter === f && styles.glTabActive]}
                        onPress={() => { setGameLogFilter(f); Haptics.selectionAsync(); }}
                      >
                        <Text style={[styles.glTabText, gameLogFilter === f && styles.glTabTextActive]}>
                          {f.toUpperCase()}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </View>

                  {/* 4-column grid — pad last row to always fill 4 columns */}
                  <View style={styles.glGrid}>
                    {(() => {
                      const remainder = filteredLogs.length % 4;
                      const padCount = remainder === 0 ? 0 : 4 - remainder;
                      return (
                        <>
                          {filteredLogs.map((g, i) => {
                            const isOver = g.value != null && prediction.line != null && g.value >= prediction.line;
                            const oppRaw = g.opponent || '?';
                            const oppShort = oppRaw.replace(/^(al-?|fc |cf |rc |sc |cd |ud |sd |rcd |as |ss |ac |us |ac |sp |ca |cp |ue |ue |ce |cm |se |sk )/i, '').slice(0, 3).toUpperCase();
                            return (
                              <View
                                key={i}
                                style={[
                                  styles.glTile,
                                  { width: tileW },
                                  isOver ? styles.glTileOver : styles.glTileUnder,
                                ]}
                              >
                                {isOver && <View style={styles.glDot} />}
                                <Text style={[styles.glTileVal, { color: isOver ? Colors.success : Colors.error }]}>
                                  {g.value != null ? String(g.value) : '—'}
                                </Text>
                                <Text style={styles.glTileMins}>{g.minutes > 0 ? `${g.minutes}'` : '—'}</Text>
                                <View style={styles.glVenueBadge}>
                                  <Text style={styles.glVenueText}>{g.venue === 'home' ? 'H' : 'A'}</Text>
                                </View>
                                <Text style={styles.glTileOpp} numberOfLines={1}>{oppShort}</Text>
                              </View>
                            );
                          })}
                          {Array.from({ length: padCount }).map((_, pi) => (
                            <View key={`pad-${pi}`} style={[styles.glTile, { width: tileW, opacity: 0 }]} pointerEvents="none" />
                          ))}
                        </>
                      );
                    })()}
                  </View>

                  {/* Home/Away splits */}
                  {(prediction.homeAvg != null || prediction.awayAvg != null) && (
                    <View style={styles.avgRow}>
                      {prediction.homeAvg != null && (
                        <Text style={styles.avgText}>HOME AVG  {prediction.homeAvg.toFixed(1)}</Text>
                      )}
                      {prediction.awayAvg != null && (
                        <Text style={styles.avgText}>AWAY AVG  {prediction.awayAvg.toFixed(1)}</Text>
                      )}
                    </View>
                  )}
                </View>
              );
            })()}

            {/* ─── H2H CARD ─── */}
            {prediction.h2hPlayerStats && prediction.h2hPlayerStats.matches.length > 0 && (
              <View style={styles.h2hCard}>
                <View style={styles.h2hHeader}>
                  <Ionicons name="swap-horizontal-outline" size={13} color={Colors.primary} />
                  <Text style={styles.h2hTitle}>
                    H2H{prediction.opponentName ? ` vs ${prediction.opponentName}` : ''}
                  </Text>
                  {prediction.h2hPlayerStats.avgVsOpponent != null && (
                    <Text style={styles.h2hAvg}>
                      AVG {prediction.h2hPlayerStats.avgVsOpponent.toFixed(1)}
                    </Text>
                  )}
                </View>
                {prediction.h2hPlayerStats.matches.map((m, i) => {
                  const isOver = m.targetStat != null && prediction.line != null && m.targetStat >= prediction.line;
                  return (
                    <View key={i} style={[styles.h2hRow, i < prediction.h2hPlayerStats!.matches.length - 1 && styles.h2hRowBorder]}>
                      <Text style={styles.h2hDate}>{m.date ? m.date.slice(0, 10) : '—'}</Text>
                      {m.score ? <Text style={styles.h2hScore}>{m.score}</Text> : null}
                      <View style={styles.h2hRight}>
                        {m.minutes > 0 && <Text style={styles.h2hMins}>{m.minutes}'</Text>}
                        <Text style={[styles.h2hStat, { color: m.targetStat != null ? (isOver ? Colors.success : Colors.error) : Colors.textTertiary }]}>
                          {m.targetStat != null ? String(m.targetStat) : '—'}
                        </Text>
                      </View>
                    </View>
                  );
                })}
              </View>
            )}

            {/* ─── POSITION COMPARISON (PROOF) ─── */}
            {prediction.positionComparison && prediction.positionComparison.players && prediction.positionComparison.players.length > 0 && (() => {
              const pc = prediction.positionComparison;
              return (
                <View style={styles.pcCard}>
                  <View style={styles.pcHeader}>
                    <View style={styles.pcTitleRow}>
                      <Ionicons name="people-outline" size={13} color={Colors.primary} />
                      <Text style={styles.pcTitle}>OPPONENT PROFILE — WHO WAS SAMPLED</Text>
                    </View>
                    <Text style={styles.pcSub}>{pc.positionShort}s vs {pc.opponent} ({(pc.venue || '').toUpperCase()}) · avg {pc.avgStatValue}</Text>
                  </View>
                  {pc.players.slice(0, 7).map((p: Record<string, unknown>, i: number) => {
                    const val = p.statValue as number;
                    const over = prediction.line != null && val >= (prediction.line as number);
                    return (
                      <View key={i} style={[styles.pcRow, i > 0 && styles.pcRowBorder]}>
                        <View style={styles.pcLeft}>
                          <Text style={styles.pcPlayerName} numberOfLines={1}>{p.name as string}</Text>
                          <Text style={styles.pcMeta} numberOfLines={1}>{p.team as string} · {(p.date as string)?.slice(0, 10) ?? '—'} · {p.minutes as number}'</Text>
                        </View>
                        <Text style={[styles.pcVal, { color: over ? Colors.success : Colors.error }]}>{val}</Text>
                      </View>
                    );
                  })}
                </View>
              );
            })()}

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
  header: { paddingHorizontal: 20, paddingBottom: 14, alignItems: 'center' },
  logoRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  logoImg: { width: 54, height: 54, marginBottom: 8 },
  logoText: { fontSize: 20, fontWeight: '800', color: Colors.text, letterSpacing: -0.3 },
  tagline: { fontSize: 11, color: Colors.primary, marginTop: 2, letterSpacing: 0.5, fontWeight: '600' },
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

  /* Confidence interval */
  ciRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 10,
  },
  ciLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.8 },
  ciVal: { fontSize: 13, color: Colors.textSecondary, fontWeight: '600' },

  /* Moneyline & Game Type */
  matchOddsRow: { paddingHorizontal: 16, paddingVertical: 12, gap: 10 },
  moneylineWrap: { gap: 6 },
  moneylineHeader: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  moneylineLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1.2 },
  mlDisclaimer: { fontSize: 9, color: Colors.textTertiary, marginTop: 4, fontStyle: 'italic' },
  moneylinePills: { flexDirection: 'row', gap: 6 },
  mlPill: {
    flex: 1, backgroundColor: '#1a1a1a', borderRadius: 8, paddingVertical: 8,
    alignItems: 'center', borderWidth: 1, borderColor: Colors.borderSubtle,
  },
  mlPillTeam: { fontSize: 9, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.8, marginBottom: 2 },
  mlPillOdds: { fontSize: 15, color: Colors.text, fontWeight: '800', fontVariant: ['tabular-nums'] as any },
  gameTypeWrap: { marginTop: 2, gap: 2 },
  gameTypeLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1.2 },
  gameTypeValue: { fontSize: 14, color: Colors.primary, fontWeight: '800', letterSpacing: 0.5 },
  gameTypeSub: { fontSize: 11, color: Colors.textSecondary },

  /* Expected Possession */
  possRow: { paddingHorizontal: 16, paddingVertical: 12, gap: 6 },
  possHeader: { flexDirection: 'row', alignItems: 'center', gap: 5, marginBottom: 2 },
  possLabel: { fontSize: 10, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 1.2 },
  possBarWrap: { flexDirection: 'row', height: 8, borderRadius: 4, overflow: 'hidden' },
  possBarHome: { backgroundColor: Colors.primary, borderTopLeftRadius: 4, borderBottomLeftRadius: 4 },
  possBarAway: { backgroundColor: '#f43f5e', borderTopRightRadius: 4, borderBottomRightRadius: 4 },
  possNumbers: { flexDirection: 'row', justifyContent: 'space-between' },
  possHomeText: { fontSize: 13, fontWeight: '800', color: Colors.primary, fontVariant: ['tabular-nums'] as any },
  possAwayText: { fontSize: 13, fontWeight: '800', color: '#f43f5e', fontVariant: ['tabular-nums'] as any },
  possSub: { fontSize: 10, color: Colors.textTertiary, marginTop: 2 },

  /* Reasoning */
  reasoningBox: { padding: 16, gap: 8 },
  aiAnalysisBox: { padding: 16, gap: 12 },
  reasoningHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  reasoningLabel: { fontSize: 10, color: Colors.primary, fontWeight: '700', letterSpacing: 1.5 },
  reasoningText: { fontSize: 13, color: Colors.textSecondary, lineHeight: 20 },

  /* AI section blocks */
  aiBlocks: { gap: 14, marginTop: 4 },
  aiVerdictBlock: {
    backgroundColor: 'rgba(57,255,20,0.06)',
    borderLeftWidth: 3,
    borderLeftColor: Colors.primary,
    borderRadius: 8,
    padding: 12,
    gap: 6,
  },
  aiVerdictPill: {
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(57,255,20,0.12)',
    borderRadius: 4,
    paddingHorizontal: 7,
    paddingVertical: 2,
  },
  aiVerdictLabel: { fontSize: 9, fontWeight: '800', letterSpacing: 1.5 },
  aiVerdictText: { fontSize: 14, fontWeight: '600', lineHeight: 21, color: Colors.text },
  aiTldrBlock: {
    backgroundColor: Colors.cardSecondary,
    borderRadius: 8,
    padding: 12,
  },
  aiTldrText: { fontSize: 12, color: Colors.textSecondary, lineHeight: 18, fontStyle: 'italic' },
  aiSection: { gap: 5 },
  aiSectionHeader: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  aiSectionTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.2 },
  aiSectionBody: { fontSize: 13, color: Colors.textSecondary, lineHeight: 20 },
  aiBodyText: { fontSize: 13, color: Colors.textSecondary, lineHeight: 20 },

  /* ─── REVERSE FORMULA CARD ─── */
  rfCard: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    padding: 16, borderWidth: 1, borderColor: Colors.borderSubtle, marginTop: 12, gap: 10,
  },
  rfHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  rfTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  rfTitle: { fontSize: 12, fontWeight: '800', color: Colors.primary, letterSpacing: 1.5 },
  rfGamesAnalyzed: { fontSize: 10, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.5 },
  rfRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  rfRowLabel: { fontSize: 11, fontWeight: '700', color: Colors.textSecondary, letterSpacing: 0.5, width: 78 },
  rfBarTrack: {
    flex: 1, height: 6, backgroundColor: Colors.cardSecondary,
    borderRadius: 3, overflow: 'hidden',
  },
  rfBarFill: { height: '100%', borderRadius: 3 },
  rfPct: { fontSize: 11, fontWeight: '700', width: 34, textAlign: 'right' },
  rfVal: { fontSize: 13, fontWeight: '800', width: 52, textAlign: 'right' },
  rfBadgeRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', marginTop: 2 },
  rfBadge: {
    paddingHorizontal: 10, paddingVertical: 5,
    borderRadius: 8, borderWidth: 1,
  },
  rfBadgeMomentum: { backgroundColor: 'rgba(255,140,66,0.15)', borderColor: '#FF8C42' },
  rfBadgeMomentumText: { fontSize: 12, fontWeight: '700', color: '#FF8C42' },
  rfBadgeText: { fontSize: 12, fontWeight: '800' },
  rfBadgeVol: { backgroundColor: Colors.cardSecondary, borderColor: Colors.border },
  rfBadgeVolText: { fontSize: 11, fontWeight: '600', color: Colors.textSecondary },
  rfProjectionRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    backgroundColor: Colors.cardSecondary, borderRadius: 10, padding: 12, marginTop: 4,
  },
  rfProjectionLabel: { fontSize: 13, color: Colors.textSecondary, fontWeight: '600' },
  rfProjectionRight: { flexDirection: 'row', alignItems: 'baseline', gap: 8 },
  rfProjectionVal: { fontSize: 20, fontWeight: '800', color: '#4DA6FF' },
  rfProjectionProb: { fontSize: 12, color: '#4DA6FF', fontWeight: '600' },

  /* ─── GAME LOG GRID ─── */
  gameLogsCard: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    padding: 16, borderWidth: 1, borderColor: Colors.borderSubtle, marginTop: 12, gap: 12,
  },
  gameLogsHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  glHeaderLeft: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  gameLogsTitle: { fontSize: 10, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 1.2 },
  hitRateBadge: {
    backgroundColor: 'rgba(57,255,20,0.12)', borderRadius: 20,
    paddingHorizontal: 10, paddingVertical: 3, borderWidth: 1, borderColor: 'rgba(57,255,20,0.3)',
  },
  hitRateBadgeText: { fontSize: 10, fontWeight: '700', color: Colors.success },
  glTabRow: {
    flexDirection: 'row', backgroundColor: Colors.cardSecondary,
    borderRadius: 8, padding: 2, gap: 2,
  },
  glTab: { flex: 1, alignItems: 'center', paddingVertical: 7, borderRadius: 7 },
  glTabActive: { backgroundColor: Colors.primaryDim },
  glTabText: { fontSize: 11, fontWeight: '700', color: Colors.textSecondary, letterSpacing: 0.5 },
  glTabTextActive: { color: Colors.primary },
  glGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  glTile: {
    borderRadius: 10, padding: 8, alignItems: 'center',
    gap: 4, borderWidth: 1, position: 'relative',
  },
  glTileOver: {
    backgroundColor: 'rgba(57,255,20,0.07)', borderColor: 'rgba(57,255,20,0.3)',
  },
  glTileUnder: {
    backgroundColor: 'rgba(255,59,48,0.07)', borderColor: 'rgba(255,59,48,0.2)',
  },
  glDot: {
    position: 'absolute', top: 6, right: 6,
    width: 6, height: 6, borderRadius: 3, backgroundColor: '#FF8C42',
  },
  glTileVal: { fontSize: 18, fontWeight: '900', lineHeight: 22 },
  glTileMins: { fontSize: 9, color: Colors.textSecondary, fontWeight: '600' },
  glVenueBadge: {
    backgroundColor: '#1a1a1a', borderRadius: 4,
    paddingHorizontal: 5, paddingVertical: 2,
  },
  glVenueText: { fontSize: 9, fontWeight: '800', color: Colors.textSecondary, letterSpacing: 0.5 },
  glTileOpp: { fontSize: 9, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.5 },
  avgRow: { flexDirection: 'row', gap: 16 },
  avgText: { fontSize: 10, color: Colors.textSecondary, fontWeight: '600', letterSpacing: 0.5 },

  /* ─── H2H CARD ─── */
  h2hCard: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    padding: 16, borderWidth: 1, borderColor: Colors.borderSubtle, marginTop: 12, gap: 0,
  },
  h2hHeader: { flexDirection: 'row', alignItems: 'center', gap: 7, marginBottom: 12 },
  h2hTitle: { fontSize: 11, fontWeight: '800', color: Colors.primary, letterSpacing: 1.2, flex: 1 },
  h2hAvg: { fontSize: 11, color: Colors.textSecondary, fontWeight: '600' },
  h2hRow: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 10, gap: 10,
  },
  h2hRowBorder: { borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle },
  h2hDate: { fontSize: 11, color: Colors.textTertiary, fontWeight: '600', width: 72 },
  h2hScore: { fontSize: 12, color: Colors.textSecondary, fontWeight: '600', flex: 1 },
  h2hRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  h2hMins: { fontSize: 10, color: Colors.textTertiary },
  h2hStat: { fontSize: 16, fontWeight: '800', minWidth: 28, textAlign: 'right' },

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

  /* Pressure Dynamics */
  pressureCard: {
    backgroundColor: Colors.cardSecondary, borderRadius: Colors.radius,
    borderWidth: 1, borderColor: Colors.borderSubtle, padding: 14, gap: 12,
  },
  pressureHeaderRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  pressureTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.2 },
  pressureBody: { fontSize: 12, color: Colors.textSecondary, lineHeight: 18 },
  pressureTeamsRow: { flexDirection: 'row', alignItems: 'stretch', gap: 10 },
  pressureTeamBlock: { flex: 1, alignItems: 'center', gap: 4 },
  pressureTeamName: { fontSize: 13, fontWeight: '700', color: Colors.text, textAlign: 'center' },
  pressureLabel: { borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3 },
  pressureLabelAggressor: { backgroundColor: 'rgba(57,255,20,0.12)', borderWidth: 1, borderColor: 'rgba(57,255,20,0.3)' },
  pressureLabelDefender: { backgroundColor: 'rgba(255,149,0,0.10)', borderWidth: 1, borderColor: 'rgba(255,149,0,0.3)' },
  pressureLabelText: { fontSize: 9, fontWeight: '800', color: Colors.textSecondary, letterSpacing: 0.8 },
  pressurePossText: { fontSize: 11, color: Colors.textTertiary },
  pressureVsDivider: { width: 1, backgroundColor: Colors.borderSubtle, alignSelf: 'stretch' },

  /* Position Comparison (Proof) */
  pcCard: {
    backgroundColor: Colors.card, borderRadius: Colors.radiusLg,
    borderWidth: 1, borderColor: Colors.borderSubtle, overflow: 'hidden', marginTop: 12,
  },
  pcHeader: { padding: 14, gap: 4, borderBottomWidth: 1, borderBottomColor: Colors.borderSubtle },
  pcTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  pcTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.2 },
  pcSub: { fontSize: 11, color: Colors.textSecondary },
  pcRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 14, paddingVertical: 10 },
  pcRowBorder: { borderTopWidth: 1, borderTopColor: Colors.borderSubtle },
  pcLeft: { flex: 1, marginRight: 12 },
  pcPlayerName: { fontSize: 13, fontWeight: '600', color: Colors.text },
  pcMeta: { fontSize: 10, color: Colors.textTertiary, marginTop: 1 },
  pcVal: { fontSize: 16, fontWeight: '800' },

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

  summarySection: { padding: 16, gap: 10 },
  summaryHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  summaryTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.5 },
  summaryGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  summaryItem: {
    backgroundColor: Colors.cardSecondary, borderRadius: 10, padding: 12,
    alignItems: 'center', gap: 4, minWidth: '30%' as unknown as number, flex: 1,
  },
  summaryLabel: { fontSize: 9, color: Colors.textTertiary, fontWeight: '700', letterSpacing: 0.5, textAlign: 'center' },
  summaryValue: { fontSize: 18, fontWeight: '800', color: Colors.text },
  summarySub: { fontSize: 8, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.8 },

  /* Match context tag line in header */
  matchContextText: {
    fontSize: 10, color: Colors.primary, fontWeight: '600', letterSpacing: 0.5, marginTop: 2,
  },

  /* Data quality banner */
  dataQualityBanner: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 6,
    backgroundColor: '#F59E0B15', paddingHorizontal: 14, paddingVertical: 8,
    borderTopWidth: 1, borderTopColor: '#F59E0B30',
  },
  dataQualityText: { fontSize: 11, color: '#F59E0B', flex: 1, lineHeight: 16 },

  /* Sharp Intelligence section */
  sharpIntelBox: {
    backgroundColor: '#FFD70008', paddingHorizontal: 16, paddingBottom: 4,
  },
  sharpIntelHeader: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingTop: 12, paddingBottom: 8,
  },
  sharpIntelLabel: {
    fontSize: 10, fontWeight: '800', color: '#FFD700', letterSpacing: 1.5,
  },
  sharpRow: {
    paddingBottom: 12, marginBottom: 2,
    borderBottomWidth: 1, borderBottomColor: Colors.border + '60',
  },
  sharpRowTitle: {
    fontSize: 10, fontWeight: '800', color: Colors.textTertiary,
    letterSpacing: 0.8, marginBottom: 4,
  },
  sharpRowText: {
    fontSize: 13, color: Colors.textSecondary, lineHeight: 19,
  },
});
