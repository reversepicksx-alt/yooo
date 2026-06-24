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
const PWA_TAGS = `    <link rel="icon" type="image/png" href="/rp-icon.png" />
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

const LOADING_SCREEN = `
<div id="rp-loading-screen">
  <div class="rp-eye-img-wrap">
    <img class="rp-eye-img" src="/assets/assets/splash-eye.e1a40406076285816282a002a7174d05.jpeg" alt="" />
  </div>
  <div class="rp-lid rp-lid-top"></div>
  <div class="rp-lid rp-lid-bot"></div>
  <div class="rp-bolt rp-b1"></div>
  <div class="rp-bolt rp-b2"></div>
  <div class="rp-bolt rp-b3"></div>
  <div class="rp-bolt rp-b4"></div>
  <div class="rp-wisp rp-w1"></div>
  <div class="rp-wisp rp-w2"></div>
  <div class="rp-wisp rp-w3"></div>
  <div class="rp-hud">
    <div class="rp-brand">
      <span class="rp-ch" style="animation-delay:.85s">R</span><span class="rp-ch" style="animation-delay:.90s">E</span><span class="rp-ch" style="animation-delay:.95s">V</span><span class="rp-ch" style="animation-delay:1.00s">E</span><span class="rp-ch" style="animation-delay:1.05s">R</span><span class="rp-ch" style="animation-delay:1.10s">S</span><span class="rp-ch" style="animation-delay:1.15s">E</span><span class="rp-ch" style="animation-delay:1.20s">P</span><span class="rp-ch" style="animation-delay:1.25s">I</span><span class="rp-ch" style="animation-delay:1.30s">C</span><span class="rp-ch" style="animation-delay:1.35s">K</span><span class="rp-ch" style="animation-delay:1.40s">S</span>
    </div>
    <div class="rp-tag">THE EYE SEES WHAT OTHERS MISS</div>
    <div class="rp-prog-track"><div class="rp-prog-fill"></div></div>
  </div>
