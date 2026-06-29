#!/usr/bin/env node
/**
 * patch-dist-html.js
 * Runs after `expo export --platform web` to inject premium styles, meta tags,
 * and a CSS loading spinner into dist/index.html.
 * Expo's Metro bundler ignores htmlTemplate, so we patch after the fact.
 */
const fs = require('fs');
const path = require('path');

const distHtml = path.join(__dirname, 'dist', 'index.html');
let html = fs.readFileSync(distHtml, 'utf8');

// ── 1. Inject premium <head> content right after <meta charset…> ──────────
const headInject = `
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, shrink-to-fit=no, viewport-fit=cover" />
  <title>ReversePicks — Elite Prop Intelligence</title>
  <meta name="description" content="AI-powered soccer player prop analytics. Bayesian projections, tactical insights, and data-driven predictions." />
  <link rel="icon" type="image/png" href="/rp-icon.png" />
  <link rel="shortcut icon" href="/rp-icon.png" />
  <link rel="apple-touch-icon" href="/rp-icon.png" />
  <meta property="og:type" content="website" />
  <meta property="og:title" content="ReversePicks — Elite Prop Intelligence" />
  <meta property="og:description" content="AI-powered soccer player prop analytics. Bayesian projections, tactical insights, and data-driven predictions." />
  <meta name="theme-color" content="#050505" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <meta name="apple-mobile-web-app-title" content="ReversePicks" />`;

html = html.replace(
  '<meta charset="utf-8" />',
  '<meta charset="utf-8" />' + headInject
);

// Remove the old minimal viewport tag if present
html = html.replace(
  '<meta httpEquiv="X-UA-Compatible" content="IE=edge" />\n  <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />',
  '<meta httpEquiv="X-UA-Compatible" content="IE=edge" />'
);

// ── 2. Patch the expo-reset style block to add dark background + input fixes
const expoResetNew = `<style id="expo-reset">
      html, body { height: 100%; }
      body { overflow: hidden; background: #050505; margin: 0; }
      #root { display: flex; height: 100%; flex: 1; background: #050505; }
      input, textarea, select {
        outline: none !important;
        -webkit-tap-highlight-color: transparent;
        caret-color: #39FF14;
      }
      input:focus, textarea:focus, select:focus { outline: none !important; box-shadow: none !important; }
      input:-webkit-autofill,
      input:-webkit-autofill:hover,
      input:-webkit-autofill:focus {
        -webkit-text-fill-color: #ffffff;
        -webkit-box-shadow: 0 0 0px 1000px #111111 inset !important;
        transition: background-color 5000s ease-in-out 0s;
        caret-color: #39FF14;
      }
      * { box-sizing: border-box; }

      /* CSS-only loading shimmer shown before React mounts */
      #rp-boot {
        position: fixed; inset: 0;
        background: #050505;
        display: flex; align-items: center; justify-content: center;
        z-index: 9999;
        transition: opacity 0.4s ease;
      }
      #rp-boot.hidden { opacity: 0; pointer-events: none; }
      @keyframes rp-pulse {
        0%, 100% { opacity: 0.3; transform: scale(0.95); }
        50%       { opacity: 1;   transform: scale(1.05); }
      }
      #rp-boot img {
        width: 72px; height: 72px;
        animation: rp-pulse 1.6s ease-in-out infinite;
        filter: drop-shadow(0 0 18px #39FF14aa);
      }
    </style>`;

html = html.replace(
  /<style id="expo-reset">[\s\S]*?<\/style>/,
  expoResetNew
);

// ── 3. Add the boot overlay and hide-on-load script just before </body> ────
const bootOverlay = `
  <!-- CSS boot overlay — visible before React mounts, hidden by JS -->
  <div id="rp-boot">
    <img src="/rp-icon.png" alt="Loading ReversePicks…" />
  </div>
  <script>
    // Hide the CSS overlay once React has rendered something into #root.
    // Checks every 100ms; falls back at 10s so the overlay never blocks the app.
    (function () {
      var overlay = document.getElementById('rp-boot');
      if (!overlay) return;
      var attempts = 0;
      var iv = setInterval(function () {
        attempts++;
        var root = document.getElementById('root');
        if ((root && root.children.length > 0) || attempts > 100) {
          clearInterval(iv);
          overlay.classList.add('hidden');
          setTimeout(function () { overlay.remove(); }, 500);
        }
      }, 100);
    })();
  </script>`;

html = html.replace('</body>', bootOverlay + '\n</body>');

fs.writeFileSync(distHtml, html, 'utf8');
console.log('[patch-dist-html] dist/index.html patched successfully.');
