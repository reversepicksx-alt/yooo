import { useState } from "react";

const TABS = [
  { id: "scan", label: "Scan", icon: "scan" },
  { id: "picks", label: "Picks", icon: "list" },
  { id: "intel", label: "Intel", icon: "flame" },
  { id: "chat", label: "Chat", icon: "chatbubbles" },
  { id: "account", label: "Account", icon: "person" },
];

const SPORTS = [
  { id: "soccer", label: "Soccer", emoji: "⚽" },
  { id: "mlb", label: "MLB", emoji: "⚾" },
  { id: "cs2", label: "CS2", emoji: "🎮" },
  { id: "wta", label: "WTA", emoji: "🎾" },
];

export default function FullAppPreview() {
  const [tab, setTab] = useState("scan");
  const [sport, setSport] = useState("soccer");
  const [sportPickerOpen, setSportPickerOpen] = useState(false);
  const [scanPhase, setScanPhase] = useState<"idle" | "scanning" | "result">("idle");
  const [loading, setLoading] = useState(true);

  // Simulate loading on mount
  if (loading) {
    setTimeout(() => setLoading(false), 3000);
    return (
      <div className="flex flex-col items-center justify-center min-h-screen" style={{ background: "#050505" }}>
        <div className="flex flex-col items-center gap-6">
          <div
            className="w-16 h-16 rounded-2xl flex items-center justify-center"
            style={{ background: "#111", border: "1px solid #222", boxShadow: "0 0 30px rgba(57,255,20,0.2)" }}
          >
            <span className="text-xl font-bold" style={{ color: "#39FF14" }}>RP</span>
          </div>
          <div className="text-center">
            <h1 className="text-sm font-bold tracking-[0.2em] text-white">REVERSEPICKS</h1>
            <p className="text-[10px] mt-1 text-gray-500">INITIALIZING...</p>
          </div>
          <div className="w-40 h-1 rounded-full overflow-hidden" style={{ background: "#1a1a1a" }}>
            <div className="h-full rounded-full animate-pulse" style={{ width: "60%", background: "#39FF14" }} />
          </div>
        </div>
      </div>
    );
  }

  const renderScanScreen = () => (
    <div className="flex flex-col gap-4">
      {/* Sport selector */}
      <div
        className="flex items-center justify-between px-4 py-3 rounded-xl"
        style={{ background: "#111", border: "1px solid #222" }}
        onClick={() => setSportPickerOpen(true)}
      >
        <div className="flex items-center gap-3">
          <span className="text-lg">{SPORTS.find((s) => s.id === sport)?.emoji}</span>
          <span className="text-sm font-semibold text-white">{SPORTS.find((s) => s.id === sport)?.label}</span>
        </div>
        <span className="text-[10px] font-medium" style={{ color: "#39FF14" }}>Change</span>
      </div>

      {/* Scan area */}
      {scanPhase === "idle" && (
        <div
          className="w-full aspect-[4/5] rounded-2xl flex flex-col items-center justify-center gap-4 cursor-pointer"
          style={{ background: "#0a0a0a", border: "2px dashed #222" }}
          onClick={() => setScanPhase("scanning")}
        >
          <div className="w-14 h-14 rounded-full flex items-center justify-center" style={{ background: "#111" }}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#39FF14" strokeWidth="2">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <circle cx="8.5" cy="8.5" r="1.5" />
              <path d="M21 15l-5-5L5 21" />
            </svg>
          </div>
          <div className="text-center">
            <p className="text-sm font-medium text-white">Tap to scan</p>
            <p className="text-[10px] text-gray-500 mt-1">Upload a prop slip screenshot</p>
          </div>
        </div>
      )}

      {scanPhase === "scanning" && (
        <div className="w-full aspect-[4/5] rounded-2xl flex items-center justify-center" style={{ background: "#111" }}>
          <div className="flex flex-col items-center gap-3">
            <div className="w-10 h-10 border-2 rounded-full animate-spin" style={{ borderColor: "#39FF14", borderTopColor: "transparent" }} />
            <p className="text-xs text-gray-400">Analyzing...</p>
          </div>
        </div>
      )}

      {scanPhase === "result" && (
        <div className="flex flex-col gap-3">
          <div className="w-full aspect-[4/5] rounded-2xl flex items-center justify-center" style={{ background: "#111" }}>
            <span className="text-6xl">⚽</span>
          </div>
          <div className="p-4 rounded-xl" style={{ background: "#111", border: "1px solid #222" }}>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: "#39FF14", color: "#000" }}>DETECTED</span>
            </div>
            <p className="text-base font-bold text-white">Erling Haaland</p>
            <div className="flex items-center gap-2 text-xs text-gray-400 mt-1">
              <span>Shots</span>
              <span>·</span>
              <span style={{ color: "#39FF14" }}>3.5</span>
            </div>
            <button className="w-full mt-3 py-2.5 rounded-xl text-xs font-bold" style={{ background: "#39FF14", color: "#000" }}>
              RUN PREDICTION
            </button>
          </div>
        </div>
      )}

      {/* Manual toggle */}
      <button className="text-center text-xs text-gray-500 py-2">Enter manually instead</button>
    </div>
  );

  const renderPicksScreen = () => (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-gray-500 px-1">Today's Picks</p>
      {[1, 2, 3].map((i) => (
        <div key={i} className="p-4 rounded-xl" style={{ background: "#111", border: "1px solid #222" }}>
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-semibold text-white">Pick {i}</span>
            <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: "#39FF14", color: "#000" }}>OVER</span>
          </div>
          <div className="w-full h-1.5 rounded-full" style={{ background: "#1a1a1a" }}>
            <div className="h-full rounded-full" style={{ width: "65%", background: "#39FF14" }} />
          </div>
        </div>
      ))}
    </div>
  );

  const renderIntelScreen = () => (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-gray-500 px-1">Tactical Intelligence</p>
      {[1, 2].map((i) => (
        <div key={i} className="p-4 rounded-xl" style={{ background: "#111", border: "1px solid #222" }}>
          <div className="flex items-center gap-2 mb-2">
            <div className="w-2 h-2 rounded-full" style={{ background: "#39FF14" }} />
            <span className="text-xs font-semibold text-white">Match Insight {i}</span>
          </div>
          <p className="text-[11px] text-gray-400 leading-relaxed">
            Advanced probability analysis shows strong momentum indicators for the upcoming fixture.
          </p>
        </div>
      ))}
    </div>
  );

  const renderChatScreen = () => (
    <div className="flex flex-col items-center justify-center gap-4 py-20">
      <div className="w-12 h-12 rounded-full flex items-center justify-center" style={{ background: "#111" }}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#39FF14" strokeWidth="2">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </div>
      <p className="text-xs text-gray-500">Tactical chat coming soon</p>
    </div>
  );

  const renderAccountScreen = () => (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3 p-4 rounded-xl" style={{ background: "#111", border: "1px solid #222" }}>
        <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ background: "#1a1a1a" }}>
          <span className="text-sm font-bold text-white">U</span>
        </div>
        <div className="flex flex-col">
          <span className="text-sm font-semibold text-white">User</span>
          <span className="text-[10px] text-gray-500">Premium Plan</span>
        </div>
      </div>
      {["Subscription", "Settings", "Help"].map((item) => (
        <div key={item} className="flex items-center justify-between p-4 rounded-xl" style={{ background: "#111", border: "1px solid #222" }}>
          <span className="text-sm text-white">{item}</span>
          <span className="text-xs text-gray-500">›</span>
        </div>
      ))}
    </div>
  );

  return (
    <div className="flex flex-col min-h-screen" style={{ background: "#050505" }}>
      {/* Main Content */}
      <div className="flex-1 px-4 pt-4 pb-24 overflow-y-auto">
        {tab === "scan" && renderScanScreen()}
        {tab === "picks" && renderPicksScreen()}
        {tab === "intel" && renderIntelScreen()}
        {tab === "chat" && renderChatScreen()}
        {tab === "account" && renderAccountScreen()}
      </div>

      {/* Sport Picker Modal */}
      {sportPickerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.85)" }}>
          <div className="w-[280px] p-4 rounded-2xl" style={{ background: "#111", border: "1px solid #222" }}>
            <div className="flex items-center justify-between mb-4">
              <span className="text-xs font-semibold text-white tracking-wider">SELECT SPORT</span>
              <button onClick={() => setSportPickerOpen(false)} className="text-xs text-gray-500">Close</button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {SPORTS.map((s) => (
                <button
                  key={s.id}
                  onClick={() => { setSport(s.id); setSportPickerOpen(false); }}
                  className="flex flex-col items-center gap-1 py-4 rounded-xl"
                  style={{
                    background: s.id === sport ? "rgba(57,255,20,0.1)" : "#1a1a1a",
                    border: s.id === sport ? "1px solid rgba(57,255,20,0.3)" : "1px solid #222",
                  }}
                >
                  <span className="text-2xl">{s.emoji}</span>
                  <span className="text-xs font-medium" style={{ color: s.id === sport ? "#39FF14" : "#bbb" }}>{s.label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Bottom Tab Bar */}
      <div className="fixed bottom-0 left-0 right-0 flex items-center justify-around py-3 px-2" style={{ background: "#0a0a0a", borderTop: "1px solid #1a1a1a" }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className="flex flex-col items-center gap-1 px-3 py-1"
          >
            <span className="text-lg" style={{ color: tab === t.id ? "#39FF14" : "#666" }}>
              {t.icon === "scan" && "📷"}
              {t.icon === "list" && "📋"}
              {t.icon === "flame" && "🔥"}
              {t.icon === "chatbubbles" && "💬"}
              {t.icon === "person" && "👤"}
            </span>
            <span className="text-[9px] font-medium" style={{ color: tab === t.id ? "#39FF14" : "#666" }}>
              {t.label}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
