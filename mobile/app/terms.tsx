import { ScrollView, View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';

export default function TermsScreen() {
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} hitSlop={12} style={styles.backBtn}>
          <Ionicons name="chevron-back" size={22} color={Colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Terms of Use</Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 32 }]}
        showsVerticalScrollIndicator={false}
      >
        <Text style={styles.updated}>Last updated: June 1, 2026</Text>

        <Text style={styles.section}>1. Acceptance of Terms</Text>
        <Text style={styles.body}>
          By downloading, installing, or using Reverse Picks ("the App"), you agree to be bound by these Terms of Use. If you do not agree, do not use the App.
        </Text>

        <Text style={styles.section}>2. Description of Service</Text>
        <Text style={styles.body}>
          Reverse Picks provides deterministic player prop analytics and predictions for informational and entertainment purposes only. The App does not facilitate, facilitate, or encourage gambling. All predictions are statistical projections and do not constitute financial, legal, or betting advice. Past performance of predictions does not guarantee future results.
        </Text>

        <Text style={styles.section}>3. Subscriptions and Billing</Text>
        <Text style={styles.body}>
          Reverse Picks offers auto-renewable subscription plans:{'\n\n'}
          • <Text style={styles.bold}>Weekly</Text> — access renews every 7 days{'\n\n'}
          • <Text style={styles.bold}>Monthly</Text> — access renews every 30 days{'\n\n'}
          • <Text style={styles.bold}>Quarterly</Text> — access renews every 3 months{'\n\n'}
          Payment is charged to your Apple ID account at confirmation of purchase. Your subscription automatically renews unless cancelled at least 24 hours before the end of the current period. You can manage and cancel subscriptions in your Apple ID Account Settings at any time. No refunds are provided for partial subscription periods.
        </Text>

        <Text style={styles.section}>4. Account</Text>
        <Text style={styles.body}>
          You must create an account using a valid email address. You are responsible for maintaining the confidentiality of your login credentials and for all activity that occurs under your account. You must be at least 17 years of age to use the App.
        </Text>

        <Text style={styles.section}>5. Permitted Use</Text>
        <Text style={styles.body}>
          You may use the App for personal, non-commercial purposes only. You agree not to:{'\n\n'}
          • Reproduce, distribute, or resell predictions or content from the App.{'\n\n'}
          • Attempt to reverse-engineer, scrape, or automate access to the App.{'\n\n'}
          • Use the App in any way that violates applicable laws or regulations.
        </Text>

        <Text style={styles.section}>6. Intellectual Property</Text>
        <Text style={styles.body}>
          All content, algorithms, models, and software within Reverse Picks are the proprietary property of Reverse Picks and are protected by applicable intellectual property laws. These Terms do not grant you any rights to our trademarks or intellectual property.
        </Text>
        <Text style={styles.body}>
          Player photos, team crests, league marks, and other third-party visual assets may appear alongside predictions for identification and informational purposes only. These assets are not sold separately by Reverse Picks, and ownership remains with the applicable rights holders. Their use remains subject to any applicable provider, league, club, player, photographer, or other rights-holder terms.
        </Text>

        <Text style={styles.section}>7. Disclaimer of Warranties</Text>
        <Text style={styles.body}>
          The App is provided "as is" without warranties of any kind, express or implied. We do not warrant that the App will be error-free, uninterrupted, or that predictions will be accurate. Your use of the App is at your sole risk.
        </Text>

        <Text style={styles.section}>8. Limitation of Liability</Text>
        <Text style={styles.body}>
          To the maximum extent permitted by law, Reverse Picks shall not be liable for any indirect, incidental, special, consequential, or punitive damages arising from your use of the App, including any losses arising from reliance on predictions or analytics provided by the service.
        </Text>

        <Text style={styles.section}>9. Termination</Text>
        <Text style={styles.body}>
          We reserve the right to suspend or terminate your account at any time for violations of these Terms. You may delete your account at any time through the Account tab in the App.
        </Text>

        <Text style={styles.section}>10. Changes to Terms</Text>
        <Text style={styles.body}>
          We may update these Terms from time to time. Continued use of the App after changes take effect constitutes acceptance of the revised Terms. Significant changes will be communicated through the App or by email.
        </Text>

        <Text style={styles.section}>11. Governing Law</Text>
        <Text style={styles.body}>
          These Terms are governed by the laws of the applicable jurisdiction. Any disputes shall be resolved through binding arbitration or in the courts of competent jurisdiction.
        </Text>

        <Text style={styles.section}>12. Contact</Text>
        <Text style={styles.body}>
          For questions about these Terms, contact us at:{'\n'}
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
