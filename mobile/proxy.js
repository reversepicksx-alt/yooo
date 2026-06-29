/**
 * ReversePicks production/dev proxy — zero npm dependencies.
 * Uses only Node.js built-in modules so it starts instantly on any deploy
 * without requiring `npm install` first.
 *
 * Production:  serves dist/ static files + proxies /api/* to FastAPI :8000
 *              Backend (uvicorn) is started by the run command, not this script.
 * Development: proxies everything to Expo Metro :5001 (or serves dist/ if built)
 */
const http = require('http');
const path = require('path');
const fs   = require('fs');
const url  = require('url');

// ── Global safety net ──────────────────────────────────────────────────────
process.on('uncaughtException',  (err)    => console.error('[Proxy] Uncaught exception (survived):', err.message));
process.on('unhandledRejection', (reason) => console.error('[Proxy] Unhandled rejection (survived):', reason));

const IS_PRODUCTION = process.env.PRODUCTION === 'true';
const BUILD_TS = Date.now(); // unique per proxy start — used for cache busting

// ── Dynamically resolve the hashed logo path from the built dist ────────────
function resolveLogoPath() {
  try {
    const assetDir = path.join(distPath, 'assets', 'assets');
    const files = fs.readdirSync(assetDir);
    const logo = files.find(f => f.startsWith('logo.') && f.endsWith('.png'));
    if (logo) return `/assets/assets/${logo}`;
  } catch {}
  // fallback: serve the raw asset from mobile/assets
  return '/rp-logo-fallback.png';
}
let _logoPath = null;
function getLogoPath() {
  if (!_logoPath) _logoPath = resolveLogoPath();
  return _logoPath;
}

const PORT         = 5000;
const BACKEND_PORT = parseInt(process.env.BACKEND_PORT || '8000', 10);
const METRO_PORT   = 5001;

const distPath   = path.join(__dirname, 'dist');
const assetsPath = path.join(__dirname, 'assets');

// ── MIME types ─────────────────────────────────────────────────────────────
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript; charset=utf-8',
  '.mjs':  'application/javascript; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.json': 'application/json',
  '.png':  'image/png',
  '.jpg':  'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif':  'image/gif',
  '.svg':  'image/svg+xml',
  '.ico':  'image/x-icon',
  '.woff': 'font/woff',
  '.woff2':'font/woff2',
  '.ttf':  'font/ttf',
  '.otf':  'font/otf',
  '.map':  'application/json',
  '.txt':  'text/plain',
  '.webp': 'image/webp',
};
const getMime = (p) => MIME[path.extname(p).toLowerCase()] || 'application/octet-stream';

// ── Static file helper ─────────────────────────────────────────────────────
function serveFile(res, filePath, extraHeaders) {
  try {
    const stat = fs.statSync(filePath);
    res.writeHead(200, { 'Content-Type': getMime(filePath), 'Content-Length': stat.size, ...extraHeaders });
    fs.createReadStream(filePath).pipe(res);
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Not found');
  }
}

// ── API proxy helper ───────────────────────────────────────────────────────
function proxyToBackend(req, res) {
  const opts = {
    hostname: 'localhost',
    port:     BACKEND_PORT,
    path:     req.url,
    method:   req.method,
    headers:  { ...req.headers, host: `localhost:${BACKEND_PORT}` },
  };
  const pr = http.request(opts, (backRes) => {
    if (!res.headersSent) res.writeHead(backRes.statusCode, backRes.headers);
    backRes.pipe(res);
    backRes.on('error', (e) => console.error('[Proxy] backRes error:', e.message));
  });
  pr.setTimeout(120000, () => {
    pr.destroy();
    if (!res.headersSent) { res.writeHead(504, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ detail: 'Backend timeout' })); }
  });
  pr.on('error', (e) => {
    console.error('[Proxy] backend error:', e.message);
    if (!res.headersSent) { res.writeHead(502, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ detail: 'Backend unavailable' })); }
  });
  req.pipe(pr);
}

