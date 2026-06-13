/**
 * ReversePicks production/dev proxy — zero npm dependencies.
 * Uses only Node.js built-in modules so it starts instantly on any deploy
 * without requiring `npm install` first.
 *
 * Production:  serves dist/ static files + proxies /api/* to FastAPI :8000
 * Development: proxies everything to Expo Metro :5001 (or serves dist/ if built)
 */
const http  = require('http');
const path  = require('path');
const fs    = require('fs');
const url   = require('url');

// ── Global safety net ──────────────────────────────────────────────────────
process.on('uncaughtException',  (err)    => console.error('[Proxy] Uncaught exception (survived):', err.message));
process.on('unhandledRejection', (reason) => console.error('[Proxy] Unhandled rejection (survived):', reason));

const IS_PRODUCTION = process.env.PRODUCTION === 'true';
const PORT         = 5000;
const BACKEND_PORT = 8000;
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
    <div id="rp-loading-screen" style="position:fixed;top:0;left:0;right:0;bottom:0;background:#050505;display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:99999;transition:opacity 0.3s ease-out">
      <div style="width:80px;height:80px;border:3px solid #39FF14;border-radius:50%;display:flex;align-items:center;justify-content:center;margin-bottom:24px;animation:rpPulse 2s ease-in-out infinite">
        <span style="color:#39FF14;font-family:system-ui,-apple-system,sans-serif;font-size:28px;font-weight:800;letter-spacing:-1px">RP</span>
      </div>
      <div style="color:#888;font-family:system-ui,-apple-system,sans-serif;font-size:14px;letter-spacing:2px;text-transform:uppercase">Loading...</div>
    </div>
    <style>@keyframes rpPulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.05);opacity:.8}}</style>
    <script>(function(){var h=function(){var e=document.getElementById('rp-loading-screen');if(e){e.style.opacity='0';setTimeout(function(){e.style.display='none'},300);}};window.addEventListener('load',h);setTimeout(h,8000);})();</script>`;

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
