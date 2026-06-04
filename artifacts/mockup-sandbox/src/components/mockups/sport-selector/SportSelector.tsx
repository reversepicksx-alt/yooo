import { useState } from "react";
import { Button } from "@/components/ui/button";

const SPORTS = [
  { id: "soccer", label: "Soccer", emoji: "⚽" },
  { id: "mlb", label: "MLB", emoji: "⚾" },
  { id: "cs2", label: "CS2", emoji: "🎮" },
  { id: "wta", label: "WTA", emoji: "🎾" },
];

export default function SportSelector() {
  const [selected, setSelected] = useState("soccer");
  const [open, setOpen] = useState(false);

  return (
    <div className="flex items-center justify-center min-h-screen" style={{ background: "#050505" }}>
      <div className="w-full max-w-[360px] px-4 flex flex-col gap-6">
        {/* Current sport display */}
        <div
          className="flex items-center justify-between px-4 py-3 rounded-xl cursor-pointer"
          style={{ background: "#111111", border: "1px solid #222" }}
          onClick={() => setOpen(true)}
        >
          <div className="flex items-center gap-3">
            <span className="text-xl">{SPORTS.find((s) => s.id === selected)?.emoji}</span>
            <span className="text-sm font-semibold text-white tracking-wide">
              {SPORTS.find((s) => s.id === selected)?.label}
            </span>
          </div>
          <span className="text-xs" style={{ color: "#39FF14" }}>
            Change sport
          </span>
        </div>

        {/* Modal */}
        {open && (
          <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.85)" }}>
            <div className="w-[320px] p-5 rounded-2xl" style={{ background: "#111111", border: "1px solid #222" }}>
              <div className="flex items-center justify-between mb-5">
                <h3 className="text-sm font-semibold text-white tracking-wider">SELECT SPORT</h3>
                <button
                  onClick={() => setOpen(false)}
                  className="text-xs px-3 py-1 rounded-full"
                  style={{ color: "#888", background: "#1a1a1a" }}
                >
                  Close
                </button>
              </div>

              <div className="grid grid-cols-2 gap-3">
                {SPORTS.map((sport) => {
                  const isActive = sport.id === selected;
                  return (
                    <button
                      key={sport.id}
                      onClick={() => {
                        setSelected(sport.id);
                        setOpen(false);
                      }}
                      className="flex flex-col items-center justify-center gap-2 py-5 rounded-xl transition-all duration-200"
                      style={{
                        background: isActive ? "rgba(57,255,20,0.1)" : "#1a1a1a",
                        border: isActive ? "1px solid rgba(57,255,20,0.4)" : "1px solid #222",
                      }}
                    >
                      <span className="text-3xl">{sport.emoji}</span>
                      <span
                        className="text-sm font-medium"
                        style={{ color: isActive ? "#39FF14" : "#bbb" }}
                      >
                        {sport.label}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
