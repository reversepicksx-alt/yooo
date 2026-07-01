const C = {
  bg: "#050505",
  primary: "#39FF14",
  text: "#FFFFFF",
  text2: "rgba(255,255,255,0.5)",
  text3: "rgba(255,255,255,0.25)",
  error: "#FF3B30",
  push: "#0A84FF",
};

const notifications = [
  { type: "hit", title: "K. De Bruyne Pass Attempts", body: "Actual: 74 · Line: 67.5 · HIT", time: "2h ago", read: false },
  { type: "miss", title: "B. Saka Key Passes", body: "Actual: 1 · Line: 2.5 · MISSED", time: "5h ago", read: false },
  { type: "mention", title: "@prop_hunter mentioned you", body: "@soccer_fan_99 agree, Arsenal midfield is weak...", time: "1h ago", read: true },
  { type: "hit", title: "M. Salah Goals", body: "Actual: 2 · Line: 0.5 · HIT", time: "1d ago", read: true },
  { type: "push", title: "E. Haaland Shots", body: "Actual: 4 · Line: 3.5 · PUSH", time: "2d ago", read: true },
];

const GlassCard = ({ children, style = {}, unread = false }: any) => (
  <div
    style={{
      background: unread ? "rgba(57,255,20,0.03)" : "rgba(17,17,17,0.5)",
      backdropFilter: "blur(20px)",
      border: unread ? "1px solid rgba(57,255,20,0.1)" : "1px solid rgba(255,255,255,0.05)",
      borderRadius: 16,
      padding: 14,
      boxShadow: "0 4px 16px rgba(0,0,0,0.2)",
      ...style,
    }}
  >
    {children}
  </div>
);

export default function NotificationsScreen() {
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
          <div style={{ fontSize: 20, fontWeight: 900, color: C.text }}>Notifications</div>
          <div style={{ fontSize: 12, color: C.text2, marginTop: 2 }}>2 unread</div>
        </div>
        <button
          style={{
            padding: "6px 14px",
            borderRadius: 10,
            background: "rgba(57,255,20,0.08)",
            border: "1px solid rgba(57,255,20,0.15)",
            color: C.primary,
            fontSize: 11,
            fontWeight: 700,
            cursor: "pointer",
          }}
        >
          Mark All Read
        </button>
      </div>

      <div style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
        {notifications.map((n, i) => {
          const color = n.type === "hit" ? C.primary : n.type === "miss" ? C.error : n.type === "mention" ? "#FFD700" : C.push;
          return (
            <GlassCard key={i} unread={!n.read}>
              <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
                <div
                  style={{
                    width: 36, height: 36, borderRadius: 10,
                    background: color + "15",
                    border: `1px solid ${color}30`,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round">
                    {n.type === "hit" && <polyline points="20 6 9 17 4 12" />}
                    {n.type === "miss" && <line x1="18" y1="6" x2="6" y2="18" />}
                    {n.type === "mention" && <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />}
                    {n.type === "push" && <circle cx="12" cy="12" r="10" />}
                  </svg>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 3 }}>
                    <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>{n.title}</span>
                    <span style={{ fontSize: 10, color: C.text3 }}>{n.time}</span>
                  </div>
                  <span style={{ fontSize: 12, color: C.text2, lineHeight: 1.5 }}>{n.body}</span>
                </div>
                {!n.read && (
                  <div
                    style={{
                      width: 8, height: 8, borderRadius: 4,
                      background: C.primary,
                      boxShadow: "0 0 6px rgba(57,255,20,0.5)",
                      flexShrink: 0,
                      marginTop: 4,
                    }}
                  />
                )}
              </div>
            </GlassCard>
          );
        })}
      </div>
    </div>
  );
}
