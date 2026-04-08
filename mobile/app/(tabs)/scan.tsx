import React, { useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  TextInput, ActivityIndicator, Alert, Platform, Modal,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { scanProp, predict, savePick, PROP_TYPES, LEAGUES, PredictionResult, ScanResult } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import PredictionCard from '@/components/PredictionCard';

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
        playerId: data.playerId,
        teamId: data.teamId,
        opponentId: data.opponentId,
        leagueId: data.leagueId || leagueId,
        propType: data.propType || propType,
        line: data.line,
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
        teamName: prediction.teamName,
        opponentName: prediction.opponentName,
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

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      <View style={styles.header}>
        <View style={styles.logoRow}>
          <Ionicons name="football" size={20} color={Colors.primary} />
          <Text style={styles.logoText}>ReversePicks</Text>
        </View>
        <Text style={styles.tagline}>AI Soccer Prop Analytics</Text>
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
                <Text style={styles.loadingText}>{scanning ? 'Reading prop slip…' : 'Analyzing with AI…'}</Text>
              </View>
            )}

            {scanResult && !scanning && !predicting && (
              <View style={styles.scanResultBox}>
                <Ionicons name="checkmark-circle" size={16} color={Colors.success} />
                <Text style={styles.scanResultText}>
                  Detected: {scanResult.playerName} · {scanResult.propType} {scanResult.line}
                </Text>
              </View>
            )}
          </>
        ) : (
          <View style={styles.manualForm}>
            <View style={styles.fieldLabel}>
              <Text style={styles.label}>Player Name</Text>
            </View>
            <TextInput
              style={styles.textInput}
              placeholder="e.g. Kevin De Bruyne"
              placeholderTextColor={Colors.textSecondary}
              value={playerQuery}
              onChangeText={setPlayerQuery}
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
              style={styles.textInput}
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
            onSave={saved ? undefined : handleSavePick}
            saving={saving}
          />
        )}

        {saved && (
          <View style={styles.savedBanner}>
            <Ionicons name="checkmark-circle" size={18} color={Colors.success} />
            <Text style={styles.savedText}>Pick saved!</Text>
          </View>
        )}
      </ScrollView>

      <Modal visible={showPropPicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setShowPropPicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>Prop Type</Text>
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
          </View>
        </TouchableOpacity>
      </Modal>

      <Modal visible={showLeaguePicker} transparent animationType="slide">
        <TouchableOpacity style={styles.modalOverlay} onPress={() => setShowLeaguePicker(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>League</Text>
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
          </View>
        </TouchableOpacity>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: { paddingHorizontal: 20, paddingBottom: 12 },
  logoRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  logoText: { fontSize: 22, fontWeight: '800', color: Colors.text },
  tagline: { fontSize: 12, color: Colors.textSecondary, marginTop: 2 },
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
  scanResultText: { color: Colors.success, fontSize: 13, flex: 1 },
  manualForm: { gap: 8, marginBottom: 16 },
  fieldLabel: { flexDirection: 'row', justifyContent: 'space-between' },
  label: { fontSize: 12, color: Colors.textSecondary, fontWeight: '600', letterSpacing: 0.5, marginBottom: 4, marginTop: 8 },
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
  },
  analyzeBtnDisabled: { opacity: 0.6 },
  analyzeBtnText: { color: '#000', fontWeight: '700', fontSize: 16 },
  savedBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: Colors.successDim,
    padding: 14,
    borderRadius: Colors.radius,
    marginTop: 4,
  },
  savedText: { color: Colors.success, fontWeight: '700', fontSize: 15 },
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
