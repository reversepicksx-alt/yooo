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
import { scanProp, predict, savePick, PROP_TYPES, LEAGUES, PredictionResult, ScanResult } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import PredictionCard from '@/components/PredictionCard';

const INPUT_STYLE = Platform.OS === 'web' ? { outlineWidth: 0 } as object : {};

type Mode = 'scan' | 'manual';

export default function ScanScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const [mode, setMode] = useState<Mode>('scan');

  const [scanning, setScanning] = useState(false);
  const [predicting, setPredicting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [prediction, setPrediction] = useState<PredictionResult | null>(null);
  const [saved, setSaved] = useState(false);

  const [playerQuery, setPlayerQuery] = useState('');
  const [propType, setPropType] = useState(PROP_TYPES[0].value);
  const [line, setLine] = useState('');
  const [leagueId, setLeagueId] = useState(39);
  const [showPropPicker, setShowPropPicker] = useState(false);
  const [showLeaguePicker, setShowLeaguePicker] = useState(false);

  const topPad = Platform.OS === 'web' ? 67 : insets.top;

  const resetResults = () => {
    setScanResult(null);
    setPrediction(null);
    setSaved(false);
  };

  const handlePickImage = async () => {
    resetResults();
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Permission needed', 'Please allow photo library access to scan prop slips.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.85,
      base64: true,
    });
    if (result.canceled || !result.assets[0].base64) return;
    const base64 = result.assets[0].base64;
    setScanning(true);
    try {
      const scanned = await scanProp(base64, 'soccer');
      setScanResult(scanned);
      if (scanned.error) {
        Alert.alert('Scan failed', scanned.error);
        return;
      }
      await runPredict(scanned);
    } catch (e: unknown) {
      Alert.alert('Scan error', e instanceof Error ? e.message : 'Failed to scan image');
    } finally {
      setScanning(false);
    }
  };

  const handleCamera = async () => {
    resetResults();
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Permission needed', 'Please allow camera access to scan prop slips.');
      return;
    }
    const result = await ImagePicker.launchCameraAsync({
      quality: 0.85,
      base64: true,
    });
    if (result.canceled || !result.assets[0].base64) return;
    const base64 = result.assets[0].base64;
    setScanning(true);
    try {
      const scanned = await scanProp(base64, 'soccer');
      setScanResult(scanned);
      if (scanned.error) { Alert.alert('Scan failed', scanned.error); return; }
      await runPredict(scanned);
    } catch (e: unknown) {
      Alert.alert('Scan error', e instanceof Error ? e.message : 'Failed');
    } finally {
      setScanning(false);
    }
  };

  const runPredict = async (data: ScanResult) => {
    setPredicting(true);
    try {
      const req = {
        playerName: data.playerName,
        playerId: data.playerId || 0,
        teamId: data.teamId || 0,
        teamName: data.teamName || data.playerTeam || '',
        opponentId: data.opponentId || 0,
        opponentName: data.opponentName || '',
        venue: data.venue || 'home',
        leagueId: data.leagueId || leagueId,
        propType: data.propType || propType,
        line: data.line || 0,
      };
      const result = await predict(req);
      setPrediction(result);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      Alert.alert('Prediction error', e instanceof Error ? e.message : 'Failed');
    } finally {
      setPredicting(false);
    }
  };

  const handleManualPredict = async () => {
    if (!playerQuery.trim()) { Alert.alert('Enter player name'); return; }
    if (!line.trim() || isNaN(parseFloat(line))) { Alert.alert('Enter valid line value'); return; }
    resetResults();
    const data: ScanResult = {
      playerName: playerQuery,
      propType,
      line: parseFloat(line),
      leagueId,
    };
    setScanResult(data);
    await runPredict(data);
  };

  const handleSavePick = async () => {
    if (!session || !prediction) return;
    setSaving(true);
    try {
      await savePick(session.email, session.token, {
        playerName: prediction.playerName || scanResult?.playerName || playerQuery,
        teamName: prediction.teamName || scanResult?.teamName || scanResult?.playerTeam,
        opponentName: prediction.opponentName || scanResult?.opponentName,
        propType: prediction.propType || propType,
        line: prediction.line ?? scanResult?.line ?? parseFloat(line),
        projection: prediction.projection ?? prediction.bayesianProjection,
        recommendation: prediction.recommendation,
        confidence: prediction.confidence,
        sport: 'soccer',
      });
      setSaved(true);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: unknown) {
      Alert.alert('Save failed', e instanceof Error ? e.message : 'Try again');
    } finally {
      setSaving(false);
    }
  };

  const handleNewPick = () => {
    resetResults();
    setPlayerQuery('');
    setLine('');
  };

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

      <View style={styles.tabRow}>
        {(['scan', 'manual'] as Mode[]).map(m => (
          <TouchableOpacity
            key={m}
            style={[styles.tab, mode === m && styles.tabActive]}
            onPress={() => { setMode(m); resetResults(); Haptics.selectionAsync(); }}
          >
            <Ionicons
              name={m === 'scan' ? 'scan-outline' : 'search-outline'}
              size={14}
              color={mode === m ? Colors.primary : Colors.textSecondary}
            />
            <Text style={[styles.tabText, mode === m && styles.tabTextActive]}>
              {m === 'scan' ? 'Scan Slip' : 'Manual Search'}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
        {!saved ? (
          <>
            {mode === 'scan' ? (
              <>
                <View style={styles.uploadBox}>
                  <Ionicons name="image-outline" size={40} color={Colors.textTertiary} />
                  <Text style={styles.uploadTitle}>Scan a Prop Slip</Text>
                  <Text style={styles.uploadSub}>Upload or take a photo of any soccer prop slip</Text>
                  <View style={styles.uploadBtns}>
                    <TouchableOpacity style={styles.uploadBtn} onPress={handleCamera} activeOpacity={0.8}>
                      <Ionicons name="camera-outline" size={16} color={Colors.primary} />
                      <Text style={styles.uploadBtnText}>Camera</Text>
                    </TouchableOpacity>
                    <TouchableOpacity style={[styles.uploadBtn, styles.uploadBtnSecondary]} onPress={handlePickImage} activeOpacity={0.8}>
                      <Ionicons name="images-outline" size={16} color={Colors.textSecondary} />
                      <Text style={[styles.uploadBtnText, { color: Colors.textSecondary }]}>Gallery</Text>
                    </TouchableOpacity>
                  </View>
                </View>

                {(scanning || predicting) && (
                  <View style={styles.loadingBox}>
                    <ActivityIndicator color={Colors.primary} />
                    <Text style={styles.loadingText}>
                      {scanning ? 'Grok Vision reading slip…' : 'AI analyzing prop…'}
                    </Text>
                  </View>
                )}

                {scanResult && !scanResult.error && !scanning && !predicting && (
                  <View style={styles.scanResultBox}>
                    <Ionicons name="checkmark-circle" size={16} color={Colors.success} />
                    <Text style={styles.scanResultText}>
                      {scanResult.playerName}
                      {scanResult.propType ? ` · ${scanResult.propType.replace(/_/g, ' ')}` : ''}
                      {scanResult.line ? ` ${scanResult.line}` : ''}
                    </Text>
                  </View>
                )}
              </>
            ) : (
              <View style={styles.manualForm}>
                <Text style={styles.label}>Player Name</Text>
                <TextInput
                  style={[styles.textInput, INPUT_STYLE]}
                  placeholder="e.g. Kevin De Bruyne"
                  placeholderTextColor={Colors.textSecondary}
                  value={playerQuery}
                  onChangeText={setPlayerQuery}
                  autoCorrect={false}
                />

                <Text style={styles.label}>League</Text>
                <TouchableOpacity style={styles.pickerBtn} onPress={() => setShowLeaguePicker(true)}>
                  <Text style={styles.pickerBtnText}>{LEAGUES.find(l => l.id === leagueId)?.name || 'Select'}</Text>
                  <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
                </TouchableOpacity>

                <Text style={styles.label}>Prop Type</Text>
                <TouchableOpacity style={styles.pickerBtn} onPress={() => setShowPropPicker(true)}>
                  <Text style={styles.pickerBtnText}>{PROP_TYPES.find(p => p.value === propType)?.label || 'Select'}</Text>
                  <Ionicons name="chevron-down" size={14} color={Colors.textSecondary} />
                </TouchableOpacity>

                <Text style={styles.label}>Line Value</Text>
                <TextInput
                  style={[styles.textInput, INPUT_STYLE]}
                  placeholder="e.g. 2.5"
                  placeholderTextColor={Colors.textSecondary}
                  value={line}
                  onChangeText={setLine}
                  keyboardType="decimal-pad"
                />

                <TouchableOpacity
                  style={[styles.analyzeBtn, predicting && styles.analyzeBtnDisabled]}
                  onPress={handleManualPredict}
                  disabled={predicting}
                  activeOpacity={0.8}
                >
                  {predicting
                    ? <ActivityIndicator color="#000" />
                    : <>
                        <Ionicons name="analytics-outline" size={18} color="#000" />
                        <Text style={styles.analyzeBtnText}>Analyze</Text>
                      </>
                  }
                </TouchableOpacity>
              </View>
            )}

            {prediction && !predicting && (
              <PredictionCard
                result={prediction}
                onSave={handleSavePick}
                saving={saving}
              />
            )}
          </>
        ) : (
          <View style={styles.savedState}>
            <View style={styles.savedCheck}>
              <Ionicons name="checkmark" size={36} color="#000" />
            </View>
            <Text style={styles.savedTitle}>Pick Saved!</Text>
            <Text style={styles.savedSub}>
              {prediction?.recommendation} {prediction?.playerName || scanResult?.playerName || playerQuery}
              {'\n'}
              {prediction?.propType?.replace(/_/g, ' ')} · Line {prediction?.line ?? scanResult?.line}
            </Text>

            <TouchableOpacity
              style={styles.viewPicksBtn}
              onPress={() => router.push('/(tabs)/picks')}
              activeOpacity={0.85}
            >
              <Ionicons name="bookmark" size={16} color="#000" />
              <Text style={styles.viewPicksBtnText}>View in My Picks</Text>
            </TouchableOpacity>

            <TouchableOpacity style={styles.newPickBtn} onPress={handleNewPick} activeOpacity={0.8}>
              <Text style={styles.newPickBtnText}>Analyze Another Pick</Text>
            </TouchableOpacity>
          </View>
        )}
      </ScrollView>

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
  logoImg: { width: 38, height: 38 },
  logoText: { fontSize: 20, fontWeight: '800', color: Colors.text, letterSpacing: -0.3 },
  tagline: { fontSize: 11, color: Colors.primary, marginTop: 1, letterSpacing: 0.5, fontWeight: '600' },
  tabRow: {
    flexDirection: 'row',
    marginHorizontal: 20,
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    padding: 3,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  tab: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 9,
    gap: 6,
    borderRadius: 9,
  },
  tabActive: { backgroundColor: Colors.primaryDim },
  tabText: { fontSize: 13, color: Colors.textSecondary, fontWeight: '600' },
  tabTextActive: { color: Colors.primary },
  body: { paddingHorizontal: 20, paddingBottom: 40 },
  uploadBox: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    padding: 32,
    alignItems: 'center',
    borderWidth: 1.5,
    borderColor: Colors.border,
    borderStyle: 'dashed',
    marginBottom: 16,
    gap: 10,
  },
  uploadTitle: { fontSize: 17, fontWeight: '700', color: Colors.text },
  uploadSub: { fontSize: 13, color: Colors.textSecondary, textAlign: 'center' },
  uploadBtns: { flexDirection: 'row', gap: 10, marginTop: 8 },
  uploadBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: Colors.primaryDim,
    borderWidth: 1,
    borderColor: Colors.primary,
    paddingVertical: 10,
    paddingHorizontal: 18,
    borderRadius: Colors.radius,
  },
  uploadBtnSecondary: {
    backgroundColor: Colors.cardSecondary,
    borderColor: Colors.border,
  },
  uploadBtnText: { color: Colors.primary, fontWeight: '600', fontSize: 14 },
  loadingBox: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: Colors.card,
    padding: 16,
    borderRadius: Colors.radius,
    marginBottom: 12,
  },
  loadingText: { color: Colors.textSecondary, fontSize: 14 },
  scanResultBox: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: Colors.successDim,
    padding: 12,
    borderRadius: Colors.radius,
    marginBottom: 12,
  },
  scanResultText: { color: Colors.success, fontSize: 13, flex: 1, textTransform: 'capitalize' },
  manualForm: { gap: 8, marginBottom: 16 },
  label: { fontSize: 12, color: Colors.textSecondary, fontWeight: '600', letterSpacing: 0.5, marginBottom: 4, marginTop: 8, textTransform: 'uppercase' },
  textInput: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.border,
    color: Colors.text,
    fontSize: 15,
    paddingHorizontal: 14,
    height: 48,
  },
  pickerBtn: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radius,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: 14,
    height: 48,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  pickerBtnText: { color: Colors.text, fontSize: 15 },
  analyzeBtn: {
    backgroundColor: Colors.primary,
    borderRadius: Colors.radius,
    height: 50,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    marginTop: 8,
    shadowColor: Colors.primary,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 10,
    elevation: 6,
  },
  analyzeBtnDisabled: { opacity: 0.6 },
  analyzeBtnText: { color: '#000', fontWeight: '700', fontSize: 16 },
  savedState: {
    flex: 1,
    alignItems: 'center',
    paddingTop: 40,
    gap: 14,
  },
  savedCheck: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: Colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: Colors.primary,
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.4,
    shadowRadius: 16,
    elevation: 10,
  },
  savedTitle: { fontSize: 24, fontWeight: '800', color: Colors.text },
  savedSub: {
    fontSize: 14,
    color: Colors.textSecondary,
    textAlign: 'center',
    lineHeight: 22,
    textTransform: 'capitalize',
  },
  viewPicksBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: Colors.primary,
    borderRadius: Colors.radius,
    paddingVertical: 14,
    paddingHorizontal: 32,
    marginTop: 8,
    shadowColor: Colors.primary,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 10,
    elevation: 6,
  },
  viewPicksBtnText: { color: '#000', fontWeight: '800', fontSize: 15 },
  newPickBtn: {
    paddingVertical: 14,
    paddingHorizontal: 24,
  },
  newPickBtnText: { color: Colors.textSecondary, fontSize: 14, fontWeight: '600' },
  modalOverlay: { flex: 1, backgroundColor: Colors.overlay, justifyContent: 'flex-end' },
  modalSheet: {
    backgroundColor: Colors.card,
    borderTopLeftRadius: Colors.radiusLg,
    borderTopRightRadius: Colors.radiusLg,
    padding: 20,
    maxHeight: '70%',
  },
  modalTitle: { fontSize: 16, fontWeight: '700', color: Colors.text, marginBottom: 14 },
  modalItem: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
  },
  modalItemActive: { backgroundColor: Colors.primaryDim, borderRadius: 8, paddingHorizontal: 10 },
  modalItemText: { fontSize: 15, color: Colors.text },
  modalItemTextActive: { color: Colors.primary, fontWeight: '600' },
});