</div>
<style>
#rp-loading-screen{position:fixed;top:0;left:0;right:0;bottom:0;background:#050505;z-index:99999;overflow:hidden;transition:opacity .5s ease-out}
/* Eye image — blended into black via radial mask */
.rp-eye-img-wrap{position:absolute;left:50%;top:42%;transform:translate(-50%,-52%);width:min(88vw, calc(68vh * 1.562));height:min(calc(88vw / 1.562), 68vh)}
.rp-eye-img{width:100%;height:100%;object-fit:contain;display:block;-webkit-mask-image:radial-gradient(ellipse 80% 70% at 50% 48%,#000 45%,transparent 100%);mask-image:radial-gradient(ellipse 80% 70% at 50% 48%,#000 45%,transparent 100%)}
/* Eyelids */
.rp-lid{position:absolute;left:0;right:0;background:#050505;z-index:10}
.rp-lid-top{top:0;height:55%;animation:lidOpen-top .82s cubic-bezier(.4,0,.2,1) .05s forwards}
.rp-lid-bot{bottom:0;height:60%;animation:lidOpen-bot .82s cubic-bezier(.4,0,.2,1) .05s forwards}
@keyframes lidOpen-top{0%{transform:translateY(0)}100%{transform:translateY(-100%)}}
@keyframes lidOpen-bot{0%{transform:translateY(0)}100%{transform:translateY(100%)}}
/* Lightning bolts */
.rp-bolt{position:absolute;background:#39FF14;box-shadow:0 0 8px 3px rgba(57,255,20,.7);opacity:0;z-index:20}
.rp-b1{width:80px;height:2.5px;top:calc(42% - min(88vw / 1.562, 68vh) * .52 - 14px);left:calc(50% - min(88vw,68vh * 1.562) * .28);transform:rotate(-24deg);animation:bolt 1.4s .95s infinite}
.rp-b2{width:60px;height:2px;top:calc(42% - min(88vw / 1.562, 68vh) * .52 - 8px);left:calc(50% + min(88vw,68vh * 1.562) * .06);transform:rotate(20deg);animation:bolt 1.6s 1.55s infinite}
.rp-b3{width:50px;height:2px;top:calc(42% - 18px);left:calc(50% - min(88vw,68vh * 1.562) * .44);transform:rotate(-10deg);animation:bolt 1.5s 1.15s infinite}
.rp-b4{width:65px;height:2px;top:calc(42% - 12px);left:calc(50% + min(88vw,68vh * 1.562) * .26);transform:rotate(14deg);animation:bolt 1.8s 1.75s infinite}
@keyframes bolt{0%,100%{opacity:0}2%{opacity:1}5%{opacity:.6}8%{opacity:1}12%{opacity:0}}
/* Cloud wisps */
.rp-wisp{position:absolute;left:6%;width:88%;height:18px;border-radius:9px;background:rgba(57,255,20,.07);opacity:0;z-index:15}
.rp-w1{top:calc(42% + min(88vw / 1.562, 68vh) * .04);animation:wisp 3.8s 1.0s infinite}
.rp-w2{top:calc(42% + min(88vw / 1.562, 68vh) * .14);animation:wisp 3.8s 1.6s infinite reverse}
.rp-w3{top:calc(42% + min(88vw / 1.562, 68vh) * .23);animation:wisp 3.8s 2.3s infinite}
@keyframes wisp{0%{opacity:0;transform:translateX(-12%)}12%{opacity:.22}88%{opacity:.22}100%{opacity:0;transform:translateX(12%)}}
/* HUD */
.rp-hud{position:absolute;bottom:6%;left:0;right:0;display:flex;flex-direction:column;align-items:center;gap:10px;opacity:0;animation:hudIn .6s .9s forwards}
@keyframes hudIn{to{opacity:1}}
.rp-brand{display:flex;gap:3px}
.rp-ch{font-family:system-ui,-apple-system,sans-serif;font-size:clamp(14px,4vw,22px);font-weight:900;color:#fff;letter-spacing:3px;opacity:0;transform:scale(.4);animation:chPop .28s forwards;text-shadow:0 0 18px rgba(57,255,20,.7)}
@keyframes chPop{to{opacity:1;transform:scale(1)}}
.rp-tag{font-family:system-ui,-apple-system,sans-serif;font-size:clamp(7px,2vw,9px);font-weight:600;color:#39FF14;letter-spacing:2.5px;text-transform:uppercase;opacity:.85}
.rp-prog-track{width:48vw;max-width:240px;height:2px;border-radius:1px;background:rgba(57,255,20,.2);overflow:hidden}
.rp-prog-fill{height:2px;background:#39FF14;box-shadow:0 0 8px 2px #39FF14;animation:prog 3s .9s ease-out forwards}
@keyframes prog{0%{width:0}60%{width:70%}80%{width:85%}100%{width:92%}}
</style>
<script>
(function(){
  /* React will call window.__rpHideLoader() when it mounts its own screen.
     Fallback: auto-hide after 10s in case React fails to signal. */
  var e=document.getElementById('rp-loading-screen');
  function hide(){if(!e)return;e.style.opacity='0';setTimeout(function(){if(e&&e.parentNode)e.parentNode.removeChild(e);e=null;},500);}
  window.__rpHideLoader=hide;
  setTimeout(hide,10000);
})();
</script>`;

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
    html = html.replace('</body>', `${LOADING_SCREEN}\n</body>`);
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
    // Cache-bust: redirect bare / to /?_v=BUILD_TS so browsers always fetch fresh HTML
    if (pathname === '/' && !req.url.includes('_v=')) {
      res.writeHead(302, { 'Location': `/?_v=${BUILD_TS}`, 'Cache-Control': 'no-store' });
      return res.end();
    }

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
  <p>We use Square for payment processing. Square's privacy policy governs any payment data you provide during checkout. We do not store your payment card details.</p>

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
