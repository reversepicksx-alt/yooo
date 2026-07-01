const C = {
  bg: "#050505",
  primary: "#39FF14",
  text: "#FFFFFF",
  text2: "rgba(255,255,255,0.5)",
  text3: "rgba(255,255,255,0.25)",
};

export default function TermsScreen() {
  return (
    <div style={{ background: C.bg, minHeight: "100vh", fontFamily: "system-ui, -apple-system, sans-serif", padding: "20px" }}>
      <div style={{ fontSize: 22, fontWeight: 900, color: C.text, marginBottom: 16 }}>Terms of Use</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {[
          { title: "1. Acceptance", body: "By using Reverse Picks, you agree to these terms." },
          { title: "2. Subscription", body: "Subscriptions auto-renew unless cancelled 24h before renewal." },
          { title: "3. Content", body: "All predictions are for entertainment purposes only." },
          { title: "4. Privacy", body: "We collect minimal data needed for app functionality." },
          { title: "5. Contact", body: "support@reversepicks.com" },
        ].map((s, i) => (
          <div key={i} style={{ padding: 14, borderRadius: 14, background: "rgba(17,17,17,0.6)", border: "1px solid rgba(57,255,20,0.08)" }}>
            <div style={{ fontSize: 14, fontWeight: 800, color: C.primary, marginBottom: 6 }}>{s.title}</div>
            <div style={{ fontSize: 13, color: C.text2, lineHeight: 1.5 }}>{s.body}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
