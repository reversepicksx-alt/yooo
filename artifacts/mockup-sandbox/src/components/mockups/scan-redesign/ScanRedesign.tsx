import { useState } from "react";
import { Button } from "@/components/ui/button";

export default function ScanRedesign() {
  const [image, setImage] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [result, setResult] = useState<null | { player: string; prop: string; line: number }>(null);

  const handleUpload = () => {
    setScanning(true);
    setTimeout(() => {
      setScanning(false);
      setResult({ player: "Erling Haaland", prop: "Shots", line: 3.5 });
    }, 2000);
  };

  return (
    <div className="min-h-screen flex flex-col" style={{ background: "#050505" }}>
      {/* Header */}
      <div className="px-4 pt-6 pb-2 flex items-center gap-3">
        <div
          className="w-8 h-8 rounded-lg flex items-center justify-center"
          style={{ background: "#111111", border: "1px solid #222" }}
        >
          <span className="text-xs font-bold" style={{ color: "#39FF14" }}>RP</span>
        </div>
        <div className="flex flex-col">
          <span className="text-xs font-semibold text-white tracking-wider">REVERSEPICKS</span>
          <span className="text-[10px] text-gray-500">Soccer Prop Analytics</span>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col items-center justify-center px-6 gap-6">
        {!image && !result && (
          <>
            <div
              className="w-full aspect-[3/4] max-w-[320px] rounded-2xl flex flex-col items-center justify-center gap-4 cursor-pointer"
              style={{ background: "#0a0a0a", border: "2px dashed #222" }}
              onClick={() => {
                setImage("/__mockup/images/haaland.jpg");
                handleUpload();
              }}
            >
              <div className="w-16 h-16 rounded-full flex items-center justify-center" style={{ background: "#111111" }}>
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#39FF14" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                  <circle cx="12" cy="13" r="4"/>
                </svg>
              </div>
              <div className="text-center">
                <p className="text-sm font-medium text-white mb-1">Tap to scan prop slip</p>
                <p className="text-[11px] text-gray-500">Upload a screenshot of any prop slip</p>
              </div>
            </div>
          </>
        )}

        {image && scanning && (
          <div className="w-full max-w-[320px] flex flex-col items-center gap-4">
            <div className="w-full aspect-[3/4] rounded-2xl overflow-hidden" style={{ background: "#111" }}>
              <div className="w-full h-full flex items-center justify-center">
                <div className="w-12 h-12 border-2 rounded-full animate-spin" style={{ borderColor: "#39FF14", borderTopColor: "transparent" }} />
              </div>
            </div>
            <p className="text-sm text-gray-400">Analyzing prop slip...</p>
          </div>
        )}

        {image && !scanning && result && (
          <div className="w-full max-w-[320px] flex flex-col gap-4">
            <div className="w-full aspect-[3/4] rounded-2xl overflow-hidden" style={{ background: "#111" }}>
              <div className="w-full h-full flex items-center justify-center text-6xl">⚽</div>
            </div>

            <div className="p-4 rounded-xl" style={{ background: "#111111", border: "1px solid #222" }}>
              <div className="flex items-center gap-2 mb-3">
                <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: "#39FF14", color: "#000" }}>
                  DETECTED
                </span>
              </div>
              <p className="text-lg font-bold text-white mb-1">{result.player}</p>
              <div className="flex items-center gap-3 text-sm text-gray-400">
                <span>{result.prop}</span>
                <span>·</span>
                <span style={{ color: "#39FF14" }}>{result.line}</span>
              </div>
              <button
                className="w-full mt-4 py-3 rounded-xl font-semibold text-sm"
                style={{ background: "#39FF14", color: "#000" }}
              >
                RUN PREDICTION
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
