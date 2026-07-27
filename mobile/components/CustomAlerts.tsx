import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Switch,
  ScrollView,
  TouchableOpacity,
  Modal,
  SafeAreaView
} from 'react-native';

export interface CustomAlertsProps {
  /** Controls the visibility of the modal */
  visible?: boolean;
  /** Callback when the modal is closed */
  onClose?: () => void;
}

export default function CustomAlerts({ visible = true, onClose }: CustomAlertsProps) {
  const [lineDrops, setLineDrops] = useState<boolean>(true);
  const [confidenceSpikes, setConfidenceSpikes] = useState<boolean>(false);
  const [liveMismatch, setLiveMismatch] = useState<boolean>(true);
  const [favoriteLeagues, setFavoriteLeagues] = useState<boolean>(true);

  const renderToggle = (
    title: string,
    description: string,
    value: boolean,
    onValueChange: (val: boolean) => void
  ) => {
    return (
      <View style={styles.settingRow}>
        <View style={styles.settingTextContainer}>
          <Text style={styles.settingTitle}>{title}</Text>
          <Text style={styles.settingDescription}>{description}</Text>
        </View>
        <Switch
          trackColor={{ false: '#3f3f46', true: '#10b981' }}
          thumbColor="#ffffff"
          ios_backgroundColor="#3f3f46"
          onValueChange={onValueChange}
          value={value}
        />
      </View>
    );
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={onClose}
    >
      <SafeAreaView style={styles.container}>
        <View style={styles.header}>
          <Text style={styles.headerTitle}>Smart Alerts</Text>
          {onClose && (
            <TouchableOpacity onPress={onClose} style={styles.closeButton} activeOpacity={0.7}>
              <Text style={styles.closeButtonText}>Done</Text>
            </TouchableOpacity>
          )}
        </View>
        
        <ScrollView style={styles.content} contentContainerStyle={styles.contentContainer}>
          <Text style={styles.sectionTitle}>Notifications</Text>
          
          <View style={styles.card}>
            {renderToggle(
              'Line Drops',
              'Get notified when sharp money moves the line significantly.',
              lineDrops,
              setLineDrops
            )}
            <View style={styles.divider} />
            {renderToggle(
              'Confidence Spikes',
              'Alerts when algorithm confidence jumps above 75%.',
              confidenceSpikes,
              setConfidenceSpikes
            )}
            <View style={styles.divider} />
            {renderToggle(
              'Live Mismatch Warnings',
              'In-game alerts when live stats drastically differ from pre-game odds.',
              liveMismatch,
              setLiveMismatch
            )}
            <View style={styles.divider} />
            {renderToggle(
              'Favorite Leagues Only',
              'Only receive alerts for leagues you have favorited.',
              favoriteLeagues,
              setFavoriteLeagues
            )}
          </View>
          
          <View style={styles.infoBox}>
            <Text style={styles.infoText}>
              Smart alerts use background processing to ensure you never miss a profitable edge. Adjusting these settings takes effect immediately.
            </Text>
          </View>
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#09090b', // zinc-950
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#27272a', // zinc-800
  },
  headerTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: '#ffffff',
  },
  closeButton: {
    padding: 8,
  },
  closeButtonText: {
    color: '#10b981', // emerald-500
    fontSize: 16,
    fontWeight: '600',
  },
  content: {
    flex: 1,
  },
  contentContainer: {
    padding: 20,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: '600',
    color: '#a1a1aa', // zinc-400
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 12,
    marginLeft: 4,
  },
  card: {
    backgroundColor: '#18181b', // zinc-900
    borderRadius: 16,
    borderWidth: 1,
    borderColor: '#27272a', // zinc-800
    overflow: 'hidden',
    marginBottom: 24,
  },
  settingRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
  },
  settingTextContainer: {
    flex: 1,
    paddingRight: 16,
  },
  settingTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#ffffff',
    marginBottom: 4,
  },
  settingDescription: {
    fontSize: 14,
    color: '#a1a1aa', // zinc-400
    lineHeight: 20,
  },
  divider: {
    height: 1,
    backgroundColor: '#27272a', // zinc-800
    marginLeft: 16,
  },
  infoBox: {
    backgroundColor: '#022c22', // emerald-950
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#064e3b', // emerald-900
  },
  infoText: {
    color: '#34d399', // emerald-400
    fontSize: 14,
    lineHeight: 20,
  }
});
