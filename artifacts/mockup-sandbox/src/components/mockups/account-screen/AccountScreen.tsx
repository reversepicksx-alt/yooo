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
  error: "#FF3B30",
};

const GlassCard = ({ children, style = {}, glow = false }: any) => (
  <div
    style={{
      background: glow ? "rgba(57,255,20,0.04)" : "rgba(17,17,17,0.7)",
      backdropFilter: "blur(20px)",
      border: glow ? "1px solid rgba(57,255,20,0.15)" : "1px solid rgba(57,255,20,0.08)",
      borderRadius: 18,
      padding: 16,
      boxShadow: "0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.03)",
      ...style,
    }}
  >
    {children}
  </div>
);

const MenuRow = ({ icon, label, value, danger = false }: any) => (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      padding: "14px 0",
      borderBottom: "1px solid rgba(255,255,255,0.04)",
      cursor: "pointer",
    }}
  >
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={danger ? C.error : C.text2} strokeWidth="2" strokeLinecap="round">
        {icon === "person" && <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />}
        {icon === "mail" && <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />}
        {icon === "shield" && <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />}
        {icon === "card" && <rect x="1" y="4" width="22" height="16" rx="2" />}
        {icon === "log-out" && <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />}
        {icon === "trash" && <polyline points="3 6 5 6 21 6" />}
      </svg>
      <span style={{ fontSize: 14, fontWeight: 600, color: danger ? C.error : C.text }}>{label}</span>
    </div>
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {value && <span style={{ fontSize: 13, color: C.text3, fontWeight: 500 }}>{value}</span>}
      <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={C.text3} strokeWidth="2">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </div>
  </div>
);

export default function AccountScreen() {
  const [usernameModal, setUsernameModal] = useState(false);
  const [username, setUsername] = useState("");

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
        <div style={{ color: C.text, fontSize: 22, fontWeight: 900 }}>Account</div>
        <div
          style={{
            width: 36, height: 36, borderRadius: 10,
            background: "rgba(17,17,17,0.6)", border: "1px solid rgba(255,255,255,0.08)",
            display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer",
          }}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={C.text2} strokeWidth="2">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.73 21a2 2 0 0 1-3.46 0" />
          </svg>
        </div>
      </div>

      <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: 14 }}>
        {/* Profile Card */}
        <GlassCard glow style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div
            style={{
              width: 56, height: 56, borderRadius: 16,
              background: "linear-gradient(135deg, rgba(57,255,20,0.2), rgba(57,255,20,0.05))",
              border: "1px solid rgba(57,255,20,0.2)",
              display: "flex", alignItems: "center", justifyContent: "center",
              boxShadow: "0 0 20px rgba(57,255,20,0.15)",
            }}
          >
            <span style={{ fontSize: 22, fontWeight: 900, color: C.primary }}>J</span>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 17, fontWeight: 800, color: C.text }}>jossel@email.com</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
              <span style={{ fontSize: 12, color: C.primary, fontWeight: 700, background: "rgba(57,255,20,0.1)", padding: "2px 10px", borderRadius: 8, border: "1px solid rgba(57,255,20,0.15)" }}>
                PRO
              </span>
              <span style={{ fontSize: 12, color: C.text3 }}>@sharp_bettor</span>
            </div>
          </div>
          <button
            onClick={() => setUsernameModal(true)}
            style={{
              padding: "6px 12px",
              borderRadius: 8,
              background: "rgba(57,255,20,0.08)",
              border: "1px solid rgba(57,255,20,0.15)",
              color: C.primary,
              fontSize: 11,
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            Edit
          </button>
        </GlassCard>

        {/* Stats */}
        <div style={{ display: "flex", gap: 10 }}>
          {[
            { label: "Picks", val: "47", sub: "all time" },
            { label: "Hit Rate", val: "68%", sub: "win rate" },
            { label: "Streak", val: "5", sub: "current" },
          ].map((stat) => (
            <GlassCard key={stat.label} style={{ flex: 1, textAlign: "center", padding: 14 }}>
              <div style={{ fontSize: 22, fontWeight: 900, color: C.primary, marginBottom: 2 }}>{stat.val}</div>
              <div style={{ fontSize: 11, color: C.text2, fontWeight: 600 }}>{stat.label}</div>
              <div style={{ fontSize: 9, color: C.text3, marginTop: 2 }}>{stat.sub}</div>
            </GlassCard>
          ))}
        </div>

        {/* Menu */}
        <GlassCard>
          <div style={{ fontSize: 11, color: C.primary, fontWeight: 800, letterSpacing: 1.5, marginBottom: 8 }}>
            SETTINGS
          </div>
          <MenuRow icon="person" label="Username" value="@sharp_bettor" />
          <MenuRow icon="mail" label="Email" value="jossel@email.com" />
          <MenuRow icon="shield" label="Access Level" value="Pro Member" />
          <MenuRow icon="card" label="Subscription" value="Monthly" />
        </GlassCard>

        <GlassCard>
          <div style={{ fontSize: 11, color: C.primary, fontWeight: 800, letterSpacing: 1.5, marginBottom: 8 }}>
            LEGAL
          </div>
          <MenuRow icon="shield" label="Terms of Use" />
          <MenuRow icon="shield" label="Privacy Policy" />
        </GlassCard>

        <GlassCard>
          <MenuRow icon="log-out" label="Sign Out" danger />
          <MenuRow icon="trash" label="Delete Account" danger />
        </GlassCard>
      </div>

      {/* Username Modal */}
      {usernameModal && (
        <div
          style={{
            position: "fixed", inset: 0,
            background: "rgba(0,0,0,0.85)",
            backdropFilter: "blur(10px)",
            display: "flex", alignItems: "center", justifyContent: "center",
            zIndex: 200, padding: 20,
          }}
          onClick={() => setUsernameModal(false)}
        >
          <div
            style={{
              background: "rgba(17,17,17,0.95)",
              border: "1px solid rgba(57,255,20,0.15)",
              borderRadius: 20,
              padding: 24,
              width: "100%", maxWidth: 320,
              boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Change Username</div>
            <div style={{ fontSize: 12, color: C.text3, marginBottom: 16 }}>3-20 characters. Letters, numbers, underscores.</div>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="e.g. soccer_fan_99"
              style={{
                width: "100%",
                background: "rgba(17,17,17,0.6)",
                border: "1px solid rgba(57,255,20,0.15)",
                borderRadius: 12,
                color: C.text,
                padding: "12px 14px",
                fontSize: 14,
                outline: "none",
                marginBottom: 16,
              }}
            />
            <button
              style={{
                width: "100%",
                background: C.primary,
                color: "#000",
                border: "none",
                borderRadius: 12,
                padding: "14px 0",
                fontSize: 15,
                fontWeight: 800,
                cursor: "pointer",
                boxShadow: "0 0 20px rgba(57,255,20,0.3)",
              }}
            >
              Save
            </button>
          </div>
        </div>
      )}

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
          { label: "Picks", active: false },
          { label: "Chat", active: false },
          { label: "Account", active: true },
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
