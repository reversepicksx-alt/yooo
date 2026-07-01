import { useState } from "react";

const SPORTS = [
  { id: "soccer", label: "Soccer", emoji: "⚽", color: "#39FF14" },
];

const PROP_TYPES = [
  { value: "pass_attempts", label: "Pass Attempts" },
  { value: "shots", label: "Shots" },
  { value: "shots_on_target", label: "Shots on Target" },
  { value: "goals", label: "Goals" },
  { value: "assists", label: "Assists" },
];

export default function ScanIdle() {
  const [sport, setSport] = useState("soccer");
  const [propType, setPropType] = useState("pass_attempts");
  const [line, setLine] = useState("");
  const [venue, setVenue] = useState("home");
  const [player, setPlayer] = useState("");
  const [opponent, setOpponent] = useState("");

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
  };

  const GlassCard = ({ children, style = {} }: any) => (
    <div
      style={{
        background: "rgba(17,17,17,0.7)",
        backdropFilter: "blur(20px)",
        border: "1px solid rgba(57,255,20,0.1)",
        borderRadius: 18,
        padding: 16,
        boxShadow: "0 8px 32px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.03)",
        ...style,
      }}
    >
      {children}
    </div>
  );

  const GlowBtn = ({ children, onClick, active = false, style = {} }: any) => (
    <button
      onClick={onClick}
      style={{
        background: active ? C.primary : "rgba(17,17,17,0.6)",
        color: active ? "#000" : C.text,
        border: active ? `1px solid ${C.primary}` : "1px solid rgba(57,255,20,0.15)",
        borderRadius: 14,
        padding: "14px 0",
        fontWeight: 700,
        fontSize: 14,
        letterSpacing: 0.5,
        cursor: "pointer",
        backdropFilter: "blur(10px)",
        boxShadow: active
          ? "0 0 24px rgba(57,255,20,0.35), 0 4px 12px rgba(57,255,20,0.15)"
          : "0 4px 12px rgba(0,0,0,0.3)",
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        transition: "all 0.2s ease",
        ...style,
      }}
    >
      {children}
    </button>
  );

  const Input = ({ placeholder, value, onChange, type = "text" }: any) => (
    <input
      type={type}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{
        background: "rgba(17,17,17,0.6)",
        border: "1px solid rgba(57,255,20,0.12)",
        borderRadius: 14,
        color: C.text,
        padding: "14px 16px",
        fontSize: 15,
        fontWeight: 500,
        width: "100%",
        outline: "none",
        backdropFilter: "blur(10px)",
        boxShadow: "0 4px 12px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.02)",
      }}
    />
  );

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
              width: 36,
              height: 36,
              borderRadius: 10,
              background: "#111",
              border: "1px solid rgba(57,255,20,0.2)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
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
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: 10,
            background: "rgba(17,17,17,0.6)",
            border: "1px solid rgba(255,255,255,0.08)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
          }}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={C.text2} strokeWidth="2" strokeLinecap="round">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.73 21a2 2 0 0 1-3.46 0" />
          </svg>
        </div>
      </div>

      {/* Content */}
      <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: 12 }}>
        {/* Sport selector */}
        <GlassCard style={{ padding: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: 8,
                background: "rgba(57,255,20,0.08)",
                border: "1px solid rgba(57,255,20,0.15)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <span style={{ fontSize: 16 }}>⚽</span>
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ color: C.text, fontSize: 14, fontWeight: 700 }}>Soccer</div>
              <div style={{ color: C.text3, fontSize: 10, fontWeight: 500 }}>Active sport</div>
            </div>
          </div>
        </GlassCard>

        {/* Player input */}
        <Input placeholder="e.g. Kevin De Bruyne" value={player} onChange={setPlayer} />

        {/* Opponent input */}
        <Input placeholder="e.g. Arsenal, Real Madrid…" value={opponent} onChange={setOpponent} />

        {/* Prop + Line row */}
        <div style={{ display: "flex", gap: 10 }}>
          <select
            value={propType}
            onChange={(e) => setPropType(e.target.value)}
            style={{
              flex: 3,
              background: "rgba(17,17,17,0.6)",
              border: "1px solid rgba(57,255,20,0.12)",
              borderRadius: 14,
              color: C.text,
              padding: "14px 16px",
              fontSize: 15,
              fontWeight: 500,
              outline: "none",
              appearance: "none",
              backdropFilter: "blur(10px)",
            }}
          >
            {PROP_TYPES.map((p) => (
              <option key={p.value} value={p.value} style={{ background: "#111" }}>
                {p.label}
              </option>
            ))}
          </select>
          <Input
            placeholder="Line"
            value={line}
            onChange={setLine}
            type="number"
            style={{ flex: 2 }}
          />
        </div>

        {/* Venue toggle */}
        <div style={{ display: "flex", gap: 8 }}>
          {["home", "neutral", "away"].map((v) => (
            <GlowBtn key={v} active={venue === v} onClick={() => setVenue(v)}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                {v === "home" && <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />}
                {v === "neutral" && <circle cx="12" cy="12" r="10" />}
                {v === "away" && <path d="M22 12h-6l-2 3h-4l-2-3H2" />}
              </svg>
              {v.toUpperCase()}
            </GlowBtn>
          ))}
        </div>

        {/* Analyze button */}
        <button
          style={{
            background: C.primary,
            color: "#000",
            border: "none",
            borderRadius: 16,
            padding: "18px 0",
            fontSize: 16,
            fontWeight: 800,
            letterSpacing: 0.5,
            cursor: "pointer",
            marginTop: 4,
            boxShadow: "0 0 30px rgba(57,255,20,0.4), 0 8px 20px rgba(57,255,20,0.2)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2.5" strokeLinecap="round">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
          </svg>
          Analyze
        </button>
      </div>

      {/* Tab bar */}
      <div
        style={{
          position: "fixed",
          bottom: 0,
          left: 0,
          right: 0,
          background: "rgba(0,0,0,0.9)",
          backdropFilter: "blur(20px)",
          borderTop: "1px solid rgba(57,255,20,0.15)",
          display: "flex",
          justifyContent: "space-around",
          padding: "10px 0 24px",
          zIndex: 100,
        }}
      >
        {[
          { label: "Predict", icon: "M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5", active: true },
          { label: "Picks", icon: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", active: false },
          { label: "Chat", icon: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z", active: false },
          { label: "Account", icon: "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2", active: false },
        ].map((tab) => (
          <div key={tab.label} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, cursor: "pointer" }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke={tab.active ? C.primary : C.text3} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d={tab.icon} />
            </svg>
            <span style={{ color: tab.active ? C.primary : C.text3, fontSize: 10, fontWeight: 600 }}>{tab.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
