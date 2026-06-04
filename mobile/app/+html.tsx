import { ScrollViewStyleReset } from 'expo-router/html';

export default function Root({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />
        <meta name="theme-color" content="#050505" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
        <meta name="apple-mobile-web-app-title" content="ReversePicks" />
        <link rel="apple-touch-icon" href="/rp-icon.png" />
        <link rel="manifest" href="/manifest.json" />
        <ScrollViewStyleReset />
        <style dangerouslySetInnerHTML={{ __html: `
          html, body { margin: 0; padding: 0; background: #050505; }
          #splash-screen {
            position: fixed; inset: 0;
            background: #050505;
            display: flex; flex-direction: column;
            align-items: center; justify-content: center;
            z-index: 9999;
            transition: opacity 0.3s ease;
            gap: 32px;
          }
          #splash-screen.hidden { opacity: 0; pointer-events: none; }
          .splash-logo-wrap {
            position: relative;
            width: 80px; height: 80px;
            display: flex;
            align-items: center; justify-content: center;
          }
          .splash-logo {
            width: 80px; height: 80px; border-radius: 20px;
            background: #111111;
            border: 1px solid #222;
            display: flex;
            align-items: center; justify-content: center;
            box-shadow: 0 0 40px rgba(57,255,20,0.15), 0 0 80px rgba(57,255,20,0.05);
          }
          .splash-logo-text {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            font-size: 24px; font-weight: 900;
            color: #39FF14; letter-spacing: 1px;
          }
          .splash-pulse-ring {
            position: absolute; inset: 0;
            border-radius: 20px;
            border: 1px solid rgba(57,255,20,0.3);
            animation: pulse-ring 2s ease-out infinite;
          }
          @keyframes pulse-ring {
            0% { transform: scale(1); opacity: 0.5; }
            50% { transform: scale(1.15); opacity: 0; }
            100% { transform: scale(1); opacity: 0; }
          }
          .splash-text {
            text-align: center;
          }
          .splash-title {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            font-size: 16px; font-weight: 900; letter-spacing: 4px;
            color: #ffffff; margin-bottom: 4px;
          }
          .splash-sub {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            font-size: 10px; font-weight: 600; letter-spacing: 2px;
            color: rgba(255,255,255,0.5); min-width: 90px;
          }
          .splash-progress {
            width: 192px; height: 3px;
            border-radius: 2px;
            background: #1a1a1a;
            overflow: hidden;
          }
          .splash-progress-fill {
            height: 3px; border-radius: 2px;
            background: #39FF14;
            box-shadow: 0 0 10px rgba(57,255,20,0.5);
            animation: progress-fill 2.5s ease-in-out forwards;
          }
          @keyframes progress-fill {
            0% { width: 0%; }
            40% { width: 45%; }
            70% { width: 78%; }
            100% { width: 100%; }
          }
          .splash-status {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            font-size: 9px; font-weight: 600;
            color: rgba(255,255,255,0.25); letter-spacing: 1.5px;
            text-transform: uppercase;
            animation: status-cycle 2.5s ease-in-out forwards;
          }
          @keyframes status-cycle {
            0% { opacity: 1; }
            25% { opacity: 1; }
            30% { opacity: 0; }
            35% { opacity: 1; }
            55% { opacity: 1; }
            60% { opacity: 0; }
            65% { opacity: 1; }
            85% { opacity: 1; }
            90% { opacity: 0; }
            95% { opacity: 1; }
            100% { opacity: 1; }
          }
          .splash-spinner {
            width: 24px; height: 24px;
            border: 2px solid rgba(57,255,20,0.2);
            border-top-color: #39FF14;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
          }
          @keyframes spin { to { transform: rotate(360deg); } }
        ` }} />
      </head>
      <body>
        <div id="splash-screen">
          <div className="splash-logo-wrap">
            <div className="splash-logo">
              <span className="splash-logo-text">RP</span>
            </div>
            <div className="splash-pulse-ring" />
          </div>
          <div className="splash-text">
            <div className="splash-title">REVERSEPICKS</div>
            <div className="splash-sub">ELITE PROP INTELLIGENCE</div>
          </div>
          <div className="splash-progress">
            <div className="splash-progress-fill" />
          </div>
          <div className="splash-status">INITIALIZING ENGINES</div>
        </div>
        <script dangerouslySetInnerHTML={{ __html: `
          window.__hideSplash = function() {
            var s = document.getElementById('splash-screen');
            if (s) { s.classList.add('hidden'); setTimeout(function(){ s.remove(); }, 400); }
          };
        ` }} />
        {children}
      </body>
    </html>
  );
}