// ── PWA / OG tags injected into every index.html response ─────────────────
const PWA_TAGS = `    <style>html,body{background:#050505!important;margin:0;padding:0}</style>
    <link rel="icon" type="image/png" href="/rp-icon.png" />
    <link rel="shortcut icon" href="/rp-icon.png" />
    <link rel="apple-touch-icon" href="/rp-icon.png" />
    <meta name="description" content="AI-powered soccer player prop analytics. Bayesian projections, tactical insights, and data-driven predictions." />
    <meta property="og:type" content="website" />
    <meta property="og:url" content="https://reversepicks.com/" />
    <meta property="og:title" content="ReversePicks \u2014 Elite Prop Intelligence" />
    <meta property="og:description" content="AI-powered soccer player prop analytics. Bayesian projections, tactical insights, and data-driven predictions." />
    <meta property="og:image" content="https://reversepicks.com/rp-icon.png" />
    <meta property="og:site_name" content="ReversePicks" />
    <meta name="twitter:card" content="summary" />
    <meta name="twitter:image" content="https://reversepicks.com/rp-icon.png" />
    <meta name="theme-color" content="#050505" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <meta name="apple-mobile-web-app-title" content="ReversePicks" />
    <link rel="manifest" href="/manifest.json" />`;

function buildLoadingScreen() { return `
<div id="rp-loading-screen">
  <div class="rp-scene">
    <div class="rp-logo-outer">
      <div class="rp-ring rp-r0"></div>
      <div class="rp-ring rp-r1"></div>
      <div class="rp-ring rp-r2"></div>
      <div class="rp-ring rp-r3"></div>
      <svg class="rp-svg" viewBox="-160 -160 320 320" xmlns="http://www.w3.org/2000/svg">
        <polyline class="rp-bolt rp-b0" points="92,0 124,-26 109,6 138,-20 125,4 162,16"/>
        <polyline class="rp-bolt rp-b1" points="65,-65 90,-102 77,-83 105,-114 88,-90 118,-130"/>
        <polyline class="rp-bolt rp-b2" points="0,-92 24,-122 4,-102 20,-136 0,-112 -16,-155"/>
        <polyline class="rp-bolt rp-b3" points="-65,-65 -90,-102 -77,-83 -105,-114 -88,-90 -118,-130"/>
        <polyline class="rp-bolt rp-b4" points="-92,0 -124,26 -109,-6 -138,20 -125,-4 -162,-16"/>
        <polyline class="rp-bolt rp-b5" points="65,65 90,102 77,83 105,114 88,90 118,130"/>
        <polyline class="rp-bolt rp-b6" points="0,92 -24,122 -4,102 -20,136 0,112 16,155"/>
        <polyline class="rp-bolt rp-b7" points="-65,65 -90,102 -77,83 -105,114 -88,90 -118,130"/>
      </svg>
      <div class="rp-logo-wrap">
        <img class="rp-logo-img" src="${getLogoPath()}" alt="ReversePicks" />
      </div>
    </div>
    <div class="rp-hud">
      <div class="rp-brand">
        <span class="rp-ch" style="animation-delay:1.60s">R</span><span class="rp-ch" style="animation-delay:1.655s">E</span><span class="rp-ch" style="animation-delay:1.71s">V</span><span class="rp-ch" style="animation-delay:1.765s">E</span><span class="rp-ch" style="animation-delay:1.82s">R</span><span class="rp-ch" style="animation-delay:1.875s">S</span><span class="rp-ch" style="animation-delay:1.93s">E</span><span class="rp-ch" style="animation-delay:1.985s">P</span><span class="rp-ch" style="animation-delay:2.04s">I</span><span class="rp-ch" style="animation-delay:2.095s">C</span><span class="rp-ch" style="animation-delay:2.15s">K</span><span class="rp-ch" style="animation-delay:2.205s">S</span>
      </div>
      <div class="rp-tag">THE EYE SEES WHAT OTHERS MISS</div>
    </div>
  </div>
</div>
<style>
#rp-loading-screen{position:fixed;top:0;left:0;right:0;bottom:0;background:#050505;z-index:99999;display:flex;align-items:center;justify-content:center;transition:opacity .6s ease-out}
.rp-scene{display:flex;flex-direction:column;align-items:center}
.rp-logo-outer{position:relative;display:flex;align-items:center;justify-content:center;margin-bottom:min(6vh,40px)}
.rp-ring{position:absolute;top:50%;left:50%;width:min(52vw,220px);height:min(52vw,220px);border:2px solid #39FF14;border-radius:50%;opacity:0;transform:translate(-50%,-50%) scale(0.2);box-shadow:0 0 12px 3px #39FF14,inset 0 0 12px 3px rgba(57,255,20,.4);pointer-events:none}
.rp-r0{animation:ringExp .75s 1.08s ease-out,ringExp .75s 1.52s ease-out}
.rp-r1{animation:ringExp .75s 1.24s ease-out,ringExp .75s 1.68s ease-out}
.rp-r2{animation:ringExp .75s 1.40s ease-out}
.rp-r3{animation:ringExp .75s 1.56s ease-out}
@keyframes ringExp{0%{opacity:.85;transform:translate(-50%,-50%) scale(.25)}60%{opacity:.35;transform:translate(-50%,-50%) scale(1.9)}100%{opacity:0;transform:translate(-50%,-50%) scale(3.2)}}
.rp-svg{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:min(95vw,500px);height:min(95vw,500px);overflow:visible;pointer-events:none}
.rp-bolt{fill:none;stroke:#39FF14;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;filter:drop-shadow(0 0 6px #39FF14) drop-shadow(0 0 16px rgba(57,255,20,.6)) drop-shadow(0 0 30px rgba(57,255,20,.3));opacity:0}
.rp-b0{animation:bF .09s 1.05s,bF .09s 1.31s,bF .09s 1.57s}
.rp-b1{animation:bF .09s 1.13s,bF .09s 1.39s,bF .09s 1.65s}
.rp-b2{animation:bF .09s 1.07s,bF .09s 1.33s,bF .09s 1.59s}
.rp-b3{animation:bF .09s 1.16s,bF .09s 1.42s}
.rp-b4{animation:bF .09s 1.09s,bF .09s 1.35s,bF .09s 1.61s}
.rp-b5{animation:bF .09s 1.11s,bF .09s 1.37s,bF .09s 1.63s}
.rp-b6{animation:bF .09s 1.19s,bF .09s 1.45s}
.rp-b7{animation:bF .09s 1.22s,bF .09s 1.48s,bF .09s 1.74s}
@keyframes bF{0%{opacity:0}12%{opacity:1}100%{opacity:0}}
.rp-logo-wrap{display:flex;align-items:center;justify-content:center;animation:logoSpin 1.5s linear 0s both}
@keyframes logoSpin{
  0%  {opacity:0;transform:scale(.78) rotate(0deg);animation-timing-function:ease-in}
  5%  {opacity:1;transform:scale(1)   rotate(90deg);animation-timing-function:linear}
  78% {           transform:scale(1)   rotate(1827deg);animation-timing-function:cubic-bezier(.2,.8,.3,1)}
  88% {           transform:scale(1.06) rotate(1890deg);animation-timing-function:cubic-bezier(.34,1.56,.64,1)}
  94% {           transform:scale(0.97) rotate(1768deg)}
  100%{           transform:scale(1)   rotate(1800deg)}
}
.rp-logo-img{width:min(52vw,220px);height:min(52vw,220px);object-fit:contain;display:block;animation:logoGlow 3s .2s ease-in-out}
@keyframes logoGlow{
  0%  {filter:drop-shadow(0 0 20px #39FF14) drop-shadow(0 0 40px rgba(57,255,20,.5))}
  40% {filter:drop-shadow(0 0 50px #39FF14) drop-shadow(0 0 100px rgba(57,255,20,.7)) drop-shadow(0 0 150px rgba(57,255,20,.3))}
  60% {filter:drop-shadow(0 0 20px #39FF14)}
  80% {filter:drop-shadow(0 0 60px #39FF14) drop-shadow(0 0 120px rgba(57,255,20,.8)) drop-shadow(0 0 180px rgba(57,255,20,.4))}
  100%{filter:drop-shadow(0 0 14px rgba(57,255,20,.3))}
}
.rp-hud{display:flex;flex-direction:column;align-items:center;gap:10px}
.rp-brand{display:flex;gap:2px}
.rp-ch{font-family:system-ui,-apple-system,sans-serif;font-size:clamp(14px,4.5vw,22px);font-weight:900;color:#fff;letter-spacing:3px;opacity:0;transform:scale(.3);animation:chPop .22s forwards;text-shadow:0 0 18px rgba(57,255,20,.7)}
@keyframes chPop{to{opacity:1;transform:scale(1)}}
.rp-tag{font-family:system-ui,-apple-system,sans-serif;font-size:clamp(7px,2vw,9px);font-weight:600;color:#39FF14;letter-spacing:2.5px;text-transform:uppercase;opacity:0;animation:tagIn .5s 2.0s forwards}
@keyframes tagIn{to{opacity:.85}}
</style>
<script>
(function(){
  var e=document.getElementById('rp-loading-screen');
  function hide(){if(!e)return;e.style.opacity='0';setTimeout(function(){if(e&&e.parentNode)e.parentNode.removeChild(e);e=null;},650);}
  window.__rpHideLoader=hide;
  setTimeout(hide,15000);
})();
</script>`; }

