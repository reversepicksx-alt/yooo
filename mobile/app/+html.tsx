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
          }
          #splash-screen.hidden { opacity: 0; pointer-events: none; }
          .splash-logo {
            width: 80px; height: 80px; border-radius: 20px;
            margin-bottom: 20px;
          }
          .splash-title {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            font-size: 22px; font-weight: 900; letter-spacing: 4px;
            color: #ffffff; margin-bottom: 4px;
          }
          .splash-sub {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            font-size: 11px; font-weight: 600; letter-spacing: 3px;
            color: #39FF14; margin-bottom: 40px;
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
          <img src="/rp-icon.png" alt="" className="splash-logo" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
          <div className="splash-title">REVERSEPICKS</div>
          <div className="splash-sub">ELITE PROP INTELLIGENCE</div>
          <div className="splash-spinner" />
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
