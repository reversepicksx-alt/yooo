import { useState } from "react";

const C = {
  bg: "#050505",
  primary: "#39FF14",
  text: "#FFFFFF",
  text2: "rgba(255,255,255,0.5)",
  text3: "rgba(255,255,255,0.25)",
};

export default function AuthSignup() {
  const [step, setStep] = useState(1);
  const [email, setEmail] = useState("");

  return (
    <div
      style={{
        background: C.bg,
        minHeight: "100vh",
        fontFamily: "system-ui, -apple-system, sans-serif",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "40px 24px",
      }}
    >
      <div
        style={{
          width: 72, height: 72, borderRadius: 20,
          background: "linear-gradient(135deg, rgba(57,255,20,0.15), rgba(57,255,20,0.05))",
          border: "1px solid rgba(57,255,20,0.2)",
          display: "flex", alignItems: "center", justifyContent: "center",
          boxShadow: "0 0 40px rgba(57,255,20,0.2)",
          marginBottom: 24,
        }}
      >
        <span style={{ fontSize: 28, fontWeight: 900, color: C.primary }}>RP</span>
      </div>

      <div style={{ textAlign: "center", marginBottom: 40 }}>
        <div style={{ fontSize: 24, fontWeight: 900, color: C.text, letterSpacing: 3, marginBottom: 6 }}>REVERSE PICKS</div>
        <div style={{ fontSize: 13, color: C.text2, fontWeight: 500 }}>AI-Powered Soccer Prop Analytics</div>
      </div>

      {step === 1 ? (
        <div style={{ width: "100%", maxWidth: 320, display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Create Account</div>
          <div style={{ fontSize: 13, color: C.text2, marginBottom: 8 }}>Enter your email to get started</div>

          <input
            type="email"
            placeholder="your@email.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{
              background: "rgba(17,17,17,0.6)",
              border: "1px solid rgba(57,255,20,0.15)",
              borderRadius: 14,
              color: C.text,
              padding: "16px 18px",
              fontSize: 15,
              outline: "none",
              backdropFilter: "blur(10px)",
              boxShadow: "0 4px 16px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.02)",
            }}
          />

          <button
            onClick={() => setStep(2)}
            style={{
              background: C.primary,
              color: "#000",
              border: "none",
              borderRadius: 14,
              padding: "18px 0",
              fontSize: 16,
              fontWeight: 800,
              cursor: "pointer",
              boxShadow: "0 0 30px rgba(57,255,20,0.3), 0 8px 20px rgba(57,255,20,0.15)",
              marginTop: 8,
            }}
          >
            Continue
          </button>

          <div style={{ textAlign: "center", marginTop: 20 }}>
            <span style={{ fontSize: 12, color: C.text3 }}>
              By signing up, you agree to our{" "}
              <span style={{ color: C.primary, cursor: "pointer", fontWeight: 600 }}>Terms</span> and{" "}
              <span style={{ color: C.primary, cursor: "pointer", fontWeight: 600 }}>Privacy Policy</span>
            </span>
          </div>
        </div>
      ) : (
        <div style={{ width: "100%", maxWidth: 320, display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ fontSize: 18, fontWeight: 800, color: C.text, marginBottom: 4 }}>Set Password</div>
          <div style={{ fontSize: 13, color: C.text2, marginBottom: 8 }}>{email}</div>

          <input
            type="password"
            placeholder="Create password"
            style={{
              background: "rgba(17,17,17,0.6)",
              border: "1px solid rgba(57,255,20,0.15)",
              borderRadius: 14,
              color: C.text,
              padding: "16px 18px",
              fontSize: 15,
              outline: "none",
              backdropFilter: "blur(10px)",
              boxShadow: "0 4px 16px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.02)",
            }}
          />
          <input
            type="password"
            placeholder="Confirm password"
            style={{
              background: "rgba(17,17,17,0.6)",
              border: "1px solid rgba(57,255,20,0.15)",
              borderRadius: 14,
              color: C.text,
              padding: "16px 18px",
              fontSize: 15,
              outline: "none",
              backdropFilter: "blur(10px)",
              boxShadow: "0 4px 16px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.02)",
            }}
          />

          <button
            style={{
              background: C.primary,
              color: "#000",
              border: "none",
              borderRadius: 14,
              padding: "18px 0",
              fontSize: 16,
              fontWeight: 800,
              cursor: "pointer",
              boxShadow: "0 0 30px rgba(57,255,20,0.3), 0 8px 20px rgba(57,255,20,0.15)",
              marginTop: 8,
            }}
          >
            Create Account
          </button>

          <button
            onClick={() => setStep(1)}
            style={{
              background: "transparent",
              color: C.text2,
              border: "none",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
              marginTop: 8,
            }}
          >
            Back
          </button>
        </div>
      )}
    </div>
  );
}