const MANIFEST = JSON.stringify({
  name: 'ReversePicks', short_name: 'ReversePicks',
  description: 'Elite Prop Intelligence', start_url: '/',
  display: 'standalone', background_color: '#050505', theme_color: '#050505',
  icons: [
    { src: '/rp-icon.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' },
    { src: '/rp-icon.png', sizes: '192x192', type: 'image/png' },
  ],
});

function serveIndex(res) {
  const indexPath = path.join(distPath, 'index.html');
  if (!fs.existsSync(indexPath)) {
    res.writeHead(503, { 'Content-Type': 'text/html', 'Cache-Control': 'no-store' });
    return res.end('<html><body style="font-family:sans-serif;padding:2rem;background:#050505;color:#fff"><h2>Starting up\u2026</h2><p>Refresh in a few seconds.</p><script>setTimeout(()=>location.reload(),5000)</script></body></html>');
  }
  try {
    let html = fs.readFileSync(indexPath, 'utf8');
    html = html.replace(/<title>[^<]*<\/title>/, '<title>ReversePicks \u2014 Elite Prop Intelligence</title>');
    html = html.replace('</head>', `${PWA_TAGS}\n  </head>`);
    html = html.replace('</body>', `${buildLoadingScreen()}\n</body>`);
    res.writeHead(200, {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store, no-cache, must-revalidate, proxy-revalidate',
      'Pragma': 'no-cache', 'Expires': '0', 'Surrogate-Control': 'no-store',
      'Clear-Site-Data': '"cache"',
    });
    res.end(html);
  } catch {
    serveFile(res, indexPath);
  }
}

