const C = {
  bg: "#050505",
  card: "#111111",
  card2: "#1A1A1A",
  primary: "#39FF14",
  text: "#FFFFFF",
  text2: "rgba(255,255,255,0.5)",
  text3: "rgba(255,255,255,0.25)",
  border: "rgba(57,255,20,0.15)",
};

const messages = [
  { id: 1, user: "soccer_fan_99", text: "KDB over 67.5 passes looks solid today", time: "2m ago", self: false, mention: false },
  { id: 2, user: "prop_hunter", text: "@soccer_fan_99 agree, Arsenal midfield is weak without Partey", time: "1m ago", self: false, mention: true },
  { id: 3, user: "sharp_bettor", text: "Haaland shots over 3.5 is free money ", time: "Just now", self: false, mention: false },
  { id: 4, user: "You", text: "Anyone looking at Saka key passes? Line looks low", time: "Just now", self: true, mention: false },
];

const GlassCard = ({ children, style = {} }: any) => (
  <div
    style={{
      background: "rgba(17,17,17,0.7)",
      backdropFilter: "blur(20px)",
      border: "1px solid rgba(57,255,20,0.08)",
      borderRadius: 18,
      padding: 16,
      boxShadow: "0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.03)",
      ...style,
    }}
  >
    {children}
  </div>
);

const avatarColors = ["#39FF14", "#0A84FF", "#FF6B6B", "#FFD700", "#B14FFF", "#FF8C00"];

export default function ChatScreen() {
  return (
    <div style={{ background: C.bg, minHeight: "100vh", fontFamily: "system-ui, -apple-system, sans-serif", display: "flex", flexDirection: "column" }}>
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
          <div style={{ color: C.text, fontSize: 20, fontWeight: 900 }}>Reverse Chat</div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
            <div style={{ width: 6, height: 6, borderRadius: 3, background: C.primary, boxShadow: "0 0 6px rgba(57,255,20,0.5)" }} />
            <span style={{ color: C.text2, fontSize: 12 }}>47 online</span>
          </div>
        </div>
        <div
          style={{
            width: 36, height: 36, borderRadius: 10,
            background: "rgba(17,17,17,0.6)", border: "1px solid rgba(255,255,255,0.08)",
            display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer",
            position: "relative",
          }}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={C.text2} strokeWidth="2">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.73 21a2 2 0 0 1-3.46 0" />
          </svg>
          <div
            style={{
              position: "absolute", top: -2, right: -2,
              width: 16, height: 16, borderRadius: 8,
              background: C.error, border: "2px solid #000",
              fontSize: 9, fontWeight: 800, color: "#fff",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            3
          </div>
        </div>
      </div>

      {/* Messages */}
      <div style={{ flex: 1, padding: "16px 20px", display: "flex", flexDirection: "column", gap: 12, overflowY: "auto" }}>
        {messages.map((msg) => {
          const colorIdx = msg.user.charCodeAt(0) % avatarColors.length;
          return (
            <div
              key={msg.id}
              style={{
                display: "flex",
                gap: 10,
                flexDirection: msg.self ? "row-reverse" : "row",
                alignItems: "flex-start",
              }}
            >
              {!msg.self && (
                <div
                  style={{
                    width: 32, height: 32, borderRadius: 10,
                    background: avatarColors[colorIdx] + "20",
                    border: `1px solid ${avatarColors[colorIdx]}40`,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  <span style={{ fontSize: 12, fontWeight: 800, color: avatarColors[colorIdx] }}>
                    {msg.user[0].toUpperCase()}
                  </span>
                </div>
              )}
              <div style={{ maxWidth: "75%" }}>
                {!msg.self && (
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                    <span style={{ fontSize: 11, fontWeight: 700, color: avatarColors[colorIdx] }}>{msg.user}</span>
                    <span style={{ fontSize: 10, color: C.text3 }}>{msg.time}</span>
                  </div>
                )}
                <div
                  style={{
                    padding: "10px 14px",
                    borderRadius: 14,
                    background: msg.self ? "rgba(57,255,20,0.1)" : "rgba(17,17,17,0.7)",
                    border: msg.self ? "1px solid rgba(57,255,20,0.2)" : "1px solid rgba(255,255,255,0.06)",
                    backdropFilter: "blur(10px)",
                    boxShadow: "0 4px 12px rgba(0,0,0,0.2)",
                  }}
                >
                  <span style={{ fontSize: 13, color: C.text, lineHeight: 1.5, fontWeight: 400 }}>
                    {msg.text.split(/(@\w+)/g).map((part, i) =>
                      part.startsWith("@") ? (
                        <span key={i} style={{ color: C.primary, fontWeight: 700 }}>{part}</span>
                      ) : part
                    )}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Input */}
      <div
        style={{
          padding: "12px 20px 30px",
          background: "rgba(0,0,0,0.9)",
          backdropFilter: "blur(20px)",
          borderTop: "1px solid rgba(57,255,20,0.1)",
          display: "flex", gap: 10, alignItems: "center",
        }}
      >
        <input
          placeholder="Message Reverse Chat..."
          style={{
            flex: 1,
            background: "rgba(17,17,17,0.6)",
            border: "1px solid rgba(57,255,20,0.12)",
            borderRadius: 16,
            color: C.text,
            padding: "12px 16px",
            fontSize: 14,
            outline: "none",
            backdropFilter: "blur(10px)",
          }}
        />
        <button
          style={{
            width: 40, height: 40, borderRadius: 12,
            background: C.primary,
            border: "none", cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
            boxShadow: "0 0 16px rgba(57,255,20,0.3)",
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2.5" strokeLinecap="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9" />
          </svg>
        </button>
      </div>
    </div>
  );
}
