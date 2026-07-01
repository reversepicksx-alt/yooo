const C = {
  bg: "#050505",
  card: "#111111",
  card2: "#1A1A1A",
  primary: "#39FF14",
  text: "#FFFFFF",
  text2: "rgba(255,255,255,0.5)",
  text3: "rgba(255,255,255,0.25)",
  border: "rgba(57,255,20,0.15)",
  error: "#FF3B30",
  push: "#0A84FF",
};

const GlassCard = ({ children, style = {}, glow = false, red = false }: any) => (
  <div
    style={{
      background: red ? "rgba(255,59,48,0.04)" : glow ? "rgba(57,255,20,0.04)" : "rgba(17,17,17,0.7)",
      backdropFilter: "blur(20px)",
      border: red ? "1px solid rgba(255,59,48,0.12)" : glow ? "1px solid rgba(57,255,20,0.12)" : "1px solid rgba(57,255,20,0.08)",
      borderRadius: 18,
      padding: 16,
      boxShadow: "0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.03)",
      ...style,
    }}
  >
    {children}
  </div>
);

const picks = [
  { player: "K. De Bruyne", prop: "Pass Attempts", line: "67.5", rec: "OVER", conf: "85%", result: "pending", league: "Premier League" },
  { player: "E. Haaland", prop: "Shots", line: "3.5", rec: "OVER", conf: "78%", result: "hit", league: "Premier League" },
  { player: "B. Saka", prop: "Key Passes", line: "2.5", rec: "UNDER", conf: "72%", result: "miss", league: "Premier League" },
  { player: "M. Salah", prop: "Goals", line: "0.5", rec: "OVER", conf: "68%", result: "hit", league: "Premier League" },
  { player: "C. Palmer", prop: "Dribbles", line: "2.5", rec: "UNDER", conf: "65%", result: "pending", league: "Premier League" },
];

export default function PicksScreen() {
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
        <div>
          <div style={{ color: C.text, fontSize: 22, fontWeight: 900 }}>My Picks</div>
          <div style={{ color: C.text2, fontSize: 12, marginTop: 2 }}>{picks.length} active predictions</div>
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 20, fontWeight: 900, color: C.primary }}>2</div>
            <div style={{ fontSize: 10, color: C.text3, fontWeight: 600 }}>HITS</div>
          </div>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 20, fontWeight: 900, color: C.error }}>1</div>
            <div style={{ fontSize: 10, color: C.text3, fontWeight: 600 }}>MISSES</div>
          </div>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 20, fontWeight: 900, color: C.push }}>2</div>
            <div style={{ fontSize: 10, color: C.text3, fontWeight: 600 }}>PENDING</div>
          </div>
        </div>
      </div>

      <div style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
        {picks.map((pick, i) => (
          <GlassCard key={i} glow={pick.result === "hit"} red={pick.result === "miss"}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
              <div>
                <div style={{ fontSize: 15, fontWeight: 800, color: C.text, marginBottom: 2 }}>{pick.player}</div>
                <div style={{ fontSize: 12, color: C.text2 }}>{pick.prop} · Line {pick.line}</div>
              </div>
              <div
                style={{
                  padding: "4px 10px",
                  borderRadius: 20,
                  fontSize: 11,
                  fontWeight: 800,
                  letterSpacing: 0.5,
                  background:
                    pick.result === "hit"
                      ? "rgba(57,255,20,0.15)"
                      : pick.result === "miss"
                      ? "rgba(255,59,48,0.15)"
                      : "rgba(10,132,255,0.12)",
                  color:
                    pick.result === "hit" ? C.primary : pick.result === "miss" ? C.error : C.push,
                  border:
                    pick.result === "hit"
                      ? "1px solid rgba(57,255,20,0.25)"
                      : pick.result === "miss"
                      ? "1px solid rgba(255,59,48,0.2)"
                      : "1px solid rgba(10,132,255,0.2)",
                }}
              >
                {pick.result === "pending" ? "PENDING" : pick.result === "hit" ? "HIT" : "MISS"}
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: 800,
                    color: pick.rec === "OVER" ? C.primary : C.error,
                  }}
                >
                  {pick.rec}
                </span>
                <span style={{ fontSize: 10, color: C.text3 }}>·</span>
                <span style={{ fontSize: 11, color: C.primary, fontWeight: 700, background: "rgba(57,255,20,0.08)", padding: "2px 8px", borderRadius: 6 }}>
                  {pick.conf} STRONG
                </span>
              </div>
              <span style={{ fontSize: 11, color: C.text3, fontWeight: 500 }}>{pick.league}</span>
            </div>
            {/* Track bar */}
            <div style={{ marginTop: 10, height: 6, background: "rgba(255,255,255,0.06)", borderRadius: 3, overflow: "hidden", position: "relative" }}>
              <div
                style={{
                  position: "absolute",
                  left: "50%",
                  top: 0,
                  bottom: 0,
                  width: 2,
                  background: C.text3,
                  zIndex: 2,
                }}
              />
              <div
                style={{
                  width: pick.rec === "OVER" ? `${parseInt(pick.conf)}%` : `${100 - parseInt(pick.conf)}%`,
                  height: "100%",
                  borderRadius: 3,
                  background:
                    pick.result === "hit"
                      ? "linear-gradient(90deg, #39FF14, #2ECC40)"
                      : pick.result === "miss"
                      ? "linear-gradient(90deg, #FF3B30, #FF6B6B)"
                      : "linear-gradient(90deg, #0A84FF, #4A8FFF)",
                  boxShadow: "0 0 8px rgba(57,255,20,0.3)",
                }}
              />
            </div>
          </GlassCard>
        ))}
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
          { label: "Predict", active: false },
          { label: "Picks", active: true },
          { label: "Chat", active: false },
          { label: "Account", active: false },
        ].map((tab) => (
          <div key={tab.label} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
            <div style={{ width: 24, height: 24, borderRadius: 6, background: tab.active ? "rgba(57,255,20,0.15)" : "transparent", display: "flex", alignItems: "center", justifyContent: "center" }}>
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
