import { useState } from "react";

const C = {
  bg: "#050505",
  card: "#111111",
  card2: "#1A1A1A",
  primary: "#39FF14",
  text: "#FFFFFF",
  text2: "rgba(255,255,255,0.5)",
  text3: "rgba(255,255,255,0.25)",
  border: "rgba(57,255,20,0.15)",
  border2: "rgba(255,255,255,0.08)",
  error: "#FF3B30",
};

const GlassCard = ({ children, style = {}, glow = false }: any) => (
  <div
    style={{
      background: glow ? "rgba(57,255,20,0.06)" : "rgba(17,17,17,0.7)",
      backdropFilter: "blur(20px)",
      border: glow ? "1px solid rgba(57,255,20,0.2)" : "1px solid rgba(57,255,20,0.1)",
      borderRadius: 18,
      padding: 16,
      boxShadow: glow
        ? "0 8px 32px rgba(57,255,20,0.1), inset 0 1px 0 rgba(255,255,255,0.03)"
        : "0 8px 32px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.03)",
      ...style,
    }}
  >
    {children}
  </div>
);

export default function ScanResult() {
  const [saved, setSaved] = useState(false);

  return (
    <div style={{ background: C.bg, minHeight: "100vh", fontFamily: "system-ui, -apple-system, sans-serif" }}>
      {/* Header */}
      <div
        style={{
          padding: "20px 20px 12px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "rgba(8,8,8,0.85)",
          backdropFilter: "blur(20px)",
          borderBottom: "1px solid rgba(57,255,20,0.08)",
          position: "sticky",
          top: 0,
          zIndex: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              width: 36, height: 36, borderRadius: 10, background: "#111",
              border: "1px solid rgba(57,255,20,0.2)", display: "flex",
              alignItems: "center", justifyContent: "center",
              boxShadow: "0 0 12px rgba(57,255,20,0.15)",
            }}
          >
            <span style={{ color: C.primary, fontSize: 12, fontWeight: 900 }}>RP</span>
          </div>
          <div>
            <div style={{ color: C.text, fontSize: 15, fontWeight: 900, letterSpacing: 2 }}>REVERSE PICKS</div>
            <div style={{ color: C.primary, fontSize: 10, fontWeight: 600, letterSpacing: 0.5 }}>AI Player Props</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ color: C.text3, fontSize: 12 }}>85% STRONG</div>
        </div>
      </div>

      <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: 14 }}>
        {/* Verdict Card */}
        <GlassCard glow style={{ textAlign: "center", padding: 24 }}>
          <div
            style={{
              display: "inline-block",
              padding: "6px 14px",
              borderRadius: 20,
              background: "rgba(57,255,20,0.15)",
              border: "1px solid rgba(57,255,20,0.3)",
              color: C.primary,
              fontSize: 11,
              fontWeight: 800,
              letterSpacing: 1.5,
              marginBottom: 16,
            }}
          >
            VERDICT
          </div>
          <div style={{ fontSize: 42, fontWeight: 900, color: C.primary, letterSpacing: -1, marginBottom: 4 }}>
            OVER
          </div>
          <div style={{ fontSize: 13, color: C.text2, marginBottom: 20 }}>
            Kevin De Bruyne Pass Attempts vs Arsenal
          </div>
          <div style={{ display: "flex", justifyContent: "center", gap: 24 }}>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 11, color: C.text3, fontWeight: 600, marginBottom: 4 }}>LINE</div>
              <div style={{ fontSize: 20, fontWeight: 800, color: C.text }}>67.5</div>
            </div>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 11, color: C.text3, fontWeight: 600, marginBottom: 4 }}>PROJECTION</div>
              <div style={{ fontSize: 20, fontWeight: 800, color: "#4A8FFF" }}>73.2</div>
            </div>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 11, color: C.text3, fontWeight: 600, marginBottom: 4 }}>EDGE</div>
              <div style={{ fontSize: 20, fontWeight: 800, color: C.primary }}>+5.7</div>
            </div>
          </div>
        </GlassCard>

        {/* Confidence meter */}
        <GlassCard>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <span style={{ fontSize: 11, color: C.text3, fontWeight: 700, letterSpacing: 1 }}>CONFIDENCE</span>
            <span style={{ fontSize: 14, fontWeight: 800, color: C.primary }}>85%</span>
          </div>
          <div style={{ height: 6, background: "rgba(255,255,255,0.06)", borderRadius: 3, overflow: "hidden" }}>
            <div
              style={{
                width: "85%",
                height: "100%",
                borderRadius: 3,
                background: "linear-gradient(90deg, #39FF14, #2ECC40)",
                boxShadow: "0 0 8px rgba(57,255,20,0.4)",
              }}
            />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
            <span style={{ fontSize: 10, color: C.text3 }}>0%</span>
            <span style={{ fontSize: 10, color: C.text3 }}>50%</span>
            <span style={{ fontSize: 10, color: C.text3 }}>100%</span>
          </div>
        </GlassCard>

        {/* Key Insights */}
        <GlassCard>
          <div style={{ fontSize: 11, color: C.primary, fontWeight: 800, letterSpacing: 1.5, marginBottom: 12 }}>
            KEY INSIGHTS
          </div>
          {[
            { label: "Home Advantage", val: "+8.3%", good: true },
            { label: "Recent Form", val: "Hot", good: true },
            { label: "Opponent Press", val: "Low", good: true },
            { label: "Sample Size", val: "12 games", good: true },
          ].map((item, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "10px 0",
                borderBottom: i < 3 ? "1px solid rgba(255,255,255,0.05)" : "none",
              }}
            >
              <span style={{ fontSize: 13, color: C.text2, fontWeight: 500 }}>{item.label}</span>
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 700,
                  color: item.good ? C.primary : C.error,
                  background: item.good ? "rgba(57,255,20,0.08)" : "rgba(255,59,48,0.08)",
                  padding: "3px 10px",
                  borderRadius: 8,
                }}
              >
                {item.val}
              </span>
            </div>
          ))}
        </GlassCard>

        {/* Game Log Preview */}
        <GlassCard>
          <div style={{ fontSize: 11, color: C.primary, fontWeight: 800, letterSpacing: 1.5, marginBottom: 12 }}>
            LAST 5 GAMES
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {[82, 71, 88, 65, 79].map((val, i) => (
              <div
                key={i}
                style={{
                  flex: 1,
                  padding: "10px 4px",
                  borderRadius: 10,
                  background: val > 67.5 ? "rgba(57,255,20,0.08)" : "rgba(255,59,48,0.06)",
                  border: val > 67.5 ? "1px solid rgba(57,255,20,0.15)" : "1px solid rgba(255,59,48,0.1)",
                  textAlign: "center",
                }}
              >
                <div style={{ fontSize: 16, fontWeight: 900, color: val > 67.5 ? C.primary : C.error }}>{val}</div>
                <div style={{ fontSize: 9, color: C.text3, marginTop: 2 }}>{["W", "L", "W", "L", "W"][i]}</div>
              </div>
            ))}
          </div>
        </GlassCard>

        {/* Save Button */}
        <button
          onClick={() => setSaved(true)}
          style={{
            background: saved ? "rgba(57,255,20,0.1)" : C.primary,
            color: saved ? C.primary : "#000",
            border: saved ? "1px solid rgba(57,255,20,0.3)" : "none",
            borderRadius: 16,
            padding: "18px 0",
            fontSize: 16,
            fontWeight: 800,
            letterSpacing: 0.5,
            cursor: "pointer",
            marginTop: 4,
            boxShadow: saved ? "none" : "0 0 30px rgba(57,255,20,0.4), 0 8px 20px rgba(57,255,20,0.2)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            transition: "all 0.2s",
          }}
        >
          {saved ? (
            <>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={C.primary} strokeWidth="2.5">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              Saved to Picks
            </>
          ) : (
            <>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2.5">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
                <polyline points="17 21 17 13 7 13 7 21" />
                <polyline points="7 3 7 8 15 8" />
              </svg>
              Save Pick
            </>
          )}
        </button>
      </div>

      {/* Tab bar */}
      <div
        style={{
          position: "fixed", bottom: 0, left: 0, right: 0,
          background: "rgba(0,0,0,0.9)", backdropFilter: "blur(20px)",
          borderTop: "1px solid rgba(57,255,20,0.15)",
          display: "flex", justifyContent: "space-around",
          padding: "10px 0 24px", zIndex: 100,
        }}
      >
        {[
          { label: "Predict", active: true },
          { label: "Picks", active: false },
          { label: "Chat", active: false },
          { label: "Account", active: false },
        ].map((tab) => (
          <div key={tab.label} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
            <div
              style={{
                width: 24, height: 24,
                borderRadius: 6,
                background: tab.active ? "rgba(57,255,20,0.15)" : "transparent",
                display: "flex", alignItems: "center", justifyContent: "center",
              }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={tab.active ? C.primary : C.text3} strokeWidth="2">
                {tab.label === "Predict" && <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />}
                {tab.label === "Picks" && <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />}
                {tab.label === "Chat" && <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />}
                {tab.label === "Account" && <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />}
              </svg>
            </div>
            <span style={{ color: tab.active ? C.primary : C.text3, fontSize: 10, fontWeight: 600 }}>{tab.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
