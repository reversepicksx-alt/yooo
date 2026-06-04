import { useEffect, useState } from "react";

export default function LoadingScreen() {
  const [progress, setProgress] = useState(0);
  const [dots, setDots] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setProgress((p) => {
        if (p >= 100) {
          clearInterval(interval);
          return 100;
        }
        return p + Math.random() * 8 + 2;
      });
    }, 200);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const dotInterval = setInterval(() => {
      setDots((d) => (d + 1) % 4);
    }, 500);
    return () => clearInterval(dotInterval);
  }, []);

  return (
    <div className="flex flex-col items-center justify-center min-h-screen" style={{ background: "#050505" }}>
      {/* Logo area */}
      <div className="flex flex-col items-center gap-8">
        {/* Animated logo mark */}
        <div className="relative">
          <div
            className="w-20 h-20 rounded-2xl flex items-center justify-center"
            style={{
              background: "#111111",
              border: "1px solid #222",
              boxShadow: "0 0 40px rgba(57,255,20,0.15), 0 0 80px rgba(57,255,20,0.05)",
            }}
          >
            <span className="text-2xl font-bold" style={{ color: "#39FF14" }}>RP</span>
          </div>
          {/* Pulse ring */}
          <div
            className="absolute inset-0 rounded-2xl"
            style={{
              border: "1px solid rgba(57,255,20,0.3)",
              animation: "pulse-ring 2s ease-out infinite",
            }}
          />
        </div>

        {/* Brand name */}
        <div className="text-center">
          <h1 className="text-lg font-bold tracking-[0.2em] text-white">REVERSEPICKS</h1>
          <p className="text-[11px] mt-1 tracking-wider text-gray-500">
            LOADING{".".repeat(dots)}
          </p>
        </div>

        {/* Progress bar */}
        <div className="w-48 h-1 rounded-full overflow-hidden" style={{ background: "#1a1a1a" }}>
          <div
            className="h-full rounded-full transition-all duration-300"
            style={{
              width: `${Math.min(progress, 100)}%`,
              background: "#39FF14",
              boxShadow: "0 0 10px rgba(57,255,20,0.5)",
            }}
          />
        </div>

        {/* Stats hint */}
        <p className="text-[10px] text-gray-600 tracking-wider">
          {progress < 30 && "INITIALIZING ENGINES"}
          {progress >= 30 && progress < 60 && "LOADING PLAYER DATABASE"}
          {progress >= 60 && progress < 90 && "CALIBRATING PROBABILITY MODELS"}
          {progress >= 90 && "READY"}
        </p>
      </div>

      <style>{`
        @keyframes pulse-ring {
          0% { transform: scale(1); opacity: 0.5; }
          50% { transform: scale(1.15); opacity: 0; }
          100% { transform: scale(1); opacity: 0; }
        }
      `}</style>
    </div>
  );
}