// ── Request handler ────────────────────────────────────────────────────────
const server = http.createServer((req, res) => {
  const pathname = url.parse(req.url).pathname || '/';

  // Always proxy /api/* to FastAPI backend
  if (pathname === '/api' || pathname.startsWith('/api/')) {
    return proxyToBackend(req, res);
  }

  if (IS_PRODUCTION) {
    // Special routes
    if (pathname === '/manifest.json') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(MANIFEST);
    }
    if (pathname === '/privacy' || pathname === '/privacy.html') {
      const PRIVACY_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Privacy Policy — ReversePicks</title>
<style>
  body{margin:0;padding:0;background:#050505;color:#ccc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.7}
  .wrap{max-width:720px;margin:0 auto;padding:48px 24px}
  h1{color:#39FF14;font-size:2rem;margin-bottom:4px}
  h2{color:#39FF14;font-size:1.1rem;margin-top:36px;margin-bottom:8px}
  a{color:#39FF14}
  .date{color:#666;font-size:.85rem;margin-bottom:32px}
  hr{border:none;border-top:1px solid #222;margin:40px 0}
</style>
</head>
<body>
<div class="wrap">
  <h1>Privacy Policy</h1>
  <p class="date">Last updated: June 17, 2026</p>

  <p>ReversePicks ("we", "us", or "our") operates the ReversePicks mobile application (the "App"). This page explains what information we collect, how we use it, and your rights.</p>

  <h2>Information We Collect</h2>
  <p>We collect only your <strong>email address</strong> when you create an account. This is used solely to authenticate you and manage your subscription.</p>

  <h2>How We Use Your Information</h2>
  <ul>
    <li>To create and manage your ReversePicks account</li>
    <li>To process and verify your subscription status</li>
    <li>To send essential account and service communications</li>
  </ul>

  <h2>Data Sharing</h2>
  <p>We do not sell, trade, or rent your personal information to third parties. We do not use your data for advertising or tracking purposes.</p>

  <h2>Data Storage</h2>
  <p>Your email address is stored securely in our database. We retain it for as long as your account is active or as needed to provide our services.</p>

  <h2>Third-Party Services</h2>
  <p>We use Stripe for payment processing. Stripe's privacy policy governs any payment data you provide during checkout. We do not store your payment card details.</p>

  <h2>Your Rights</h2>
  <p>You may request deletion of your account and associated data at any time by contacting us at <a href="mailto:reversepicksx@gmail.com">reversepicksx@gmail.com</a>.</p>

  <h2>Children's Privacy</h2>
  <p>The App is not directed to children under 13. We do not knowingly collect personal information from children under 13.</p>

  <h2>Changes to This Policy</h2>
  <p>We may update this Privacy Policy from time to time. We will notify you of material changes by posting the new policy on this page with an updated date.</p>

  <h2>Contact</h2>
  <p>If you have questions about this Privacy Policy, please contact us at <a href="mailto:reversepicksx@gmail.com">reversepicksx@gmail.com</a>.</p>

  <hr/>
  <p style="color:#444;font-size:.8rem">ReversePicks &mdash; Elite Prop Intelligence</p>
</div>
</body>
</html>`;
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      return res.end(PRIVACY_HTML);
    }
    if (pathname === '/rp-icon.png' || pathname === '/favicon.ico' || pathname === '/favicon.png') {
      return serveFile(res, path.join(assetsPath, 'rp-icon.png'));
    }
    // Static asset from dist/
    if (pathname !== '/') {
      const filePath = path.join(distPath, pathname);
      // Prevent path traversal
      if (!filePath.startsWith(distPath + path.sep) && filePath !== distPath) {
        res.writeHead(403); return res.end('Forbidden');
      }
      if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
        const isHashed = /\.[0-9a-f]{8,}\.(js|css|png|woff2?)$/.test(pathname) ||
                         pathname.startsWith('/_expo/') || pathname.startsWith('/assets/');
        return serveFile(res, filePath, isHashed ? { 'Cache-Control': 'public, max-age=31536000, immutable' } : {});
      }
    }

    // SPA fallback
    return serveIndex(res);

  } else {
    // Development: serve dist/ if built, otherwise proxy to Metro
    const hasDist = fs.existsSync(path.join(distPath, 'index.html'));
    if (hasDist) {
      if (pathname !== '/') {
        const filePath = path.join(distPath, pathname);
        if (!filePath.startsWith(distPath + path.sep) && filePath !== distPath) { res.writeHead(403); return res.end('Forbidden'); }
        if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) return serveFile(res, filePath);
      }
      return serveIndex(res);
    }
    // Proxy to Metro dev server
    const opts = { hostname: 'localhost', port: METRO_PORT, path: req.url, method: req.method, headers: req.headers };
    const pr = http.request(opts, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    pr.on('error', () => { if (!res.headersSent) { res.writeHead(502); res.end('Frontend loading\u2026'); } });
    req.pipe(pr);
  }
});

// WebSocket passthrough for Metro hot-reload (dev only)
server.on('upgrade', (req, socket) => {
  if (IS_PRODUCTION) return socket.destroy();
  const pr = http.request({ hostname: 'localhost', port: METRO_PORT, path: req.url, method: req.method, headers: req.headers });
  pr.on('upgrade', (_res, proxySocket) => {
    socket.write('HTTP/1.1 101 Switching Protocols\r\n\r\n');
    proxySocket.pipe(socket);
    socket.pipe(proxySocket);
  });
  pr.on('error', () => socket.destroy());
  pr.end();
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`[Proxy] Listening on port ${PORT} (${IS_PRODUCTION ? 'PRODUCTION' : 'development'})`);
  console.log('[Proxy] /api/*  \u2192 http://localhost:8000 (FastAPI backend)');
  console.log(IS_PRODUCTION ? '[Proxy] /**     \u2192 dist/ (static build)' : '[Proxy] /**     \u2192 http://localhost:5001 (Expo Metro)');
});
