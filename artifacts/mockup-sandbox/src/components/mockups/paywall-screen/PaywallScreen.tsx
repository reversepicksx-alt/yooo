const C = {
  bg: "#050505",
  primary: "#39FF14",
  text: "#FFFFFF",
  text2: "rgba(255,255,255,0.5)",
  text3: "rgba(255,255,255,0.25)",
};

const plans = [
  { label: "Weekly", price: "$9.99", period: "per week", popular: false },
  { label: "Monthly", price: "$24.99", period: "per month", popular: true },
  { label: "Quarterly", price: "$59.99", period: "per quarter", popular: false, save: "40%" },
];

const features = [
  "Unlimited AI Predictions",
  "Tactical Breakdowns",
  "Sharp Summaries",
  "Community Chat",
  "Push Notifications",
  "Game Log Analytics",
];

export default function PaywallScreen() {
  return (
    <div
      style={{
        background: C.bg,
        minHeight: "100vh",
        fontFamily: "system-ui, -apple-system, sans-serif",
        display: "flex",
        flexDirection: "column",
        padding: "0 24px 40px",
      }}
    >
      {/* Header */}
      <div style={{ padding: "24px 0 16px", textAlign: "center" }}>
        <div
          style={{
            width: 60, height: 60, borderRadius: 16,
            background: "linear-gradient(135deg, rgba(57,255,20,0.15), rgba(57,255,20,0.05))",
            border: "1px solid rgba(57,255,20,0.2)",
            display: "flex", alignItems: "center", justifyContent: "center",
            boxShadow: "0 0 30px rgba(57,255,20,0.2)",
            margin: "0 auto 16px",
          }}
        >
          <span style={{ fontSize: 24, fontWeight: 900, color: C.primary }}>RP</span>
        </div>
        <div style={{ fontSize: 22, fontWeight: 900, color: C.text, letterSpacing: 2, marginBottom: 6 }}>GO PRO</div>
        <div style={{ fontSize: 13, color: C.text2, maxWidth: 260, margin: "0 auto" }}>
          Unlock the full power of AI prop analytics
        </div>
      </div>

      {/* Plans */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 24 }}>
        {plans.map((plan) => (
          <div
            key={plan.label}
            style={{
              background: plan.popular ? "rgba(57,255,20,0.06)" : "rgba(17,17,17,0.6)",
              backdropFilter: "blur(20px)",
              border: plan.popular ? "1.5px solid rgba(57,255,20,0.3)" : "1px solid rgba(57,255,20,0.1)",
              borderRadius: 18,
              padding: 18,
              boxShadow: plan.popular
                ? "0 8px 32px rgba(57,255,20,0.1), inset 0 1px 0 rgba(255,255,255,0.03)"
                : "0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.03)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              cursor: "pointer",
              position: "relative",
            }}
          >
            {plan.popular && (
              <div
                style={{
                  position: "absolute",
                  top: -10,
                  left: "50%",
                  transform: "translateX(-50%)",
                  background: C.primary,
                  color: "#000",
                  fontSize: 10,
                  fontWeight: 800,
                  padding: "3px 12px",
                  borderRadius: 10,
                  letterSpacing: 0.5,
                }}
              >
                MOST POPULAR
              </div>
            )}
            <div>
              <div style={{ fontSize: 15, fontWeight: 800, color: C.text, marginBottom: 2 }}>{plan.label}</div>
              <div style={{ fontSize: 12, color: C.text2 }}>{plan.period}</div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ fontSize: 22, fontWeight: 900, color: C.primary }}>{plan.price}</div>
              {plan.save && (
                <div style={{ fontSize: 10, color: C.primary, fontWeight: 700, background: "rgba(57,255,20,0.1)", padding: "2px 8px", borderRadius: 6, marginTop: 2, display: "inline-block" }}>
                  Save {plan.save}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Features */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 12, color: C.primary, fontWeight: 800, letterSpacing: 1.5, marginBottom: 12, textAlign: "center" }}>
          WHAT YOU GET
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {features.map((f) => (
            <div key={f} style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div
                style={{
                  width: 24, height: 24, borderRadius: 6,
                  background: "rgba(57,255,20,0.1)",
                  border: "1px solid rgba(57,255,20,0.2)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  flexShrink: 0,
                }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={C.primary} strokeWidth="3" strokeLinecap="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              </div>
              <span style={{ fontSize: 14, color: C.text2, fontWeight: 500 }}>{f}</span>
            </div>
          ))}
        </div>
      </div>

      {/* CTA */}
      <button
        style={{
          background: C.primary,
          color: "#000",
          border: "none",
          borderRadius: 16,
          padding: "20px 0",
          fontSize: 17,
          fontWeight: 800,
          cursor: "pointer",
          boxShadow: "0 0 30px rgba(57,255,20,0.4), 0 8px 20px rgba(57,255,20,0.2)",
          marginBottom: 12,
        }}
      >
        Subscribe Now
      </button>
      <button
        style={{
          background: "transparent",
          color: C.text2,
          border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: 14,
          padding: "14px 0",
          fontSize: 14,
          fontWeight: 600,
          cursor: "pointer",
        }}
      >
        Restore Purchases
      </button>

      <div style={{ textAlign: "center", marginTop: 16 }}>
        <span style={{ fontSize: 11, color: C.text3 }}>
          <span style={{ color: C.primary, cursor: "pointer" }}>Terms</span> ·{" "}
          <span style={{ color: C.primary, cursor: "pointer" }}>Privacy</span> ·{" "}
          <span style={{ color: C.primary, cursor: "pointer" }}>EULA</span>
        </span>
      </div>
    </div>
  );
}
