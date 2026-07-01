import { ScrollView, View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';

export default function PrivacyPolicyScreen() {
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} hitSlop={12} style={styles.backBtn}>
          <Ionicons name="chevron-back" size={22} color={Colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Privacy Policy</Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 32 }]}
        showsVerticalScrollIndicator={false}
      >
        <Text style={styles.updated}>Last updated: June 1, 2026</Text>

        <Text style={styles.section}>1. Information We Collect</Text>
        <Text style={styles.body}>
          ReversePicks collects the following information to provide and improve the service:{'\n\n'}
          • <Text style={styles.bold}>Email address</Text> — used to identify your account and send important service notifications.{'\n\n'}
          • <Text style={styles.bold}>Subscription and purchase data</Text> — processed through Apple's App Store and RevenueCat. We receive confirmation of active entitlements but never see your payment card details.{'\n\n'}
          • <Text style={styles.bold}>Usage data</Text> — the player props and predictions you generate, stored to power your Picks history and improve our models.{'\n\n'}
          • <Text style={styles.bold}>Device and session data</Text> — standard analytics (OS version, session duration) to monitor app stability. No advertising identifiers are collected.
        </Text>

        <Text style={styles.section}>2. How We Use Your Information</Text>
        <Text style={styles.body}>
          We use your information exclusively to:{'\n\n'}
          • Provide the ReversePicks prediction and analytics service.{'\n\n'}
          • Authenticate your account and manage your subscription status.{'\n\n'}
          • Send transactional emails (account creation, subscription receipts). We do not send marketing emails without explicit consent.{'\n\n'}
          • Improve prediction accuracy by analysing anonymised, aggregated prop outcomes.
        </Text>

        <Text style={styles.section}>3. Data Sharing</Text>
        <Text style={styles.body}>
          We do not sell, rent, or trade your personal information to third parties. We share data only with:{'\n\n'}
          • <Text style={styles.bold}>Apple / App Store</Text> — for In-App Purchase processing and subscription management.{'\n\n'}
          • <Text style={styles.bold}>RevenueCat</Text> — for subscription entitlement tracking. RevenueCat's privacy policy is available at revenuecat.com/privacy.{'\n\n'}
          • <Text style={styles.bold}>Google Gemini / xAI Grok</Text> — anonymised match context is sent to AI providers to generate predictions. No personal identifying information is included in these requests.
        </Text>

        <Text style={styles.section}>4. Data Retention</Text>
        <Text style={styles.body}>
          Your account data is retained for as long as your account is active. You may request deletion of your account and associated data at any time through the Account tab inside the app, or by emailing support@reversepicks.com. Upon deletion, your personal data is removed within 30 days.
        </Text>

        <Text style={styles.section}>5. Security</Text>
        <Text style={styles.body}>
          We use industry-standard security practices including encrypted connections (TLS), hashed passwords, and access-controlled databases. No system is completely secure, but we take reasonable measures to protect your data.
        </Text>

        <Text style={styles.section}>6. Children's Privacy</Text>
        <Text style={styles.body}>
          ReversePicks is intended for users 17 years of age and older, consistent with its App Store age rating. We do not knowingly collect personal information from children under 13.
        </Text>

        <Text style={styles.section}>7. Your Rights</Text>
        <Text style={styles.body}>
          Depending on your jurisdiction, you may have rights to access, correct, or delete your personal data. To exercise any of these rights, contact us at support@reversepicks.com.
        </Text>

        <Text style={styles.section}>8. Changes to This Policy</Text>
        <Text style={styles.body}>
          We may update this Privacy Policy from time to time. Significant changes will be communicated through the app or by email. Continued use of the service after changes take effect constitutes acceptance of the updated policy.
        </Text>

        <Text style={styles.section}>9. Contact</Text>
        <Text style={styles.body}>
          For privacy-related questions, contact us at:{'\n'}
          support@reversepicks.com
        </Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
  },
  backBtn: { width: 40, alignItems: 'flex-start' },
  headerTitle: { color: Colors.text, fontSize: 16, fontWeight: '700' },
  scroll: { flex: 1 },
  content: { paddingHorizontal: 20, paddingTop: 20, gap: 4 },
  updated: { color: Colors.textTertiary, fontSize: 12, marginBottom: 12 },
  section: { color: Colors.primary, fontSize: 14, fontWeight: '700', marginTop: 18, marginBottom: 4 },
  body: { color: Colors.textSecondary, fontSize: 13, lineHeight: 20 },
  bold: { fontWeight: '700', color: Colors.text },
});
