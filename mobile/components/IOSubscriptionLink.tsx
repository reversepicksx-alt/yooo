import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Linking } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';

export default function IOSubscriptionLink() {
  const handleOpen = () => {
    Linking.openURL('https://reversepicks.com');
  };

  return (
    <View style={styles.root}>
      <View style={styles.card}>
        <View style={styles.iconWrap}>
          <Ionicons name="link-outline" size={22} color={Colors.primary} />
        </View>
        <Text style={styles.title}>Manage your subscription on our website</Text>
        <Text style={styles.body}>
          Apple requires subscription payments to be handled outside the iOS app. Visit our website to subscribe, change your plan, or manage billing.
        </Text>
        <TouchableOpacity style={styles.button} onPress={handleOpen} activeOpacity={0.8}>
          <Text style={styles.buttonText}>Open reversepicks.com</Text>
          <Ionicons name="open-outline" size={16} color={Colors.background} />
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    marginBottom: 24,
  },
  card: {
    backgroundColor: Colors.card,
    borderRadius: Colors.radiusLg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: 20,
    alignItems: 'center',
    gap: 12,
  },
  iconWrap: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: Colors.primaryDim,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 4,
  },
  title: {
    fontSize: 16,
    fontWeight: '700',
    color: Colors.text,
    textAlign: 'center',
  },
  body: {
    fontSize: 13,
    color: Colors.textSecondary,
    textAlign: 'center',
    lineHeight: 20,
  },
  button: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: Colors.primary,
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 12,
    marginTop: 4,
  },
  buttonText: {
    fontSize: 14,
    fontWeight: '700',
    color: Colors.background,
  },
});
